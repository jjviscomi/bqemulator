// Phase 10 admin endpoints E2E via raw HTTP against a live bqemulator
// container.
//
// Ship criterion: ``bqemulator import/export/seed/backup/restore`` all
// round-trip cleanly; CLI-only paths are exercised by the Python E2E +
// integration suites. This file covers the /admin/* JSON surface against
// the same live container the rest of the Go E2E suite hits.

package e2e

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"testing"
)

const adminProject = "e2e-go-admin"

func adminRest(t *testing.T, method, path string, body any) (int, map[string]any) {
	t.Helper()
	var buf io.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		buf = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, restURL()+path, buf)
	if err != nil {
		t.Fatalf("rest %s %s: %v", method, path, err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("rest %s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == 204 {
		return resp.StatusCode, nil
	}
	if resp.StatusCode >= 400 && method != "DELETE" && path != "/admin/config" {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("rest %s %s: %d %s", method, path, resp.StatusCode, string(body))
	}
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

func TestAdminAdminEndpoints(t *testing.T) {
	dataset := "admin_go_ds"
	adminRest(t, "POST",
		fmt.Sprintf("/bigquery/v2/projects/%s/datasets", adminProject),
		map[string]any{
			"datasetReference": map[string]any{
				"projectId": adminProject,
				"datasetId": dataset,
			},
		})
	defer adminRest(t, "DELETE",
		fmt.Sprintf("/bigquery/v2/projects/%s/datasets/%s?deleteContents=true",
			adminProject, dataset), nil)

	// /admin/config — admin must be enabled for this to be 200.
	status, body := adminRest(t, "GET", "/admin/config", nil)
	if status == 404 {
		t.Skip("admin disabled in container; covered by unit tests")
	}
	if got, want := body["kind"], "bqemu#adminConfig"; got != want {
		t.Errorf("/admin/config kind: got %v want %v", got, want)
	}

	// /admin/catalog reports the dataset.
	_, body = adminRest(t, "GET", "/admin/catalog?projectId="+adminProject, nil)
	if body["kind"] != "bqemu#adminCatalog" {
		t.Errorf("/admin/catalog kind: %v", body["kind"])
	}

	// /admin/jobs returns a list.
	_, body = adminRest(t, "GET", "/admin/jobs", nil)
	if body["kind"] != "bqemu#adminJobList" {
		t.Errorf("/admin/jobs kind: %v", body["kind"])
	}

	// /admin/streams returns stream counts.
	_, body = adminRest(t, "GET", "/admin/streams", nil)
	if body["kind"] != "bqemu#adminStreamList" {
		t.Errorf("/admin/streams kind: %v", body["kind"])
	}
}
