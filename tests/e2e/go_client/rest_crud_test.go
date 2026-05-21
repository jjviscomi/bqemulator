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
