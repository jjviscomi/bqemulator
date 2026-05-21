package com.example;

import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.BigQueryOptions;
import com.google.cloud.bigquery.DatasetId;
import com.google.cloud.bigquery.DatasetInfo;
import com.google.cloud.bigquery.FieldValueList;
import com.google.cloud.bigquery.QueryJobConfiguration;
import com.google.cloud.bigquery.TableResult;
import java.io.IOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * E2E: Phase 9 GEOGRAPHY / RANGE / INTERVAL against the bqemulator
 * via the google-cloud-bigquery Java client.
 *
 * Ship criterion: queries using ST_DWITHIN, ST_INTERSECTS,
 * RANGE_CONTAINS, and INTERVAL arithmetic return correct results
 * against ghcr.io/jjviscomi/bqemulator:dev.
 */
class SpecializedTypesTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String PROJECT = "e2e-java-specialized_types";
    private static final String DATASET = "specialized_types_java_ds";

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
        } catch (Exception ignored) {}
        client.create(DatasetInfo.of(DatasetId.of(PROJECT, DATASET)));
    }

    @AfterEach
    void tearDown() {
        try {
            client.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignored) {}
    }

    private void restPost(String path, String body) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) URI.create(REST_URL + path).toURL().openConnection();
        conn.setRequestMethod("POST");
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setDoOutput(true);
        try (OutputStream os = conn.getOutputStream()) {
            os.write(body.getBytes());
        }
        int code = conn.getResponseCode();
        if (code >= 400) {
            throw new IOException("REST POST " + path + " → " + code);
        }
    }

    @Test
    void testShipCriterion() throws Exception {
        // Create GEOGRAPHY table.
        restPost(
                "/bigquery/v2/projects/" + PROJECT + "/datasets/" + DATASET + "/tables",
                "{\"schema\":{\"fields\":["
                        + "{\"name\":\"id\",\"type\":\"INT64\",\"mode\":\"REQUIRED\"},"
                        + "{\"name\":\"loc\",\"type\":\"GEOGRAPHY\"}"
                        + "]},\"tableReference\":{\"projectId\":\"" + PROJECT
                        + "\",\"datasetId\":\"" + DATASET + "\",\"tableId\":\"places\"}}");
        restPost(
                "/bigquery/v2/projects/" + PROJECT + "/datasets/" + DATASET
                        + "/tables/places/insertAll",
                "{\"rows\":["
                        + "{\"json\":{\"id\":\"1\",\"loc\":\"POINT(0 0)\"}},"
                        + "{\"json\":{\"id\":\"2\",\"loc\":\"POINT(3 4)\"}},"
                        + "{\"json\":{\"id\":\"3\",\"loc\":\"POINT(10 10)\"}}"
                        + "]}");

        // ST_DWITHIN.
        TableResult result = client.query(QueryJobConfiguration.of(
                "SELECT id FROM `" + PROJECT + "." + DATASET + ".places` "
                        + "WHERE ST_DWITHIN(loc, ST_GEOGFROMTEXT('POINT(0 0)'), 600000) "
                        + "ORDER BY id"));
        int count = 0;
        for (FieldValueList row : result.iterateAll()) {
            long id = row.get(0).getLongValue();
            assertTrue(id == 1 || id == 2, "unexpected id " + id);
            count++;
        }
        assertEquals(2, count, "ST_DWITHIN should match exactly 2 rows");

        // RANGE_CONTAINS.
        TableResult range = client.query(QueryJobConfiguration.of(
                "SELECT RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), "
                        + "DATE '2024-06-15') AS mid"));
        FieldValueList rangeRow = range.iterateAll().iterator().next();
        assertTrue(rangeRow.get(0).getBooleanValue(),
                "RANGE_CONTAINS expected true for mid date");

        // INTERVAL arithmetic.
        TableResult interval = client.query(QueryJobConfiguration.of(
                "SELECT CAST(DATE '2024-01-15' + INTERVAL 1 DAY AS STRING) AS d_next"));
        FieldValueList intervalRow = interval.iterateAll().iterator().next();
        assertTrue(intervalRow.get(0).getStringValue().startsWith("2024-01-16"),
                "INTERVAL arithmetic should yield 2024-01-16");
    }
}
