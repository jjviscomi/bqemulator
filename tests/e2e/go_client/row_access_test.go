// Phase 8 row access policies + authorized views E2E for the
// bqemulator via the Google Go BigQuery client.
//
// Exercises the Phase 8 ship criterion:
//   - A row access policy granting only user:eu-analyst@example.com
//     rows where region='EU' is enforced.
//   - Other callers see zero rows.
//   - An authorized view still enforces RAP (no bypass — see ADR 0018).
//
// The X-Bqemu-Caller header is injected via a custom http.RoundTripper
// passed to option.WithHTTPClient (see ADR 0018).

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
	"google.golang.org/api/iterator"
	"google.golang.org/api/option"
)

const row_accessProject = "e2e-go-row_access"

type callerHeaderTransport struct {
	caller string
	inner  http.RoundTripper
}

func (t *callerHeaderTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if t.caller != "" {
		req.Header.Set("X-Bqemu-Caller", t.caller)
	}
	return t.inner.RoundTrip(req)
}

func row_accessClient(ctx context.Context, t *testing.T, caller string) *bigquery.Client {
	t.Helper()
	httpClient := &http.Client{
		Transport: &callerHeaderTransport{caller: caller, inner: http.DefaultTransport},
	}
	client, err := bigquery.NewClient(
		ctx,
		row_accessProject,
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
		option.WithHTTPClient(httpClient),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func row_accessRest(t *testing.T, method, path string, body any) {
	t.Helper()
	var rd io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("marshal: %v", err)
		}
		rd = bytes.NewReader(buf)
	}
	req, err := http.NewRequest(method, restURL()+path, rd)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("rest %s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 && resp.StatusCode != http.StatusNoContent {
		b, _ := io.ReadAll(resp.Body)
		t.Fatalf("rest %s %s -> %d: %s", method, path, resp.StatusCode, b)
	}
}

func row_accessRunSql(ctx context.Context, t *testing.T, client *bigquery.Client, sql string) {
	t.Helper()
	q := client.Query(sql)
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query %q: %v", sql, err)
	}
	var row []bigquery.Value
	for {
		if err := it.Next(&row); err == iterator.Done {
			return
		} else if err != nil {
			t.Fatalf("read %q: %v", sql, err)
		}
	}
}

func row_accessCollectInts(ctx context.Context, t *testing.T, client *bigquery.Client, sql string) []int64 {
	t.Helper()
	q := client.Query(sql)
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query %q: %v", sql, err)
	}
	var out []int64
	for {
		var row []bigquery.Value
		if err := it.Next(&row); err == iterator.Done {
			return out
		} else if err != nil {
			t.Fatalf("read %q: %v", sql, err)
		}
		out = append(out, row[0].(int64))
	}
}

func TestRowAccessRowAccessPolicies(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	dsID := "row_access_go_ds"
	viewDsID := "row_access_go_v_ds"

	admin := row_accessClient(ctx, t, "")
	defer admin.Close()
	// Cleanup any leftovers from previous runs.
	_ = admin.Dataset(dsID).DeleteWithContents(ctx)
	_ = admin.Dataset(viewDsID).DeleteWithContents(ctx)

	if err := admin.Dataset(dsID).Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer admin.Dataset(dsID).DeleteWithContents(ctx)
	if err := admin.Dataset(viewDsID).Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create view dataset: %v", err)
	}
	defer admin.Dataset(viewDsID).DeleteWithContents(ctx)

	row_accessRunSql(ctx, t, admin,
		fmt.Sprintf("CREATE TABLE `%s.%s.orders` (id INT64, region STRING)", row_accessProject, dsID),
	)
	row_accessRunSql(ctx, t, admin,
		fmt.Sprintf(
			"INSERT INTO `%s.%s.orders` VALUES (1, 'EU'), (2, 'EU'), (3, 'US'), (4, 'US')",
			row_accessProject, dsID,
		),
	)

	// Authorized view setup via raw REST.
	row_accessRest(t, "POST",
		fmt.Sprintf("/bigquery/v2/projects/%s/datasets/%s/tables", row_accessProject, viewDsID),
		map[string]any{
			"tableReference": map[string]string{
				"projectId": row_accessProject,
				"datasetId": viewDsID,
				"tableId":   "all_orders",
			},
			"view": map[string]string{
				"query": fmt.Sprintf("SELECT id, region FROM `%s`.%s.orders", row_accessProject, dsID),
			},
		})
	row_accessRest(t, "PATCH",
		fmt.Sprintf("/bigquery/v2/projects/%s/datasets/%s", row_accessProject, dsID),
		map[string]any{
			"access": []map[string]any{
				{
					"view": map[string]string{
						"projectId": row_accessProject,
						"datasetId": viewDsID,
						"tableId":   "all_orders",
					},
				},
			},
		})
	row_accessRest(t, "POST",
		fmt.Sprintf(
			"/bigquery/v2/projects/%s/datasets/%s/tables/orders/rowAccessPolicies",
			row_accessProject, dsID,
		),
		map[string]any{
			"rowAccessPolicyReference": map[string]string{
				"projectId": row_accessProject,
				"datasetId": dsID,
				"tableId":   "orders",
				"policyId":  "eu_only",
			},
			"filterPredicate": "region = 'EU'",
			"grantees":        []string{"user:eu-analyst@example.com"},
		})

	// 1. EU caller sees only EU rows.
	eu := row_accessClient(ctx, t, "user:eu-analyst@example.com")
	defer eu.Close()
	got := row_accessCollectInts(ctx, t, eu,
		fmt.Sprintf("SELECT id FROM `%s.%s.orders` ORDER BY id", row_accessProject, dsID))
	want := []int64{1, 2}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("EU caller: got %v want %v", got, want)
	}

	// 2. Other caller sees zero rows.
	other := row_accessClient(ctx, t, "user:other@example.com")
	defer other.Close()
	got = row_accessCollectInts(ctx, t, other,
		fmt.Sprintf("SELECT id FROM `%s.%s.orders`", row_accessProject, dsID))
	if len(got) != 0 {
		t.Fatalf("other caller: expected 0 rows, got %v", got)
	}

	// 3. Authorized view still enforces RAP (no bypass). P2.d follow-up
	// #1 (2026-05-18) reversed the ADR 0018 authorized-view bypass
	// decision after empirical recording proved real BigQuery enforces
	// row-level security UNIVERSALLY through views. Integration +
	// conformance fixtures updated then; this E2E test caught in
	// P2.d follow-up #2 (2026-05-18).
	got = row_accessCollectInts(ctx, t, other,
		fmt.Sprintf("SELECT id FROM `%s.%s.all_orders` ORDER BY id", row_accessProject, viewDsID))
	want = []int64{}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("authorized view still enforces RAP: got %v want %v", got, want)
	}

	// 4. INFORMATION_SCHEMA reflects the policy.
	q := admin.Query(fmt.Sprintf(
		"SELECT policy_name, table_name FROM `%s`.%s.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
		row_accessProject, dsID,
	))
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("info schema: %v", err)
	}
	found := false
	for {
		var row []bigquery.Value
		if err := it.Next(&row); err == iterator.Done {
			break
		} else if err != nil {
			t.Fatalf("info schema read: %v", err)
		}
		if row[0].(string) == "eu_only" && row[1].(string) == "orders" {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("INFORMATION_SCHEMA.ROW_ACCESS_POLICIES did not list eu_only")
	}
}
