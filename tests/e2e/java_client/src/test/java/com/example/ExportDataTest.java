package com.example;

import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.BigQueryOptions;
import com.google.cloud.bigquery.DatasetId;
import com.google.cloud.bigquery.DatasetInfo;
import com.google.cloud.bigquery.Field;
import com.google.cloud.bigquery.LegacySQLTypeName;
import com.google.cloud.bigquery.QueryJobConfiguration;
import com.google.cloud.bigquery.Schema;
import com.google.cloud.bigquery.StandardTableDefinition;
import com.google.cloud.bigquery.TableId;
import com.google.cloud.bigquery.TableInfo;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * EXPORT DATA → Cloud Storage (CSV) E2E against a live container.
 *
 * <p>EXPORT DATA runs as a query job: the inner SELECT is materialised
 * and written to the wildcard {@code uri} under
 * {@code BQEMU_GCS_LOCAL_ROOT}. With a single output shard the {@code *}
 * expands to a 12-digit zero-padded counter, so {@code export_java/*.csv}
 * becomes {@code export_java/000000000000.csv}. The Makefile target
 * ({@code test-e2e-java}) bind-mounts a host directory onto
 * {@code /var/lib/bqemu-gcs} and exports its host path as
 * {@code BQEMU_GCS_HOST_ROOT}, so we read the exported file off the mount.
 */
class ExportDataTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String GCS_HOST_ROOT = System.getenv("BQEMU_GCS_HOST_ROOT");
    private static final String PROJECT = "e2e-java-export";
    private static final String DATASET = "export_java_ds";
    private static final String BUCKET = "g1-e2e";

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
    void exportsQueryResultsToCsv() throws Exception {
        assumeTrue(GCS_HOST_ROOT != null && !GCS_HOST_ROOT.isEmpty(),
                "BQEMU_GCS_HOST_ROOT not set (run via `make test-e2e-java`)");

        TableId srcId = TableId.of(PROJECT, DATASET, "src");
        Schema schema = Schema.of(
                Field.newBuilder("id", LegacySQLTypeName.INTEGER)
                        .setMode(Field.Mode.REQUIRED).build(),
                Field.newBuilder("name", LegacySQLTypeName.STRING).build()
        );
        client.create(TableInfo.of(srcId, StandardTableDefinition.of(schema)));
        client.query(QueryJobConfiguration.of(
                "INSERT INTO `" + PROJECT + "." + DATASET + ".src` (id, name) "
                        + "VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma')"));

        client.query(QueryJobConfiguration.of(
                "EXPORT DATA OPTIONS ("
                        + "uri = 'gs://" + BUCKET + "/export_java/*.csv', "
                        + "format = 'CSV', overwrite = true) AS "
                        + "SELECT id, name FROM `" + PROJECT + "." + DATASET + ".src` ORDER BY id"));

        Path shard = Paths.get(GCS_HOST_ROOT, BUCKET, "export_java", "000000000000.csv");
        assertTrue(Files.exists(shard), "expected export shard at " + shard);

        List<String> lines = new ArrayList<>();
        for (String line : Files.readAllLines(shard)) {
            String trimmed = line.replaceAll("\\r$", "");
            if (!trimmed.isEmpty()) {
                lines.add(trimmed);
            }
        }
        assertEquals(List.of("id,name", "1,alpha", "2,beta", "3,gamma"), lines);
    }
}
