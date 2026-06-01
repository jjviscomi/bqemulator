// Phase 1 REST CRUD + query E2E for the bqemulator via the Google Go
// BigQuery client. Matches the Phase 1 ship criterion.

package e2e

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	"google.golang.org/api/iterator"
	"google.golang.org/api/option"
)

func project() string {
	if v := os.Getenv("BQEMU_PROJECT"); v != "" {
		return v
	}
	return "e2e-go"
}

func bqClient(ctx context.Context, t *testing.T) *bigquery.Client {
	t.Helper()
	client, err := bigquery.NewClient(
		ctx,
		project(),
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func TestRestCrudDatasetTableInsertQuery(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := bqClient(ctx, t)
	defer client.Close()

	datasetID := "e2e_go_ds"
	tableID := "customers"
	ds := client.Dataset(datasetID)
	// Clean start.
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() {
		if err := ds.DeleteWithContents(ctx); err != nil {
			t.Logf("cleanup delete: %v", err)
		}
	}()

	tbl := ds.Table(tableID)
	schema := bigquery.Schema{
		{Name: "id", Type: bigquery.IntegerFieldType, Required: true},
		{Name: "name", Type: bigquery.StringFieldType},
	}
	if err := tbl.Create(ctx, &bigquery.TableMetadata{Schema: schema}); err != nil {
		t.Fatalf("create table: %v", err)
	}

	type customerRow struct {
		ID   int64  `bigquery:"id"`
		Name string `bigquery:"name"`
	}
	rows := []customerRow{
		{ID: 1, Name: "Alice"},
		{ID: 2, Name: "Bob"},
	}
	if err := tbl.Inserter().Put(ctx, rows); err != nil {
		t.Fatalf("insert: %v", err)
	}

	q := client.Query(fmt.Sprintf(
		"SELECT COUNT(*) AS n FROM `%s.%s.%s`",
		project(), datasetID, tableID,
	))
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query read: %v", err)
	}
	var row struct{ N int64 }
	if err := it.Next(&row); err != nil && err != iterator.Done {
		t.Fatalf("row next: %v", err)
	}
	if row.N != 2 {
		t.Fatalf("expected 2 rows, got %d", row.N)
	}
}

func TestRestCrudDropTableRemovesFromCatalog(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := bqClient(ctx, t)
	defer client.Close()

	datasetID := "e2e_go_ds_drop"
	tableID := "to_drop"
	ds := client.Dataset(datasetID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() {
		if err := ds.DeleteWithContents(ctx); err != nil {
			t.Logf("cleanup delete: %v", err)
		}
	}()

	tbl := ds.Table(tableID)
	if err := tbl.Create(ctx, &bigquery.TableMetadata{
		Schema: bigquery.Schema{{Name: "id", Type: bigquery.IntegerFieldType}},
	}); err != nil {
		t.Fatalf("create table: %v", err)
	}
	if _, err := tbl.Metadata(ctx); err != nil {
		t.Fatalf("table should be visible before drop: %v", err)
	}

	// Drop via DDL submitted through jobs.query.
	q := client.Query(fmt.Sprintf("DROP TABLE `%s.%s.%s`", project(), datasetID, tableID))
	job, err := q.Run(ctx)
	if err != nil {
		t.Fatalf("drop run: %v", err)
	}
	status, err := job.Wait(ctx)
	if err != nil {
		t.Fatalf("drop wait: %v", err)
	}
	if err := status.Err(); err != nil {
		t.Fatalf("drop job failed: %v", err)
	}

	// Gone from tables.get, matching BigQuery (a dropped table 404s).
	if _, err := tbl.Metadata(ctx); err == nil {
		t.Fatalf("expected dropped table to be absent from tables.get")
	}

	// Gone from tables.list.
	it := ds.Tables(ctx)
	for {
		meta, err := it.Next()
		if err == iterator.Done {
			break
		}
		if err != nil {
			t.Fatalf("list tables: %v", err)
		}
		if meta.TableID == tableID {
			t.Fatalf("dropped table still present in tables.list")
		}
	}
}
