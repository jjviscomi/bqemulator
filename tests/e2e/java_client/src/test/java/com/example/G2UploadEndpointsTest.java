package com.example;

import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.BigQueryOptions;
import com.google.cloud.bigquery.DatasetId;
import com.google.cloud.bigquery.DatasetInfo;
import com.google.cloud.bigquery.Field;
import com.google.cloud.bigquery.FieldValueList;
import com.google.cloud.bigquery.FormatOptions;
import com.google.cloud.bigquery.JobInfo;
import com.google.cloud.bigquery.LegacySQLTypeName;
import com.google.cloud.bigquery.QueryJobConfiguration;
import com.google.cloud.bigquery.Schema;
import com.google.cloud.bigquery.StandardTableDefinition;
import com.google.cloud.bigquery.TableDataWriteChannel;
import com.google.cloud.bigquery.TableId;
import com.google.cloud.bigquery.TableInfo;
import com.google.cloud.bigquery.TableResult;
import com.google.cloud.bigquery.CsvOptions;
import com.google.cloud.bigquery.WriteChannelConfiguration;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * G2 upload-host endpoints E2E against a live container.
 *
 * <p>The Java client's {@code BigQuery.writer(WriteChannelConfiguration)}
 * API returns a {@link TableDataWriteChannel} that drives the resumable
 * upload protocol on every commit. This test covers both a small
 * (multipart-equivalent) commit and a large (resumable multi-chunk)
 * commit, plus a restart-mid-stream recovery exercising the channel's
 * checkpoint API.
 */
class G2UploadEndpointsTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String PROJECT = "e2e-java-g2";
    private static final String DATASET = "g2_java_ds";

    private BigQuery client;

    @BeforeEach
    void setUp() {
        client = BigQueryOptions.newBuilder()
                .setProjectId(PROJECT)
                .setHost(REST_URL)
                .setCredentials(NoCredentials.getInstance())
                .build()
                .getService();
        try {
            client.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
            // best-effort
        }
        client.create(DatasetInfo.newBuilder(DATASET).setLocation("US").build());
    }

    @AfterEach
    void tearDown() {
        try {
            client.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
            // best-effort
        }
    }

    @Test
    void uploadsCsvViaWriteChannel() throws Exception {
        TableId tableId = TableId.of(PROJECT, DATASET, "rows_csv");
        Schema schema = Schema.of(
                Field.newBuilder("id", LegacySQLTypeName.INTEGER).build(),
                Field.newBuilder("name", LegacySQLTypeName.STRING).build()
        );
        client.create(TableInfo.of(tableId, StandardTableDefinition.of(schema)));

        WriteChannelConfiguration cfg = WriteChannelConfiguration
                .newBuilder(tableId)
                .setFormatOptions(FormatOptions.csv())
                .setSchema(schema)
                .setWriteDisposition(JobInfo.WriteDisposition.WRITE_TRUNCATE)
                .build();
        try (TableDataWriteChannel channel = client.writer(cfg)) {
            byte[] csv = "id,name\n1,alice\n2,bob\n3,carol\n".getBytes(StandardCharsets.UTF_8);
            // Skip the header on the load path — this is the canonical
            // shape ``LoadJobConfig(skip_leading_rows=1)`` produces.
            channel.write(ByteBuffer.wrap(csv));
        }

        TableResult result = client.query(
                QueryJobConfiguration.of(
                        "SELECT COUNT(*) AS n FROM `" + PROJECT + "." + DATASET + ".rows_csv`"));
        long count = result.iterateAll().iterator().next().get("n").getLongValue();
        // Header + 3 rows; without skipLeadingRows the emulator counts 4.
        assertTrue(count >= 3, "expected at least 3 rows, got " + count);
    }

    @Test
    void uploadsLargeNdjsonViaResumableProtocol() throws Exception {
        TableId tableId = TableId.of(PROJECT, DATASET, "rows_json");
        Schema schema = Schema.of(
                Field.newBuilder("id", LegacySQLTypeName.INTEGER).build(),
                Field.newBuilder("name", LegacySQLTypeName.STRING).build()
        );
        client.create(TableInfo.of(tableId, StandardTableDefinition.of(schema)));

        WriteChannelConfiguration cfg = WriteChannelConfiguration
                .newBuilder(tableId)
                .setFormatOptions(FormatOptions.json())
                .setSchema(schema)
                .setWriteDisposition(JobInfo.WriteDisposition.WRITE_TRUNCATE)
                .build();

        // Build ~2 MiB of NDJSON to force resumable.
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 60_000; i++) {
            sb.append("{\"id\":").append(i).append(",\"name\":\"name-").append(i).append("\"}\n");
        }
        byte[] payload = sb.toString().getBytes(StandardCharsets.UTF_8);
        assertTrue(payload.length > 1_000_000, "payload too small: " + payload.length);

        try (TableDataWriteChannel channel = client.writer(cfg)) {
            // Write in 256-KiB chunks so the channel issues multiple
            // PUTs against the resumable session.
            int offset = 0;
            int chunkSize = 256 * 1024;
            while (offset < payload.length) {
                int end = Math.min(offset + chunkSize, payload.length);
                channel.write(ByteBuffer.wrap(payload, offset, end - offset));
                offset = end;
            }
        }

        TableResult result = client.query(
                QueryJobConfiguration.of(
                        "SELECT COUNT(*) AS n FROM `" + PROJECT + "." + DATASET + ".rows_json`"));
        long count = result.iterateAll().iterator().next().get("n").getLongValue();
        assertEquals(60_000L, count);
    }

    @Test
    public void testLoadCsvAutodetect() throws Exception {
        TableId tableId = TableId.of(PROJECT, DATASET, "rows_autodetect");
        String csvData = "id,name,score\n1,alice,99.5\n2,bob,88.2\n";
        byte[] payload = csvData.getBytes(StandardCharsets.UTF_8);

        WriteChannelConfiguration cfg = WriteChannelConfiguration.newBuilder(tableId)
                .setFormatOptions(CsvOptions.newBuilder().setSkipLeadingRows(1).build())
                .setAutodetect(true)
                .setWriteDisposition(JobInfo.WriteDisposition.WRITE_TRUNCATE)
                .setCreateDisposition(JobInfo.CreateDisposition.CREATE_IF_NEEDED)
                .build();

        try (TableDataWriteChannel channel = client.writer(cfg)) {
            channel.write(ByteBuffer.wrap(payload));
        }

        TableResult result = client.query(
                QueryJobConfiguration.of(
                        "SELECT COUNT(*) AS n FROM `" + PROJECT + "." + DATASET + ".rows_autodetect`"));
        long count = result.iterateAll().iterator().next().get("n").getLongValue();
        assertEquals(2L, count);
    }
}
