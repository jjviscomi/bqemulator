// E2E: SESSION_USER() inside a RAP filter predicate (ADR 0038), exercised
// through the official cloud.google.com/go/bigquery Go client.
//
// The canonical "tenant isolation by email domain" pattern:
//   - Seed a tenants table with rows for two domains.
//   - Create a RAP filter
//     REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = tenant_id granted to
//     allAuthenticatedUsers.
//   - Each caller sees only their own tenant's rows.
//
// The X-Bqemu-Caller header is injected via the same custom
// http.RoundTripper as row_access_test.go.

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

const sessionUserProject = "e2e-go-row_access_session_user"

func sessionUserClient(ctx context.Context, t *testing.T, caller string) *bigquery.Client {
	t.Helper()
	httpClient := &http.Client{
		Transport: &callerHeaderTransport{caller: caller, inner: http.DefaultTransport},
	}
	client, err := bigquery.NewClient(
		ctx,
		sessionUserProject,
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
		option.WithHTTPClient(httpClient),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

// Bounded REST client so a stalled emulator can't hang the test run
// indefinitely (CodeRabbit thread PRRT_kwDOSkfuJ86EVwO0). The 15s cap
// matches the per-call SLA the rest of the e2e suite assumes.
var sessionUserHTTP = &http.Client{Timeout: 15 * time.Second}

func sessionUserRest(ctx context.Context, t *testing.T, method, path string, body any) {
	t.Helper()
	var rd io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("marshal: %v", err)
		}
		rd = bytes.NewReader(buf)
	}
	req, err := http.NewRequestWithContext(ctx, method, restURL()+path, rd)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := sessionUserHTTP.Do(req)
	if err != nil {
		t.Fatalf("rest %s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 && resp.StatusCode != http.StatusNoContent {
		b, _ := io.ReadAll(resp.Body)
		t.Fatalf("rest %s %s -> %d: %s", method, path, resp.StatusCode, b)
	}
}

func sessionUserRunSql(ctx context.Context, t *testing.T, client *bigquery.Client, sql string) {
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

func sessionUserCollectInts(ctx context.Context, t *testing.T, client *bigquery.Client, sql string) []int64 {
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

func TestRowAccessSessionUserFilter(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	dsID := "session_user_go_ds"

	admin := sessionUserClient(ctx, t, "")
	defer admin.Close()
	_ = admin.Dataset(dsID).DeleteWithContents(ctx)

	if err := admin.Dataset(dsID).Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer admin.Dataset(dsID).DeleteWithContents(ctx)

	sessionUserRunSql(ctx, t, admin,
		fmt.Sprintf("CREATE TABLE `%s.%s.tenants` (id INT64, tenant_id STRING)", sessionUserProject, dsID),
	)
	sessionUserRunSql(ctx, t, admin,
		fmt.Sprintf(
			"INSERT INTO `%s.%s.tenants` VALUES (1, 'example.com'), (2, 'example.com'), "+
				"(3, 'other.com'), (4, 'other.com')",
			sessionUserProject, dsID,
		),
	)

	sessionUserRest(ctx, t, "POST",
		fmt.Sprintf("/bigquery/v2/projects/%s/datasets/%s/tables/tenants/rowAccessPolicies",
			sessionUserProject, dsID),
		map[string]any{
			"rowAccessPolicyReference": map[string]string{
				"projectId": sessionUserProject,
				"datasetId": dsID,
				"tableId":   "tenants",
				"policyId":  "tenant_by_session_user",
			},
			"filterPredicate": "REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = tenant_id",
			"grantees":        []string{"allAuthenticatedUsers"},
		})

	// @example.com caller sees only example.com tenant rows.
	exampleClient := sessionUserClient(ctx, t, "user:alice@example.com")
	defer exampleClient.Close()
	got := sessionUserCollectInts(ctx, t, exampleClient,
		fmt.Sprintf("SELECT id FROM `%s.%s.tenants` ORDER BY id", sessionUserProject, dsID))
	want := []int64{1, 2}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("@example.com caller: got %v want %v", got, want)
	}

	// @other.com caller sees only other.com tenant rows.
	otherClient := sessionUserClient(ctx, t, "user:bob@other.com")
	defer otherClient.Close()
	got = sessionUserCollectInts(ctx, t, otherClient,
		fmt.Sprintf("SELECT id FROM `%s.%s.tenants` ORDER BY id", sessionUserProject, dsID))
	want = []int64{3, 4}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("@other.com caller: got %v want %v", got, want)
	}
}

func TestBareSelectSessionUser(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := sessionUserClient(ctx, t, "user:claire@example.com")
	defer client.Close()
	q := client.Query("SELECT SESSION_USER() AS who")
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	var row []bigquery.Value
	if err := it.Next(&row); err != nil {
		t.Fatalf("read: %v", err)
	}
	if row[0].(string) != "claire@example.com" {
		t.Fatalf("got %q, want claire@example.com", row[0])
	}
}

// TestBareSelectCurrentUser exercises CURRENT_USER(), documented as a
// co-equal alias for SESSION_USER() in BigQuery's reference (ADR 0040);
// same caller-identity semantics, same pre-translator substitution.
func TestBareSelectCurrentUser(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := sessionUserClient(ctx, t, "user:dani@example.com")
	defer client.Close()
	q := client.Query("SELECT CURRENT_USER() AS who")
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	var row []bigquery.Value
	if err := it.Next(&row); err != nil {
		t.Fatalf("read: %v", err)
	}
	if row[0].(string) != "dani@example.com" {
		t.Fatalf("got %q, want dani@example.com", row[0])
	}
}

// TestBareSelectSessionUserSystemVar pins the @@session.user spelling
// (ADR 0040). The system-variable form resolves via the same path as
// the function form — a future SQLGlot AST change for @@session.user
// would surface here as a test failure rather than silently producing
// the ANONYMOUS_CALLER literal.
func TestBareSelectSessionUserSystemVar(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := sessionUserClient(ctx, t, "user:eli@example.com")
	defer client.Close()
	q := client.Query("SELECT @@session.user AS who")
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	var row []bigquery.Value
	if err := it.Next(&row); err != nil {
		t.Fatalf("read: %v", err)
	}
	if row[0].(string) != "eli@example.com" {
		t.Fatalf("got %q, want eli@example.com", row[0])
	}
}
