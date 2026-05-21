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
import com.google.cloud.bigquery.InsertAllRequest;
import com.google.cloud.bigquery.LegacySQLTypeName;
import com.google.cloud.bigquery.Schema;
import com.google.cloud.bigquery.StandardTableDefinition;
import com.google.cloud.bigquery.TableId;
import com.google.cloud.bigquery.TableInfo;
import com.google.cloud.bigquery.storage.v1.BigQueryReadClient;
import com.google.cloud.bigquery.storage.v1.BigQueryReadSettings;
import com.google.cloud.bigquery.storage.v1.CreateReadSessionRequest;
import com.google.cloud.bigquery.storage.v1.DataFormat;
import com.google.cloud.bigquery.storage.v1.ReadRowsRequest;
import com.google.cloud.bigquery.storage.v1.ReadRowsResponse;
import com.google.cloud.bigquery.storage.v1.ReadSession;
import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import org.apache.arrow.memory.BufferAllocator;
import org.apache.arrow.memory.RootAllocator;
import org.apache.arrow.vector.ipc.ArrowStreamReader;
import org.apache.arrow.vector.VectorSchemaRoot;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayInputStream;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * E2E: Phase 4 Storage Read API via the google-cloud-bigquerystorage
 * Java client. Mirrors the Python and Go storage_read ship-criterion: seed
 * three rows via REST, open a session over gRPC, stream ReadRows,
 * and assert all three rows round-trip via Arrow IPC decoding.
 */
class StorageReadTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String GRPC_ENDPOINT = System.getenv("BQEMU_GRPC_ENDPOINT") != null
            ? System.getenv("BQEMU_GRPC_ENDPOINT")
            : "localhost:9060";
    private static final String PROJECT = "e2e-java-storage_read";
    private static final String DATASET = "storage_read_java_ds";
    private static final String TABLE = "places";

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
                Field.newBuilder("name", LegacySQLTypeName.STRING).build(),
                Field.newBuilder("score", LegacySQLTypeName.INTEGER).build());
        bqClient.create(TableInfo.of(
                TableId.of(PROJECT, DATASET, TABLE),
                StandardTableDefinition.of(schema)));

        InsertAllRequest.Builder ins = InsertAllRequest.newBuilder(
                TableId.of(PROJECT, DATASET, TABLE));
        for (Object[] row : new Object[][]{
                {1L, "Alice", 90L},
                {2L, "Bob", 70L},
                {3L, "Carol", 85L},
        }) {
            Map<String, Object> m = new HashMap<>();
            m.put("id", row[0]);
            m.put("name", row[1]);
            m.put("score", row[2]);
            ins.addRow(m);
        }
        bqClient.insertAll(ins.build());
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
    void createsReadSessionAndStreamsArrowBatches() throws Exception {
        ManagedChannel channel = ManagedChannelBuilder.forTarget(GRPC_ENDPOINT)
                .usePlaintext()
                .build();
        try {
            BigQueryReadSettings settings = BigQueryReadSettings.newBuilder()
                    .setCredentialsProvider(NoCredentialsProvider.create())
                    .setTransportChannelProvider(FixedTransportChannelProvider.create(
                            GrpcTransportChannel.create(channel)))
                    .build();
            try (BigQueryReadClient readClient = BigQueryReadClient.create(settings)) {
                ReadSession session = readClient.createReadSession(
                        CreateReadSessionRequest.newBuilder()
                                .setParent("projects/" + PROJECT)
                                .setReadSession(ReadSession.newBuilder()
                                        .setTable("projects/" + PROJECT
                                                + "/datasets/" + DATASET
                                                + "/tables/" + TABLE)
                                        .setDataFormat(DataFormat.ARROW)
                                        .build())
                                .setMaxStreamCount(1)
                                .build());
                assertTrue(session.getStreamsCount() >= 1,
                        "expected at least one stream");

                int total = 0;
                try (BufferAllocator allocator = new RootAllocator()) {
                    for (int i = 0; i < session.getStreamsCount(); i++) {
                        String streamName = session.getStreams(i).getName();
                        for (ReadRowsResponse resp : readClient.readRowsCallable().call(
                                ReadRowsRequest.newBuilder()
                                        .setReadStream(streamName)
                                        .build())) {
                            byte[] batch = resp.getArrowRecordBatch()
                                    .getSerializedRecordBatch()
                                    .toByteArray();
                            if (batch.length == 0) {
                                continue;
                            }
                            // The emulator serializes each batch as a
                            // self-contained Arrow IPC stream (schema
                            // header + record batch), so the reader
                            // parses the batch payload directly.
                            try (ArrowStreamReader reader = new ArrowStreamReader(
                                    new ByteArrayInputStream(batch), allocator)) {
                                while (reader.loadNextBatch()) {
                                    VectorSchemaRoot root = reader.getVectorSchemaRoot();
                                    total += root.getRowCount();
                                }
                            }
                        }
                    }
                }
                assertEquals(3, total, "expected 3 rows from ReadRows");
            }
        } finally {
            channel.shutdownNow();
        }
    }
}
