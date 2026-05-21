package com.example;

import com.google.api.gax.core.NoCredentialsProvider;
import com.google.api.gax.grpc.GrpcTransportChannel;
import com.google.api.gax.rpc.FixedTransportChannelProvider;
import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.BigQueryOptions;
import com.google.cloud.bigquery.DatasetId;
import com.google.cloud.bigquery.DatasetInfo;
import com.google.cloud.bigquery.Field;
import com.google.cloud.bigquery.LegacySQLTypeName;
import com.google.cloud.bigquery.Schema;
import com.google.cloud.bigquery.StandardTableDefinition;
import com.google.cloud.bigquery.TableId;
import com.google.cloud.bigquery.TableInfo;
import com.google.cloud.bigquery.storage.v1.AppendRowsRequest;
import com.google.cloud.bigquery.storage.v1.AppendRowsResponse;
import com.google.cloud.bigquery.storage.v1.BatchCommitWriteStreamsRequest;
import com.google.cloud.bigquery.storage.v1.BatchCommitWriteStreamsResponse;
import com.google.cloud.bigquery.storage.v1.BigQueryWriteClient;
import com.google.cloud.bigquery.storage.v1.BigQueryWriteSettings;
import com.google.cloud.bigquery.storage.v1.CreateWriteStreamRequest;
import com.google.cloud.bigquery.storage.v1.FinalizeWriteStreamRequest;
import com.google.cloud.bigquery.storage.v1.ProtoRows;
import com.google.cloud.bigquery.storage.v1.ProtoSchema;
import com.google.cloud.bigquery.storage.v1.TableName;
import com.google.cloud.bigquery.storage.v1.WriteStream;
import com.google.protobuf.ByteString;
import com.google.protobuf.DescriptorProtos;
import com.google.protobuf.DescriptorProtos.DescriptorProto;
import com.google.protobuf.DescriptorProtos.FieldDescriptorProto;
import com.google.protobuf.DescriptorProtos.FieldDescriptorProto.Label;
import com.google.protobuf.DescriptorProtos.FieldDescriptorProto.Type;
import com.google.protobuf.Descriptors;
import com.google.protobuf.DynamicMessage;
import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * E2E: Phase 5 Storage Write API via the google-cloud-bigquerystorage
 * Java client. Covers CreateWriteStream + AppendRows on a COMMITTED
 * stream using dynamic protobuf rows.
 */
class StorageWriteTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String GRPC_ENDPOINT = System.getenv("BQEMU_GRPC_ENDPOINT") != null
            ? System.getenv("BQEMU_GRPC_ENDPOINT")
            : "localhost:9060";
    private static final String PROJECT = "e2e-java";
    private static final String DATASET = "e2e_java_write";
    private static final String TABLE = "write_target";

    private static BigQuery bqClient;

    @BeforeAll
    static void setUp() {
        bqClient = BigQueryOptions.newBuilder()
                .setProjectId(PROJECT)
                .setHost(REST_URL)
                .setCredentials(NoCredentials.getInstance())
                .build()
                .getService();
        try {
            bqClient.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
            // absent
        }
        bqClient.create(DatasetInfo.newBuilder(DATASET).setLocation("US").build());

        Schema schema = Schema.of(
                Field.newBuilder("id", LegacySQLTypeName.INTEGER)
                        .setMode(Field.Mode.REQUIRED).build(),
                Field.newBuilder("name", LegacySQLTypeName.STRING).build()
        );
        bqClient.create(TableInfo.of(
                TableId.of(PROJECT, DATASET, TABLE),
                StandardTableDefinition.of(schema)));
    }

    @AfterAll
    static void tearDown() {
        try {
            bqClient.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
            // best-effort
        }
    }

    @Test
    void committedStreamAcceptsProtoRows() throws Exception {
        ManagedChannel channel = ManagedChannelBuilder.forTarget(GRPC_ENDPOINT)
                .usePlaintext()
                .build();
        try {
            BigQueryWriteSettings settings = BigQueryWriteSettings.newBuilder()
                    .setCredentialsProvider(NoCredentialsProvider.create())
                    .setTransportChannelProvider(FixedTransportChannelProvider.create(
                            GrpcTransportChannel.create(channel)))
                    .build();
            try (BigQueryWriteClient writeClient = BigQueryWriteClient.create(settings)) {
                // 1. Create COMMITTED stream.
                TableName parent = TableName.of(PROJECT, DATASET, TABLE);
                WriteStream stream = writeClient.createWriteStream(
                        CreateWriteStreamRequest.newBuilder()
                                .setParent(parent.toString())
                                .setWriteStream(WriteStream.newBuilder()
                                        .setType(WriteStream.Type.COMMITTED)
                                        .build())
                                .build());
                assertTrue(stream.getName().contains("/streams/"));

                // 2. Build dynamic proto schema {id:int64, name:string}.
                DescriptorProto descriptorProto = DescriptorProto.newBuilder()
                        .setName("Row")
                        .addField(FieldDescriptorProto.newBuilder()
                                .setName("id")
                                .setNumber(1)
                                .setType(Type.TYPE_INT64)
                                .setLabel(Label.LABEL_OPTIONAL)
                                .build())
                        .addField(FieldDescriptorProto.newBuilder()
                                .setName("name")
                                .setNumber(2)
                                .setType(Type.TYPE_STRING)
                                .setLabel(Label.LABEL_OPTIONAL)
                                .build())
                        .build();
                DescriptorProtos.FileDescriptorProto fileProto =
                        DescriptorProtos.FileDescriptorProto.newBuilder()
                                .setName("row.proto")
                                .setSyntax("proto2")
                                .addMessageType(descriptorProto)
                                .build();
                Descriptors.FileDescriptor fileDesc = Descriptors.FileDescriptor.buildFrom(
                        fileProto, new Descriptors.FileDescriptor[]{});
                Descriptors.Descriptor rowDesc = fileDesc.getMessageTypes().get(0);

                DynamicMessage row1 = DynamicMessage.newBuilder(rowDesc)
                        .setField(rowDesc.findFieldByName("id"), 1L)
                        .setField(rowDesc.findFieldByName("name"), "alice")
                        .build();
                DynamicMessage row2 = DynamicMessage.newBuilder(rowDesc)
                        .setField(rowDesc.findFieldByName("id"), 2L)
                        .setField(rowDesc.findFieldByName("name"), "bob")
                        .build();

                // 3. Send AppendRows on a bidi stream.
                AppendRowsStreamCollector collector = new AppendRowsStreamCollector();
                var appendStream = writeClient.appendRowsCallable().splitCall(collector);
                try {
                    AppendRowsRequest request = AppendRowsRequest.newBuilder()
                            .setWriteStream(stream.getName())
                            .setProtoRows(AppendRowsRequest.ProtoData.newBuilder()
                                    .setWriterSchema(ProtoSchema.newBuilder()
                                            .setProtoDescriptor(descriptorProto)
                                            .build())
                                    .setRows(ProtoRows.newBuilder()
                                            .addSerializedRows(row1.toByteString())
                                            .addSerializedRows(row2.toByteString())
                                            .build())
                                    .build())
                            .setOffset(com.google.protobuf.Int64Value.of(0L))
                            .build();
                    appendStream.send(request);
                    appendStream.closeSend();
                    collector.awaitDone(15, TimeUnit.SECONDS);
                    assertEquals(1, collector.responses.size());
                    AppendRowsResponse response = collector.responses.get(0);
                    assertEquals(0, response.getError().getCode(),
                            () -> "AppendRows error: " + response.getError().getMessage());
                } finally {
                    // Already closed via closeSend.
                }

                // 4. Verify rows became visible via REST tabledata.list.
                assertEquals(2, countRows());
            }
        } finally {
            channel.shutdownNow();
        }
    }

    @Test
    void pendingStreamIsInvisibleUntilBatchCommit() throws Exception {
        // Use a separate table to avoid coupling with the COMMITTED-stream
        // test's row count — JUnit @BeforeAll seeds the class-level table
        // once and tests share state otherwise.
        String pendingTable = "write_target_pending";
        Schema schema = Schema.of(
                Field.newBuilder("id", LegacySQLTypeName.INTEGER)
                        .setMode(Field.Mode.REQUIRED).build(),
                Field.newBuilder("name", LegacySQLTypeName.STRING).build());
        bqClient.create(TableInfo.of(
                TableId.of(PROJECT, DATASET, pendingTable),
                StandardTableDefinition.of(schema)));

        ManagedChannel channel = ManagedChannelBuilder.forTarget(GRPC_ENDPOINT)
                .usePlaintext()
                .build();
        try {
            BigQueryWriteSettings settings = BigQueryWriteSettings.newBuilder()
                    .setCredentialsProvider(NoCredentialsProvider.create())
                    .setTransportChannelProvider(FixedTransportChannelProvider.create(
                            GrpcTransportChannel.create(channel)))
                    .build();
            try (BigQueryWriteClient writeClient = BigQueryWriteClient.create(settings)) {
                TableName parent = TableName.of(PROJECT, DATASET, pendingTable);
                WriteStream stream = writeClient.createWriteStream(
                        CreateWriteStreamRequest.newBuilder()
                                .setParent(parent.toString())
                                .setWriteStream(WriteStream.newBuilder()
                                        .setType(WriteStream.Type.PENDING)
                                        .build())
                                .build());
                assertTrue(stream.getName().contains("/streams/"));

                // Same dynamic descriptor as the COMMITTED test.
                DescriptorProto descriptorProto = DescriptorProto.newBuilder()
                        .setName("Row")
                        .addField(FieldDescriptorProto.newBuilder()
                                .setName("id")
                                .setNumber(1)
                                .setType(Type.TYPE_INT64)
                                .setLabel(Label.LABEL_OPTIONAL)
                                .build())
                        .addField(FieldDescriptorProto.newBuilder()
                                .setName("name")
                                .setNumber(2)
                                .setType(Type.TYPE_STRING)
                                .setLabel(Label.LABEL_OPTIONAL)
                                .build())
                        .build();
                DescriptorProtos.FileDescriptorProto fileProto =
                        DescriptorProtos.FileDescriptorProto.newBuilder()
                                .setName("row.proto")
                                .setSyntax("proto2")
                                .addMessageType(descriptorProto)
                                .build();
                Descriptors.FileDescriptor fileDesc = Descriptors.FileDescriptor.buildFrom(
                        fileProto, new Descriptors.FileDescriptor[]{});
                Descriptors.Descriptor rowDesc = fileDesc.getMessageTypes().get(0);

                DynamicMessage row1 = DynamicMessage.newBuilder(rowDesc)
                        .setField(rowDesc.findFieldByName("id"), 10L)
                        .setField(rowDesc.findFieldByName("name"), "pending-a")
                        .build();
                DynamicMessage row2 = DynamicMessage.newBuilder(rowDesc)
                        .setField(rowDesc.findFieldByName("id"), 20L)
                        .setField(rowDesc.findFieldByName("name"), "pending-b")
                        .build();

                AppendRowsStreamCollector collector = new AppendRowsStreamCollector();
                var appendStream = writeClient.appendRowsCallable().splitCall(collector);
                try {
                    appendStream.send(AppendRowsRequest.newBuilder()
                            .setWriteStream(stream.getName())
                            .setProtoRows(AppendRowsRequest.ProtoData.newBuilder()
                                    .setWriterSchema(ProtoSchema.newBuilder()
                                            .setProtoDescriptor(descriptorProto)
                                            .build())
                                    .setRows(ProtoRows.newBuilder()
                                            .addSerializedRows(row1.toByteString())
                                            .addSerializedRows(row2.toByteString())
                                            .build())
                                    .build())
                            .setOffset(com.google.protobuf.Int64Value.of(0L))
                            .build());
                    appendStream.closeSend();
                    collector.awaitDone(15, TimeUnit.SECONDS);
                    assertEquals(0, collector.responses.get(0).getError().getCode());
                } finally {
                    // closeSend already called
                }

                // Rows are not visible pre-commit.
                assertEquals(0, countRowsIn(pendingTable));

                writeClient.finalizeWriteStream(FinalizeWriteStreamRequest.newBuilder()
                        .setName(stream.getName())
                        .build());

                BatchCommitWriteStreamsResponse commitResp = writeClient.batchCommitWriteStreams(
                        BatchCommitWriteStreamsRequest.newBuilder()
                                .setParent(String.format(
                                        "projects/%s/datasets/%s", PROJECT, DATASET))
                                .addWriteStreams(stream.getName())
                                .build());
                assertEquals(0, commitResp.getStreamErrorsCount(),
                        () -> "BatchCommit stream errors: " + commitResp.getStreamErrorsList());

                assertEquals(2, countRowsIn(pendingTable));
            }
        } finally {
            channel.shutdownNow();
        }
    }

    private int countRowsIn(String table) throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        String url = String.format(
                "%s/bigquery/v2/projects/%s/datasets/%s/tables/%s/data",
                REST_URL, PROJECT, DATASET, table);
        HttpResponse<String> resp = client.send(
                HttpRequest.newBuilder().uri(URI.create(url)).GET().build(),
                HttpResponse.BodyHandlers.ofString());
        Matcher m = Pattern.compile("\"totalRows\"\\s*:\\s*\"(\\d+)\"").matcher(resp.body());
        if (m.find()) {
            return Integer.parseInt(m.group(1));
        }
        return 0;
    }

    private int countRows() throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        String url = String.format(
                "%s/bigquery/v2/projects/%s/datasets/%s/tables/%s/data",
                REST_URL, PROJECT, DATASET, TABLE);
        HttpResponse<String> resp = client.send(
                HttpRequest.newBuilder().uri(URI.create(url)).GET().build(),
                HttpResponse.BodyHandlers.ofString());
        Matcher m = Pattern.compile("\"totalRows\"\\s*:\\s*\"(\\d+)\"").matcher(resp.body());
        if (m.find()) {
            return Integer.parseInt(m.group(1));
        }
        return 0;
    }

    private static class AppendRowsStreamCollector
            implements com.google.api.gax.rpc.ResponseObserver<AppendRowsResponse> {
        final java.util.List<AppendRowsResponse> responses = new java.util.ArrayList<>();
        private final java.util.concurrent.CountDownLatch done =
                new java.util.concurrent.CountDownLatch(1);

        @Override
        public void onStart(com.google.api.gax.rpc.StreamController controller) {
            // no-op; request-response pairs are 1:1 here.
        }

        @Override
        public void onResponse(AppendRowsResponse response) {
            responses.add(response);
        }

        @Override
        public void onError(Throwable throwable) {
            done.countDown();
        }

        @Override
        public void onComplete() {
            done.countDown();
        }

        void awaitDone(long timeout, TimeUnit unit) throws InterruptedException {
            done.await(timeout, unit);
        }
    }
}
