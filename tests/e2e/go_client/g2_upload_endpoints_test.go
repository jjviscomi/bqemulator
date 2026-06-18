// G2 upload-host endpoints E2E against a live bqemulator container.
//
// The Go ``bigquery.NewClient(...).Dataset(...).Table(...).LoaderFrom(reader)``
// API goes through the upload-host endpoints — this test exercises
// both multipart (small payload) and resumable (large payload) paths.

package e2e

import (
	"bytes"
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	"google.golang.org/api/iterator"
	"google.golang.org/api/option"
)

const (
	g2Project = "e2e-go-g2"
)

func g2Client(ctx context.Context, t *testing.T) *bigquery.Client {
	t.Helper()
	client, err := bigquery.NewClient(
		ctx,
		g2Project,
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func TestG2LoadCSVMultipart(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := g2Client(ctx, t)
	defer client.Close()

	ds := client.Dataset("g2_go_csv")
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() { _ = ds.DeleteWithContents(ctx) }()

	tbl := ds.Table("rows")
	if err := tbl.Create(ctx, &bigquery.TableMetadata{
		Schema: bigquery.Schema{
			{Name: "id", Type: bigquery.IntegerFieldType},
			{Name: "name", Type: bigquery.StringFieldType},
		},
	}); err != nil {
		t.Fatalf("create table: %v", err)
	}

	csv := []byte("id,name\n1,alice\n2,bob\n3,carol\n4,dan\n")
	rs := bigquery.NewReaderSource(bytes.NewReader(csv))
	rs.SourceFormat = bigquery.CSV
	rs.SkipLeadingRows = 1
	rs.Schema = bigquery.Schema{
		{Name: "id", Type: bigquery.IntegerFieldType},
		{Name: "name", Type: bigquery.StringFieldType},
	}
	loader := tbl.LoaderFrom(rs)
	loader.WriteDisposition = bigquery.WriteTruncate

	job, err := loader.Run(ctx)
	if err != nil {
		t.Fatalf("loader run: %v", err)
	}
	status, err := job.Wait(ctx)
	if err != nil {
		t.Fatalf("loader wait: %v", err)
	}
	if status.Err() != nil {
		t.Fatalf("loader err: %v", status.Err())
	}

	q := client.Query(fmt.Sprintf("SELECT COUNT(*) AS n FROM `%s.g2_go_csv.rows`", g2Project))
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	var row struct{ N int64 }
	if err := it.Next(&row); err != nil && err != iterator.Done {
		t.Fatalf("iterate: %v", err)
	}
	if row.N != 4 {
		t.Fatalf("want 4 rows, got %d", row.N)
	}
}

func TestG2LoadNDJSONResumable(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	client := g2Client(ctx, t)
	defer client.Close()

	ds := client.Dataset("g2_go_json")
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() { _ = ds.DeleteWithContents(ctx) }()

	tbl := ds.Table("rows")
	if err := tbl.Create(ctx, &bigquery.TableMetadata{
		Schema: bigquery.Schema{
			{Name: "id", Type: bigquery.IntegerFieldType},
			{Name: "name", Type: bigquery.StringFieldType},
		},
	}); err != nil {
		t.Fatalf("create table: %v", err)
	}

	// Build a ~2 MiB NDJSON payload to push the client into the
	// resumable upload protocol.
	var b strings.Builder
	for i := 0; i < 60000; i += 1 {
		fmt.Fprintf(&b, "{\"id\":%d,\"name\":\"name-%d\"}\n", i, i)
	}
	ndjson := b.String()
	if len(ndjson) < 1_000_000 {
		t.Fatalf("payload too small: %d", len(ndjson))
	}

	rs := bigquery.NewReaderSource(strings.NewReader(ndjson))
	rs.SourceFormat = bigquery.JSON
	rs.Schema = bigquery.Schema{
		{Name: "id", Type: bigquery.IntegerFieldType},
		{Name: "name", Type: bigquery.StringFieldType},
	}
	loader := tbl.LoaderFrom(rs)
	loader.WriteDisposition = bigquery.WriteTruncate

	job, err := loader.Run(ctx)
	if err != nil {
		t.Fatalf("loader run: %v", err)
	}
	status, err := job.Wait(ctx)
	if err != nil {
		t.Fatalf("loader wait: %v", err)
	}
	if status.Err() != nil {
		t.Fatalf("loader err: %v", status.Err())
	}

	q := client.Query(fmt.Sprintf("SELECT COUNT(*) AS n FROM `%s.g2_go_json.rows`", g2Project))
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	var row struct{ N int64 }
	if err := it.Next(&row); err != nil && err != iterator.Done {
		t.Fatalf("iterate: %v", err)
	}
	if row.N != 60000 {
		t.Fatalf("want 60000 rows, got %d", row.N)
	}
}

func TestLoadTableCSVAutodetect(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := g2Client(ctx, t)
	defer client.Close()

	dsID := "g2_csv_autodetect"
	tblID := "rows"

	ds := client.Dataset(dsID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("Failed to create dataset: %v", err)
	}
	defer func() { _ = ds.DeleteWithContents(ctx) }()

	csvData := []byte("id,name,score\n1,alice,99.5\n2,bob,88.2\n")
	rs := bigquery.NewReaderSource(bytes.NewReader(csvData))
	rs.SourceFormat = bigquery.CSV
	rs.SkipLeadingRows = 1
	rs.AutoDetect = true
	
	loader := ds.Table(tblID).LoaderFrom(rs)
	loader.WriteDisposition = bigquery.WriteTruncate
	loader.CreateDisposition = bigquery.CreateIfNeeded

	job, err := loader.Run(ctx)
	if err != nil {
		t.Fatalf("Loader.Run failed: %v", err)
	}
	status, err := job.Wait(ctx)
	if err != nil {
		t.Fatalf("Job wait failed: %v", err)
	}
	if err := status.Err(); err != nil {
		t.Fatalf("Job failed: %v", err)
	}

	q := client.Query(fmt.Sprintf("SELECT COUNT(*) AS n FROM `%s.%s.%s`", g2Project, dsID, tblID))
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("Query failed: %v", err)
	}

	var row struct{ N int64 }
	err = it.Next(&row)
	if err != iterator.Done && err != nil {
		t.Fatalf("Next failed: %v", err)
	}
	if row.N != 2 {
		t.Errorf("Expected 2 rows, got %d", row.N)
	}

	// Verify inferred schema via Metadata
	meta, err := ds.Table(tblID).Metadata(ctx)
	if err != nil {
		t.Fatalf("Failed to fetch metadata: %v", err)
	}
	if len(meta.Schema) != 3 {
		t.Fatalf("Expected 3 schema fields, got %d", len(meta.Schema))
	}
	if meta.Schema[0].Name != "id" || (meta.Schema[0].Type != bigquery.IntegerFieldType && meta.Schema[0].Type != "INT64") {
		t.Errorf("Expected id INT64, got %v %v", meta.Schema[0].Name, meta.Schema[0].Type)
	}
	if meta.Schema[1].Name != "name" || (meta.Schema[1].Type != bigquery.StringFieldType && meta.Schema[1].Type != "STRING") {
		t.Errorf("Expected name STRING, got %v %v", meta.Schema[1].Name, meta.Schema[1].Type)
	}
	if meta.Schema[2].Name != "score" || (meta.Schema[2].Type != bigquery.FloatFieldType && meta.Schema[2].Type != "FLOAT64") {
		t.Errorf("Expected score FLOAT64, got %v %v", meta.Schema[2].Name, meta.Schema[2].Type)
	}
}
