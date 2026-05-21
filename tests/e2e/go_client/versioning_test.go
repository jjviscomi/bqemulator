// Phase 7 versioning E2E (time travel, snapshots, clones, materialized
// views) for the bqemulator via the Google Go BigQuery client.

package e2e

import (
	"context"
	"fmt"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	"google.golang.org/api/iterator"
	"google.golang.org/api/option"
)

func versioningClient(ctx context.Context, t *testing.T) *bigquery.Client {
	t.Helper()
	client, err := bigquery.NewClient(
		ctx,
		"e2e-go-versioning",
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func runQuery(ctx context.Context, t *testing.T, client *bigquery.Client, sql string) {
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

func collectInts(ctx context.Context, t *testing.T, client *bigquery.Client, sql string) []int64 {
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

func TestVersioningVersioning(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	client := versioningClient(ctx, t)
	defer client.Close()

	dsID := "versioning_go_ds"
	ds := client.Dataset(dsID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() {
		if err := ds.DeleteWithContents(ctx); err != nil {
			t.Logf("cleanup: %v", err)
		}
	}()

	runQuery(ctx, t, client,
		fmt.Sprintf("CREATE TABLE `%s.%s.orders` (id INT64, country STRING, amount INT64)",
			"e2e-go-versioning", dsID),
	)
	runQuery(ctx, t, client,
		fmt.Sprintf("INSERT INTO `%s.%s.orders` VALUES (1, 'US', 10), (2, 'US', 20)",
			"e2e-go-versioning", dsID),
	)

	// 1. FOR SYSTEM_TIME AS OF
	time.Sleep(50 * time.Millisecond)
	boundary := time.Now().UTC().Format("2006-01-02 15:04:05.000000")
	time.Sleep(50 * time.Millisecond)

	runQuery(ctx, t, client,
		fmt.Sprintf("INSERT INTO `%s.%s.orders` VALUES (3, 'CA', 30)", "e2e-go-versioning", dsID),
	)

	historical := collectInts(ctx, t, client,
		fmt.Sprintf("SELECT id FROM `%s.%s.orders` "+
			"FOR SYSTEM_TIME AS OF TIMESTAMP '%s' ORDER BY id",
			"e2e-go-versioning", dsID, boundary),
	)
	if len(historical) != 2 || historical[0] != 1 || historical[1] != 2 {
		t.Fatalf("expected [1,2], got %v", historical)
	}

	// 2. CREATE SNAPSHOT TABLE
	runQuery(ctx, t, client,
		fmt.Sprintf("CREATE SNAPSHOT TABLE `%s.%s.orders_snap` CLONE `%s.%s.orders`",
			"e2e-go-versioning", dsID, "e2e-go-versioning", dsID),
	)
	runQuery(ctx, t, client,
		fmt.Sprintf("INSERT INTO `%s.%s.orders` VALUES (4, 'NZ', 40)", "e2e-go-versioning", dsID),
	)
	snap := collectInts(ctx, t, client,
		fmt.Sprintf("SELECT id FROM `%s.%s.orders_snap` ORDER BY id",
			"e2e-go-versioning", dsID),
	)
	if len(snap) != 3 {
		t.Fatalf("expected snapshot to have 3 rows, got %d", len(snap))
	}

	// 3. CREATE TABLE ... CLONE
	runQuery(ctx, t, client,
		fmt.Sprintf("CREATE TABLE `%s.%s.workcopy` CLONE `%s.%s.orders`",
			"e2e-go-versioning", dsID, "e2e-go-versioning", dsID),
	)
	runQuery(ctx, t, client,
		fmt.Sprintf("INSERT INTO `%s.%s.workcopy` VALUES (99, 'NZ', 999)", "e2e-go-versioning", dsID),
	)
	cloneIDs := collectInts(ctx, t, client,
		fmt.Sprintf("SELECT id FROM `%s.%s.workcopy` WHERE id = 99", "e2e-go-versioning", dsID),
	)
	srcIDs := collectInts(ctx, t, client,
		fmt.Sprintf("SELECT id FROM `%s.%s.orders` WHERE id = 99", "e2e-go-versioning", dsID),
	)
	if len(cloneIDs) != 1 || len(srcIDs) != 0 {
		t.Fatalf("clone should have id=99 but source should not; got clone=%v src=%v", cloneIDs, srcIDs)
	}

	// 4. CREATE MATERIALIZED VIEW
	runQuery(ctx, t, client,
		fmt.Sprintf("CREATE MATERIALIZED VIEW `%s.%s.country_totals` AS "+
			"SELECT country, SUM(amount) AS total "+
			"FROM `%s.%s.orders` GROUP BY country",
			"e2e-go-versioning", dsID, "e2e-go-versioning", dsID),
	)
	runQuery(ctx, t, client,
		fmt.Sprintf("INSERT INTO `%s.%s.orders` VALUES (10, 'US', 1000)", "e2e-go-versioning", dsID),
	)
	totals := collectInts(ctx, t, client,
		fmt.Sprintf("SELECT total FROM `%s.%s.country_totals` "+
			"WHERE country = 'US'",
			"e2e-go-versioning", dsID),
	)
	if len(totals) != 1 || totals[0] < 1000 {
		t.Fatalf("MV should auto-refresh: %v", totals)
	}
}
