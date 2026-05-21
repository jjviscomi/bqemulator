package com.example;

import org.junit.jupiter.api.Test;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import static org.junit.jupiter.api.Assertions.*;

/**
 * E2E: health endpoints against a live bqemulator container.
 *
 * Run with: BQEMU_REST_URL=http://localhost:9050 mvn test
 */
class HealthTest {
    private static final String REST_URL =
        System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";

    @Test
    void healthzReturnsOk() throws Exception {
        var client = HttpClient.newHttpClient();
        var request = HttpRequest.newBuilder()
            .uri(URI.create(REST_URL + "/healthz"))
            .GET()
            .build();
        var response = client.send(request, HttpResponse.BodyHandlers.ofString());
        assertEquals(200, response.statusCode());
        assertTrue(response.body().contains("\"ok\""));
    }

    @Test
    void readyzReturnsOk() throws Exception {
        var client = HttpClient.newHttpClient();
        var request = HttpRequest.newBuilder()
            .uri(URI.create(REST_URL + "/readyz"))
            .GET()
            .build();
        var response = client.send(request, HttpResponse.BodyHandlers.ofString());
        assertEquals(200, response.statusCode());
    }
}
