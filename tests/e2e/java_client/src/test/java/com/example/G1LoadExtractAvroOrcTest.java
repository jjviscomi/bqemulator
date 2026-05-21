package com.example;

import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.BigQueryOptions;
import com.google.cloud.bigquery.DatasetId;
import com.google.cloud.bigquery.DatasetInfo;
import com.google.cloud.bigquery.Field;
import com.google.cloud.bigquery.FieldValueList;
import com.google.cloud.bigquery.LegacySQLTypeName;
import com.google.cloud.bigquery.QueryJobConfiguration;
import com.google.cloud.bigquery.Schema;
import com.google.cloud.bigquery.StandardTableDefinition;
import com.google.cloud.bigquery.TableId;
import com.google.cloud.bigquery.TableInfo;
import com.google.cloud.bigquery.TableResult;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * G1 load Avro + extract Avro + load ORC E2E against a live container.
 *
 * <p>The Java test gets <b>three</b> cases instead of two because ORC is
 * most common in the Hadoop / Hive / Trino ecosystem that Java tends to
 * sit in (AGENTS.md non-negotiable + ADR 0027 §"Capability matrix").
 */
class G1LoadExtractAvroOrcTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String PROJECT = "e2e-java-g1";
    private static final String DATASET = "g1_java_ds";
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
    void loadsAvroFileAgainstLiveContainer() throws Exception {
        TableId tableId = TableId.of(PROJECT, DATASET, "items_avro");
        Schema schema = Schema.of(
                Field.newBuilder("id", LegacySQLTypeName.INTEGER)
                        .setMode(Field.Mode.REQUIRED).build(),
                Field.newBuilder("name", LegacySQLTypeName.STRING).build()
        );
        client.create(TableInfo.of(tableId, StandardTableDefinition.of(schema)));

        postLoadJob("items_avro",
                "gs://" + BUCKET + "/load_avro_basic.avro",
                "AVRO");

        TableResult result = client.query(
                QueryJobConfiguration.of(
                        "SELECT id, name FROM `" + PROJECT + "." + DATASET + ".items_avro` ORDER BY id"));
        List<String> names = new ArrayList<>();
        for (FieldValueList row : result.iterateAll()) {
            names.add(row.get("name").getStringValue());
        }
        assertEquals(List.of("alpha", "beta", "gamma"), names);
    }

    @Test
    void extractsToAvroAgainstLiveContainer() throws Exception {
        // Seed src.
        TableId srcId = TableId.of(PROJECT, DATASET, "rt_src");
        TableId dstId = TableId.of(PROJECT, DATASET, "rt_dst");
        Schema schema = Schema.of(
                Field.newBuilder("id", LegacySQLTypeName.INTEGER)
                        .setMode(Field.Mode.REQUIRED).build(),
                Field.newBuilder("val", LegacySQLTypeName.STRING).build()
        );
        client.create(TableInfo.of(srcId, StandardTableDefinition.of(schema)));
        client.create(TableInfo.of(dstId, StandardTableDefinition.of(schema)));
        client.query(QueryJobConfiguration.of(
                "INSERT INTO `" + PROJECT + "." + DATASET + ".rt_src` (id, val) "
                        + "VALUES (1, 'alpha'), (2, 'beta')"));

        // Extract to Avro.
        postExtractJob("rt_src",
                "gs://" + BUCKET + "/extract_java.avro");

        // Re-load to dst — proves the extracted file is a well-formed Avro.
        postLoadJob("rt_dst",
                "gs://" + BUCKET + "/extract_java.avro",
                "AVRO");

        TableResult result = client.query(QueryJobConfiguration.of(
                "SELECT val FROM `" + PROJECT + "." + DATASET + ".rt_dst` ORDER BY id"));
        List<String> vals = new ArrayList<>();
        for (FieldValueList row : result.iterateAll()) {
            vals.add(row.get("val").getStringValue());
        }
        assertEquals(List.of("alpha", "beta"), vals);
    }

    @Test
    void loadsOrcFileAgainstLiveContainer() throws Exception {
        TableId tableId = TableId.of(PROJECT, DATASET, "items_orc");
        Schema schema = Schema.of(
                Field.newBuilder("id", LegacySQLTypeName.INTEGER)
                        .setMode(Field.Mode.REQUIRED).build(),
                Field.newBuilder("name", LegacySQLTypeName.STRING).build()
        );
        client.create(TableInfo.of(tableId, StandardTableDefinition.of(schema)));

        postLoadJob("items_orc",
                "gs://" + BUCKET + "/load_orc_basic.orc",
                "ORC");

        TableResult result = client.query(
                QueryJobConfiguration.of(
                        "SELECT id, name FROM `" + PROJECT + "." + DATASET + ".items_orc` ORDER BY id"));
        List<Long> ids = new ArrayList<>();
        for (FieldValueList row : result.iterateAll()) {
            ids.add(row.get("id").getLongValue());
        }
        assertEquals(List.of(1L, 2L, 3L), ids);
    }

    private void postLoadJob(String tableId, String sourceUri, String sourceFormat) throws Exception {
        String body = "{"
                + "\"configuration\":{\"load\":{"
                + "\"destinationTable\":{"
                + "\"projectId\":\"" + PROJECT + "\","
                + "\"datasetId\":\"" + DATASET + "\","
                + "\"tableId\":\"" + tableId + "\""
                + "},"
                + "\"sourceUris\":[\"" + sourceUri + "\"],"
                + "\"sourceFormat\":\"" + sourceFormat + "\","
                + "\"writeDisposition\":\"WRITE_TRUNCATE\""
                + "}}}";
        postJob(body);
    }

    private void postExtractJob(String tableId, String destUri) throws Exception {
        String body = "{"
                + "\"configuration\":{\"extract\":{"
                + "\"sourceTable\":{"
                + "\"projectId\":\"" + PROJECT + "\","
                + "\"datasetId\":\"" + DATASET + "\","
                + "\"tableId\":\"" + tableId + "\""
                + "},"
                + "\"destinationUris\":[\"" + destUri + "\"],"
                + "\"destinationFormat\":\"AVRO\""
                + "}}}";
        postJob(body);
    }

    private void postJob(String body) throws IOException, InterruptedException {
        // Force HTTP/1.1 — Java's HttpClient defaults to HTTP/2 and tries
        // an h2c (HTTP/2 cleartext) upgrade against uvicorn, which is
        // HTTP/1.1 only. The upgrade negotiation can land in a state where
        // the body is sent on the rejected h2 attempt and the fall-back
        // HTTP/1.1 request arrives empty — producing a 400 from the
        // defensive JSONDecodeError handler (was a 500 before the
        // 2026-05-21 audit). Pinning HTTP/1.1 avoids the negotiation
        // entirely. The published Linux image and the testcontainers
        // wrapper both speak HTTP/1.1 only.
        HttpClient httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(REST_URL + "/bigquery/v2/projects/" + PROJECT + "/jobs"))
                .header("Content-Type", "application/json")
                .timeout(Duration.ofSeconds(30))
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
        assertTrue(resp.statusCode() >= 200 && resp.statusCode() < 300,
                "POST jobs returned " + resp.statusCode() + ": " + resp.body());
    }
}
