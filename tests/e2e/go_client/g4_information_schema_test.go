// G4 INFORMATION_SCHEMA E2E for the Go BigQuery client. Verifies the
// emulator answers INFORMATION_SCHEMA.TABLES and
// INFORMATION_SCHEMA.COLUMNS queries with the documented column shape.

package e2e

import (
	"context"
	"fmt"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	"google.golang.org/api/iterator"
)

func TestG4InformationSchemaTables(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := bqClient(ctx, t)
	defer client.Close()

	datasetID := "g4_go_tables_ds"
	ds := client.Dataset(datasetID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() { _ = ds.DeleteWithContents(ctx) }()

	for _, name := range []string{"orders", "customers"} {
		tbl := ds.Table(name)
		if err := tbl.Create(ctx, &bigquery.TableMetadata{
			Schema: bigquery.Schema{
				{Name: "id", Type: bigquery.IntegerFieldType},
			},
		}); err != nil {
			t.Fatalf("create %s: %v", name, err)
		}
	}

	q := client.Query(fmt.Sprintf(
		"SELECT table_name FROM `%s.%s`.INFORMATION_SCHEMA.TABLES "+
			"WHERE table_type = 'BASE TABLE' ORDER BY table_name",
		project(), datasetID,
	))
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	var names []string
	for {
		// Explicit bigquery tag — see colRow struct in the columns test
		// below for the same rationale.
		var row struct {
			TableName string `bigquery:"table_name"`
		}
		err := it.Next(&row)
		if err == iterator.Done {
			break
		}
		if err != nil {
			t.Fatalf("iter: %v", err)
		}
		names = append(names, row.TableName)
	}
	want := []string{"customers", "orders"}
	if len(names) != len(want) {
		t.Fatalf("expected %d tables, got %d", len(want), len(names))
	}
	for i, n := range want {
		if names[i] != n {
			t.Errorf("position %d: expected %s, got %s", i, n, names[i])
		}
	}
}

func TestG4InformationSchemaColumns(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := bqClient(ctx, t)
	defer client.Close()

	datasetID := "g4_go_cols_ds"
	ds := client.Dataset(datasetID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() { _ = ds.DeleteWithContents(ctx) }()

	tbl := ds.Table("events")
	if err := tbl.Create(ctx, &bigquery.TableMetadata{
		Schema: bigquery.Schema{
			{Name: "id", Type: bigquery.IntegerFieldType, Required: true},
			{Name: "ts", Type: bigquery.TimestampFieldType},
			{Name: "payload", Type: bigquery.StringFieldType},
		},
	}); err != nil {
		t.Fatalf("create table: %v", err)
	}

	q := client.Query(fmt.Sprintf(
		"SELECT column_name, data_type FROM `%s.%s`.INFORMATION_SCHEMA.COLUMNS "+
			"WHERE table_name = 'events' ORDER BY ordinal_position",
		project(), datasetID,
	))
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	// The Go ``cloud.google.com/go/bigquery`` client maps struct fields to
	// column names by lowercasing the field name unless an explicit
	// ``bigquery:"..."`` tag is provided. Without the tag, the client
	// looks for a ``columnname`` column and finds nothing — the struct
	// is populated with zero values. Pin the mapping explicitly.
	type colRow struct {
		ColumnName string `bigquery:"column_name"`
		DataType   string `bigquery:"data_type"`
	}
	var rows []colRow
	for {
		var r colRow
		err := it.Next(&r)
		if err == iterator.Done {
			break
		}
		if err != nil {
			t.Fatalf("iter: %v", err)
		}
		rows = append(rows, r)
	}
	if len(rows) != 3 {
		t.Fatalf("expected 3 columns, got %d", len(rows))
	}
	want := []colRow{
		{"id", "INT64"},
		{"ts", "TIMESTAMP"},
		{"payload", "STRING"},
	}
	for i, w := range want {
		if rows[i] != w {
			t.Errorf("row %d: expected %+v, got %+v", i, w, rows[i])
		}
	}
}
