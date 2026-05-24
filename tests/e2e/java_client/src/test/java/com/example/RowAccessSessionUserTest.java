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
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * E2E: SESSION_USER() inside a RAP filter predicate (ADR 0038), exercised
 * through the official google-cloud-bigquery Java client.
 *
 * The canonical "tenant isolation by email domain" pattern:
 *   - Seed a tenants table with rows for two domains.
 *   - Create a RAP filter REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') =
 *     tenant_id granted to allAuthenticatedUsers.
 *   - Each caller sees only their own tenant's rows.
 *
 * The X-Bqemu-Caller header is injected per-client via the BigQuery
 * client's FixedHeaderProvider (same pattern as RowAccessTest).
 */
class RowAccessSessionUserTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String PROJECT = "e2e-java-row_access_session_user";
    private static final String DATASET = "session_user_java_ds";

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
        try {
            admin.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
        }
        admin.create(DatasetInfo.newBuilder(DATASET).setLocation("US").build());

        runAdminQuery("CREATE TABLE `" + PROJECT + "." + DATASET + ".tenants` "
                + "(id INT64, tenant_id STRING)");
        runAdminQuery("INSERT INTO `" + PROJECT + "." + DATASET + ".tenants` "
                + "VALUES (1, 'example.com'), (2, 'example.com'), "
                + "(3, 'other.com'), (4, 'other.com')");

        // Defensive: drop a stale RAP before creating, mirroring
        // RowAccessTest.setUp's pattern — DELETE /datasets/X doesn't
        // currently cascade to row access policies.
        restIgnore4xx("DELETE",
                "/bigquery/v2/projects/" + PROJECT + "/datasets/" + DATASET
                        + "/tables/tenants/rowAccessPolicies/tenant_by_session_user",
                null);
        rest("POST",
                "/bigquery/v2/projects/" + PROJECT + "/datasets/" + DATASET
                        + "/tables/tenants/rowAccessPolicies",
                "{\"rowAccessPolicyReference\":{\"projectId\":\"" + PROJECT
                        + "\",\"datasetId\":\"" + DATASET
                        + "\",\"tableId\":\"tenants\",\"policyId\":\"tenant_by_session_user\"},"
                        + "\"filterPredicate\":"
                        + "\"REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = tenant_id\","
                        + "\"grantees\":[\"allAuthenticatedUsers\"]}");
    }

    @AfterEach
    void tearDown() {
        try {
            admin.delete(DatasetId.of(PROJECT, DATASET),
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

    // Connect-side timeout on the client, per-request timeout via the
    // HttpRequest builder. The 15s cap matches the per-call SLA the
    // rest of the e2e suite assumes; without it, a stalled emulator
    // can hang the test run indefinitely
    // (CodeRabbit thread PRRT_kwDOSkfuJ86EVwO9).
    private static final Duration REST_TIMEOUT = Duration.ofSeconds(15);
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(REST_TIMEOUT)
            .build();

    private void rest(String method, String path, String body) throws Exception {
        URI uri = URI.create(REST_URL + path);
        HttpRequest.BodyPublisher publisher = body == null
                ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofString(body);
        HttpRequest req = HttpRequest.newBuilder(uri)
                .timeout(REST_TIMEOUT)
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

    private void restIgnore4xx(String method, String path, String body) throws Exception {
        URI uri = URI.create(REST_URL + path);
        HttpRequest.BodyPublisher publisher = body == null
                ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofString(body);
        HttpRequest req = HttpRequest.newBuilder(uri)
                .timeout(REST_TIMEOUT)
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
    void exampleComCallerSeesOnlyExampleComRows() throws Exception {
        BigQuery bq = clientFor("user:alice@example.com");
        TableResult res = runAs(bq,
                "SELECT id FROM `" + PROJECT + "." + DATASET + ".tenants` ORDER BY id");
        List<Long> ids = new ArrayList<>();
        for (FieldValueList row : res.iterateAll()) {
            ids.add(row.get("id").getLongValue());
        }
        assertEquals(java.util.Arrays.asList(1L, 2L), ids);
    }

    @Test
    void otherComCallerSeesOnlyOtherComRows() throws Exception {
        BigQuery bq = clientFor("user:bob@other.com");
        TableResult res = runAs(bq,
                "SELECT id FROM `" + PROJECT + "." + DATASET + ".tenants` ORDER BY id");
        List<Long> ids = new ArrayList<>();
        for (FieldValueList row : res.iterateAll()) {
            ids.add(row.get("id").getLongValue());
        }
        assertEquals(java.util.Arrays.asList(3L, 4L), ids);
    }

    @Test
    void bareSelectSessionUserReturnsBareEmail() throws Exception {
        // Not a RAP test, but uses the same pre-translator substitution
        // path — pinning here keeps both surfaces (RAP filter +
        // free-form query) in one place.
        BigQuery bq = clientFor("user:claire@example.com");
        TableResult res = runAs(bq, "SELECT SESSION_USER() AS who");
        String got = null;
        for (FieldValueList row : res.iterateAll()) {
            got = row.get("who").getStringValue();
            break;
        }
        assertEquals("claire@example.com", got);
    }

    @Test
    void bareSelectCurrentUserReturnsBareEmail() throws Exception {
        // CURRENT_USER() is documented as a co-equal alias for
        // SESSION_USER() in BigQuery's reference (ADR 0040); same
        // caller-identity semantics, same pre-translator substitution.
        BigQuery bq = clientFor("user:dani@example.com");
        TableResult res = runAs(bq, "SELECT CURRENT_USER() AS who");
        String got = null;
        for (FieldValueList row : res.iterateAll()) {
            got = row.get("who").getStringValue();
            break;
        }
        assertEquals("dani@example.com", got);
    }

    @Test
    void bareSelectSessionUserSystemVarReturnsBareEmail() throws Exception {
        // The system-variable spelling @@session.user resolves via the
        // same path as the function form (ADR 0040) — pinned here so a
        // future SQLGlot AST change for @@session.user would surface as
        // a test failure rather than silently producing the
        // ANONYMOUS_CALLER literal.
        BigQuery bq = clientFor("user:eli@example.com");
        TableResult res = runAs(bq, "SELECT @@session.user AS who");
        String got = null;
        for (FieldValueList row : res.iterateAll()) {
            got = row.get("who").getStringValue();
            break;
        }
        assertEquals("eli@example.com", got);
    }
}
