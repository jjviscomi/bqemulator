// Package e2e tests the bqemulator via the Go BigQuery client.
//
// Run with: BQEMU_REST_URL=http://localhost:9050 go test ./... -count=1
package e2e

import (
	"encoding/json"
	"net/http"
	"os"
	"testing"
)

func restURL() string {
	if url := os.Getenv("BQEMU_REST_URL"); url != "" {
		return url
	}
	return "http://localhost:9050"
}

// bqAPIBase is the endpoint to pass to option.WithEndpoint. The Google
// BigQuery Go client constructs URLs by relative-resolving against the
// endpoint, so it must include the "/bigquery/v2/" path component or
// every request lands on /projects/... and returns 404.
func bqAPIBase() string {
	return restURL() + "/bigquery/v2/"
}

func TestHealthz(t *testing.T) {
	resp, err := http.Get(restURL() + "/healthz")
	if err != nil {
		t.Fatalf("GET /healthz failed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}
	var body map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode failed: %v", err)
	}
	if body["status"] != "ok" {
		t.Fatalf("expected status ok, got %v", body["status"])
	}
}

func TestReadyz(t *testing.T) {
	resp, err := http.Get(restURL() + "/readyz")
	if err != nil {
		t.Fatalf("GET /readyz failed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}
}
