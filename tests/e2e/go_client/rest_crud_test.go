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

// TestRestCrudSingleDDLQueryResultShape verifies the job result of a lone
// DDL statement: CREATE TABLE returns the declared schema with zero rows
// (not DuckDB's Count status column), CTAS returns the SELECT's schema
// with zero rows (no leaked status row), and DROP TABLE returns a fully
// empty result — matching the rest_crud/ddl_result_* conformance corpus
// recorded from real BigQuery.
func TestRestCrudSingleDDLQueryResultShape(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	client := bqClient(ctx, t)
	defer client.Close()

	datasetID := "e2e_go_ddl_result"
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

	// CREATE TABLE: declared schema, zero rows.
	createIt, err := client.Query(fmt.Sprintf(
		"CREATE TABLE `%s.%s.t` (id INT64, name STRING)", project(), datasetID,
	)).Read(ctx)
	if err != nil {
		t.Fatalf("create table query: %v", err)
	}
	var row []bigquery.Value
	if err := createIt.Next(&row); err != iterator.Done {
		t.Fatalf("expected zero rows from CREATE TABLE, got row=%v err=%v", row, err)
	}
	if got := len(createIt.Schema); got != 2 {
		t.Fatalf("expected 2 schema fields from CREATE TABLE, got %d", got)
	}
	if createIt.Schema[0].Name != "id" || createIt.Schema[0].Type != bigquery.IntegerFieldType {
		t.Fatalf("unexpected first field: %+v", createIt.Schema[0])
	}
	if createIt.Schema[1].Name != "name" || createIt.Schema[1].Type != bigquery.StringFieldType {
		t.Fatalf("unexpected second field: %+v", createIt.Schema[1])
	}

	// CTAS: the SELECT's schema, zero rows (no leaked status row).
	ctasIt, err := client.Query(fmt.Sprintf(
		"CREATE TABLE `%s.%s.t2` AS SELECT 1 AS id, 'x' AS nm", project(), datasetID,
	)).Read(ctx)
	if err != nil {
		t.Fatalf("ctas query: %v", err)
	}
	if err := ctasIt.Next(&row); err != iterator.Done {
		t.Fatalf("expected zero rows from CTAS, got row=%v err=%v", row, err)
	}
	if got := len(ctasIt.Schema); got != 2 {
		t.Fatalf("expected 2 schema fields from CTAS, got %d", got)
	}

	// DROP TABLE: fully empty result.
	dropIt, err := client.Query(fmt.Sprintf(
		"DROP TABLE `%s.%s.t`", project(), datasetID,
	)).Read(ctx)
	if err != nil {
		t.Fatalf("drop query: %v", err)
	}
	if err := dropIt.Next(&row); err != iterator.Done {
		t.Fatalf("expected zero rows from DROP TABLE, got row=%v err=%v", row, err)
	}
	if got := len(dropIt.Schema); got != 0 {
		t.Fatalf("expected empty schema from DROP TABLE, got %d fields", got)
	}
}

// TestRestCrudDropSchemaNonEmptyRequiresCascade verifies that a bare DROP
// SCHEMA on a non-empty dataset is rejected (reason resourceInUse) and the
// dataset survives, while DROP SCHEMA ... CASCADE drops it. Pinned by the
// rest_crud/ddl_drop_schema_* conformance corpus recorded from real BigQuery.
func TestRestCrudDropSchemaNonEmptyRequiresCascade(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	client := bqClient(ctx, t)
	defer client.Close()

	datasetID := "e2e_go_drop_schema_restrict"
	ds := client.Dataset(datasetID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() {
		if err := ds.DeleteWithContents(ctx); err != nil {
			t.Logf("cleanup: %v", err)
		}
	}()
	if err := ds.Table("t").Create(ctx, &bigquery.TableMetadata{
		Schema: bigquery.Schema{{Name: "id", Type: bigquery.IntegerFieldType}},
	}); err != nil {
		t.Fatalf("create table: %v", err)
	}

	// Bare DROP SCHEMA on the non-empty dataset must fail (at Run or in
	// the job's terminal status).
	failed := false
	if job, err := client.Query(fmt.Sprintf("DROP SCHEMA `%s.%s`", project(), datasetID)).Run(ctx); err != nil {
		failed = true
	} else if status, werr := job.Wait(ctx); werr != nil || status.Err() != nil {
		failed = true
	}
	if !failed {
		t.Fatalf("expected bare DROP SCHEMA on a non-empty dataset to fail")
	}
	if _, err := ds.Metadata(ctx); err != nil {
		t.Fatalf("dataset should survive a rejected drop: %v", err)
	}

	// CASCADE drops the dataset and its contents.
	job, err := client.Query(fmt.Sprintf("DROP SCHEMA `%s.%s` CASCADE", project(), datasetID)).Run(ctx)
	if err != nil {
		t.Fatalf("cascade run: %v", err)
	}
	status, err := job.Wait(ctx)
	if err != nil {
		t.Fatalf("cascade wait: %v", err)
	}
	if err := status.Err(); err != nil {
		t.Fatalf("cascade job failed: %v", err)
	}
	if _, err := ds.Metadata(ctx); err == nil {
		t.Fatalf("dataset should be gone after CASCADE")
	}
}
