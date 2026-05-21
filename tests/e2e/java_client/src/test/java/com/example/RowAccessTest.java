package com.example;

import com.google.api.gax.rpc.FixedHeaderProvider;
import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.BigQueryOptions;
import com.google.cloud.bigquery.DatasetId;
import com.google.cloud.bigquery.DatasetInfo;
import com.google.cloud.bigquery.FieldValueList;
import com.google.cloud.bigquery.QueryJobConfiguration;
import com.google.cloud.bigquery.TableResult;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * E2E: Phase 8 row access policies + authorized views via the
 * google-cloud-bigquery Java client.
 *
 * Exercises the Phase 8 ship criterion:
 *   - A row access policy granting only user:eu-analyst@example.com
 *     rows where region='EU' is enforced.
 *   - Other callers see zero rows.
 *   - An authorized view still enforces RAP (no bypass — see ADR 0018).
 */
class RowAccessTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String PROJECT = "e2e-java-row_access";
    private static final String DATASET = "row_access_java_ds";
    private static final String VIEW_DATASET = "row_access_java_v_ds";

    private BigQuery admin;

    private BigQuery clientFor(String caller) {
        BigQueryOptions.Builder b = BigQueryOptions.newBuilder()
                .setProjectId(PROJECT)
                .setHost(REST_URL)
                .setCredentials(NoCredentials.getInstance());
        if (caller != null) {
            Map<String, String> headers = new HashMap<>();
            headers.put("X-Bqemu-Caller", caller);
            b.setHeaderProvider(FixedHeaderProvider.create(headers));
        }
        return b.build().getService();
    }

    @BeforeEach
    void setUp() throws Exception {
        admin = clientFor(null);
        // Cleanup any leftovers.
        try {
            admin.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
        }
        try {
            admin.delete(DatasetId.of(PROJECT, VIEW_DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
        }
        admin.create(DatasetInfo.newBuilder(DATASET).setLocation("US").build());
        admin.create(DatasetInfo.newBuilder(VIEW_DATASET).setLocation("US").build());

        runAdminQuery("CREATE TABLE `" + PROJECT + "." + DATASET + ".orders` (id INT64, region STRING)");
        runAdminQuery("INSERT INTO `" + PROJECT + "." + DATASET + ".orders` "
                + "VALUES (1, 'EU'), (2, 'EU'), (3, 'US'), (4, 'US')");

        // Authorized view + RAP setup via raw REST.
        rest("POST",
                "/bigquery/v2/projects/" + PROJECT + "/datasets/" + VIEW_DATASET + "/tables",
                "{\"tableReference\":{\"projectId\":\"" + PROJECT + "\",\"datasetId\":\""
                        + VIEW_DATASET + "\",\"tableId\":\"all_orders\"},"
                        + "\"view\":{\"query\":\"SELECT id, region FROM `" + PROJECT
                        + "`." + DATASET + ".orders\"}}");
        rest("PATCH",
                "/bigquery/v2/projects/" + PROJECT + "/datasets/" + DATASET,
                "{\"access\":[{\"view\":{\"projectId\":\"" + PROJECT + "\",\"datasetId\":\""
                        + VIEW_DATASET + "\",\"tableId\":\"all_orders\"}}]}");
        // Drop a stale RAP before re-creating. ``DELETE /datasets/X``
        // does not currently cascade to row access policies (real
        // BigQuery does); without this defensive cleanup the second
        // @BeforeEach setUp hits 409 ALREADY_EXISTS.
        restIgnore4xx("DELETE",
                "/bigquery/v2/projects/" + PROJECT + "/datasets/" + DATASET
                        + "/tables/orders/rowAccessPolicies/eu_only",
                null);
        rest("POST",
                "/bigquery/v2/projects/" + PROJECT + "/datasets/" + DATASET
                        + "/tables/orders/rowAccessPolicies",
                "{\"rowAccessPolicyReference\":{\"projectId\":\"" + PROJECT
                        + "\",\"datasetId\":\"" + DATASET
                        + "\",\"tableId\":\"orders\",\"policyId\":\"eu_only\"},"
                        + "\"filterPredicate\":\"region = 'EU'\","
                        + "\"grantees\":[\"user:eu-analyst@example.com\"]}");
    }

    @AfterEach
    void tearDown() {
        try {
            admin.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
        }
        try {
            admin.delete(DatasetId.of(PROJECT, VIEW_DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
        }
    }

    private TableResult runAdminQuery(String sql) throws Exception {
        return admin.query(QueryJobConfiguration.newBuilder(sql).setUseLegacySql(false).build());
    }

    private TableResult runAs(BigQuery bq, String sql) throws Exception {
        return bq.query(QueryJobConfiguration.newBuilder(sql).setUseLegacySql(false).build());
    }

    // NOTE: java.net.HttpURLConnection rejects PATCH ("Invalid HTTP
    // method"). Use the JDK 11+ HttpClient which supports PATCH.
    // Pin to HTTP/1.1 because Uvicorn (the emulator's ASGI server)
    // doesn't speak HTTP/2 and HttpClient's default upgrade attempt
    // confuses some intermediate proxies / docker port forwards.
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .build();

    private void rest(String method, String path, String body) throws Exception {
        URI uri = URI.create(REST_URL + path);
        HttpRequest.BodyPublisher publisher = body == null
                ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofString(body);
        HttpRequest req = HttpRequest.newBuilder(uri)
                .method(method, publisher)
                .header("Content-Type", "application/json")
                .build();
        HttpResponse<String> resp = HTTP.send(req, HttpResponse.BodyHandlers.ofString());
        int rc = resp.statusCode();
        if (rc >= 400 && rc != 204) {
            throw new IOException(
                    "REST " + method + " " + path + " -> " + rc + ": " + resp.body());
        }
    }

    /** REST call that tolerates 4xx — useful for "delete if exists" cleanup. */
    private void restIgnore4xx(String method, String path, String body) throws Exception {
        URI uri = URI.create(REST_URL + path);
        HttpRequest.BodyPublisher publisher = body == null
                ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofString(body);
        HttpRequest req = HttpRequest.newBuilder(uri)
                .method(method, publisher)
                .header("Content-Type", "application/json")
                .build();
        HttpResponse<String> resp = HTTP.send(req, HttpResponse.BodyHandlers.ofString());
        int rc = resp.statusCode();
        if (rc >= 500) {
            throw new IOException(
                    "REST " + method + " " + path + " -> " + rc + ": " + resp.body());
        }
    }

    @Test
    void euCallerSeesOnlyEuRows() throws Exception {
        BigQuery eu = clientFor("user:eu-analyst@example.com");
        TableResult res = runAs(eu,
                "SELECT id FROM `" + PROJECT + "." + DATASET + ".orders` ORDER BY id");
        List<Long> ids = new ArrayList<>();
        for (FieldValueList row : res.iterateAll()) {
            ids.add(row.get("id").getLongValue());
        }
        assertEquals(java.util.Arrays.asList(1L, 2L), ids);
    }

    @Test
    void otherCallerSeesNoRows() throws Exception {
        BigQuery other = clientFor("user:other@example.com");
        TableResult res = runAs(other,
                "SELECT id FROM `" + PROJECT + "." + DATASET + ".orders`");
        long count = 0;
        for (FieldValueList row : res.iterateAll()) {
            count++;
        }
        assertEquals(0, count);
    }

    @Test
    void authorizedViewStillEnforcesRap() throws Exception {
        // P2.d follow-up #1 (2026-05-18) reversed the ADR 0018
        // authorized-view bypass decision after empirical recording
        // proved real BigQuery enforces row-level security UNIVERSALLY
        // through views. Integration + conformance fixtures updated
        // then; this E2E test caught in P2.d follow-up #2 (2026-05-18).
        BigQuery other = clientFor("user:other@example.com");
        TableResult res = runAs(other,
                "SELECT id FROM `" + PROJECT + "." + VIEW_DATASET + ".all_orders` ORDER BY id");
        List<Long> ids = new ArrayList<>();
        for (FieldValueList row : res.iterateAll()) {
            ids.add(row.get("id").getLongValue());
        }
        assertEquals(java.util.Collections.emptyList(), ids);
    }

    @Test
    void informationSchemaListsPolicy() throws Exception {
        // NOTE: backticking only the project segment matches the canonical
        // BigQuery INFORMATION_SCHEMA syntax. Backticking the full 4-part
        // path is also valid BigQuery, but currently trips a SQLGlot
        // tokenizer crash in the emulator's INFORMATION_SCHEMA expander —
        // tracked as a follow-up, not P1 scope.
        TableResult res = runAdminQuery(
                "SELECT policy_name, table_name FROM `" + PROJECT + "`." + DATASET
                        + ".INFORMATION_SCHEMA.ROW_ACCESS_POLICIES");
        boolean found = false;
        for (FieldValueList row : res.iterateAll()) {
            if ("eu_only".equals(row.get("policy_name").getStringValue())
                    && "orders".equals(row.get("table_name").getStringValue())) {
                found = true;
                break;
            }
        }
        assertTrue(found, "INFORMATION_SCHEMA.ROW_ACCESS_POLICIES should list eu_only");
    }
}
