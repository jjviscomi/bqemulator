// Phase 9 GEOGRAPHY / RANGE / INTERVAL E2E via the Go BigQuery
// client, exercised against a live bqemulator container.
//
// Ship criterion: queries using ST_DWITHIN, ST_INTERSECTS,
// RANGE_CONTAINS, and INTERVAL arithmetic return correct results
// against ghcr.io/jjviscomi/bqemulator:dev.

package e2e

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	"cloud.google.com/go/civil"
	"google.golang.org/api/iterator"
	"google.golang.org/api/option"
)

const specialized_typesProject = "e2e-go-specialized_types"

func specialized_typesClient(ctx context.Context, t *testing.T) *bigquery.Client {
	t.Helper()
	client, err := bigquery.NewClient(
		ctx,
		specialized_typesProject,
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func specialized_typesRest(t *testing.T, method, path string, body any) map[string]any {
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
	if resp.StatusCode >= 400 && method != "DELETE" {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("rest %s %s: %d %s", method, path, resp.StatusCode, string(body))
	}
	if resp.StatusCode == 204 {
		return nil
	}
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return out
}

func TestSpecializedTypesShipCriterion(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	client := specialized_typesClient(ctx, t)
	defer client.Close()

	dataset := "specialized_types_ds"
	specialized_typesRest(t, "POST", fmt.Sprintf("/bigquery/v2/projects/%s/datasets", specialized_typesProject), map[string]any{
		"datasetReference": map[string]any{
			"projectId": specialized_typesProject,
			"datasetId": dataset,
		},
	})
	defer specialized_typesRest(t, "DELETE",
		fmt.Sprintf("/bigquery/v2/projects/%s/datasets/%s?deleteContents=true",
			specialized_typesProject, dataset), nil)

	// --- Schema round-trip for GEOGRAPHY / INTERVAL / RANGE ---
	specialized_typesRest(t, "POST",
		fmt.Sprintf("/bigquery/v2/projects/%s/datasets/%s/tables", specialized_typesProject, dataset),
		map[string]any{
			"schema": map[string]any{
				"fields": []map[string]any{
					{"name": "id", "type": "INT64", "mode": "REQUIRED"},
					{"name": "loc", "type": "GEOGRAPHY"},
				},
			},
			"tableReference": map[string]any{
				"projectId": specialized_typesProject,
				"datasetId": dataset,
				"tableId":   "places",
			},
		})

	specialized_typesRest(t, "POST",
		fmt.Sprintf("/bigquery/v2/projects/%s/datasets/%s/tables/places/insertAll",
			specialized_typesProject, dataset),
		map[string]any{
			"rows": []map[string]any{
				{"json": map[string]any{"id": "1", "loc": "POINT(0 0)"}},
				{"json": map[string]any{"id": "2", "loc": "POINT(3 4)"}},
				{"json": map[string]any{"id": "3", "loc": "POINT(10 10)"}},
			},
		})

	// --- ST_DWITHIN ---
	q := client.Query(fmt.Sprintf(
		"SELECT id FROM `%s.%s.places` "+
			"WHERE ST_DWITHIN(loc, ST_GEOGFROMTEXT('POINT(0 0)'), 600000) "+
			"ORDER BY id",
		specialized_typesProject, dataset))
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("ST_DWITHIN read: %v", err)
	}
	var ids []int64
	for {
		var row struct{ ID int64 }
		err := it.Next(&row)
		if err == iterator.Done {
			break
		}
		if err != nil {
			t.Fatalf("iter: %v", err)
		}
		ids = append(ids, row.ID)
	}
	if got, want := fmt.Sprint(ids), "[1 2]"; got != want {
		t.Errorf("ST_DWITHIN ids: got %s want %s", got, want)
	}

	// --- RANGE_CONTAINS ---
	q = client.Query(
		"SELECT RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), DATE '2024-06-15') AS mid")
	it, err = q.Read(ctx)
	if err != nil {
		t.Fatalf("RANGE_CONTAINS read: %v", err)
	}
	var rc struct{ Mid bool }
	if err := it.Next(&rc); err != nil {
		t.Fatalf("RANGE_CONTAINS next: %v", err)
	}
	if !rc.Mid {
		t.Errorf("RANGE_CONTAINS expected true")
	}

	// --- INTERVAL arithmetic ---
	// ``DATE + INTERVAL N DAY`` returns DATETIME (not DATE) in the
	// emulator's current type-promotion model, matching the
	// permissive shape the Python E2E asserts on.
	q = client.Query("SELECT DATE '2024-01-15' + INTERVAL 1 DAY AS d")
	it, err = q.Read(ctx)
	if err != nil {
		t.Fatalf("INTERVAL read: %v", err)
	}
	var iv struct{ D civil.DateTime }
	if err := it.Next(&iv); err != nil {
		t.Fatalf("INTERVAL next: %v", err)
	}
	if iv.D.Date.Year != 2024 || iv.D.Date.Month != time.January || iv.D.Date.Day != 16 {
		t.Errorf("INTERVAL: got %v want 2024-01-16", iv.D)
	}
}
