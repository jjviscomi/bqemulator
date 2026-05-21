package com.example;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * E2E: Phase 10 admin endpoints against the bqemulator via raw HTTP.
 *
 * The four CLI-only commands (import/export/seed/backup/restore) are
 * exercised by the Python E2E + integration suites. This file covers
 * the /admin/* JSON surface against the same live container the rest of
 * the Java E2E suite hits.
 */
class AdminTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String PROJECT = "e2e-java-admin";
    private static final String DATASET = "admin_java_ds";

    @BeforeEach
    void setUp() throws IOException {
        try {
            restDelete("/bigquery/v2/projects/" + PROJECT + "/datasets/" + DATASET
                    + "?deleteContents=true");
        } catch (IOException ignored) {}
        restPost(
                "/bigquery/v2/projects/" + PROJECT + "/datasets",
                "{\"datasetReference\":{\"projectId\":\"" + PROJECT
                        + "\",\"datasetId\":\"" + DATASET + "\"}}");
    }

    @AfterEach
    void tearDown() {
        try {
            restDelete("/bigquery/v2/projects/" + PROJECT + "/datasets/" + DATASET
                    + "?deleteContents=true");
        } catch (IOException ignored) {}
    }

    private int restGet(String path) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) URI.create(REST_URL + path).toURL().openConnection();
        conn.setRequestMethod("GET");
        int code = conn.getResponseCode();
        if (code < 400) {
            try (InputStream in = conn.getInputStream()) {
                in.readAllBytes();
            }
        }
        return code;
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

    private void restDelete(String path) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) URI.create(REST_URL + path).toURL().openConnection();
        conn.setRequestMethod("DELETE");
        conn.getResponseCode();
    }

    @Test
    void testAdminEndpointsAreReachable() throws IOException {
        // /admin/config — 404 means admin disabled; we skip in that case.
        int code = restGet("/admin/config");
        Assumptions.assumeTrue(code != 404,
                "admin disabled in container; covered by unit tests");
        assertEquals(200, code, "admin/config should return 200 when admin enabled");

        assertEquals(200, restGet("/admin/catalog?projectId=" + PROJECT));
        assertEquals(200, restGet("/admin/jobs"));
        assertEquals(200, restGet("/admin/streams"));
    }
}
