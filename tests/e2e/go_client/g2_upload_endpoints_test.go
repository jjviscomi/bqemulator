// G2 upload-host endpoints E2E against a live bqemulator container.
//
// The Go ``bigquery.NewClient(...).Dataset(...).Table(...).LoaderFrom(reader)``
// API goes through the upload-host endpoints. Payloads under the client's
// upload chunk size (16 MiB by default) go out as a single multipart
// request, which is the path these tests exercise.

package e2e

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	"google.golang.org/api/googleapi"
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

// g2IDNameTable recreates dataset dsID holding an empty
// rows(id INT64, name STRING) table and drops it when the test ends.
func g2IDNameTable(ctx context.Context, t *testing.T, client *bigquery.Client, dsID string) *bigquery.Table {
	t.Helper()
	ds := client.Dataset(dsID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	t.Cleanup(func() { _ = ds.DeleteWithContents(context.Background()) })

	tbl := ds.Table("rows")
	if err := tbl.Create(ctx, &bigquery.TableMetadata{Schema: g2IDNameSchema}); err != nil {
		t.Fatalf("create table: %v", err)
	}
	return tbl
}

var g2IDNameSchema = bigquery.Schema{
	{Name: "id", Type: bigquery.IntegerFieldType},
	{Name: "name", Type: bigquery.StringFieldType},
}

// g2LoadAndCount runs a WRITE_TRUNCATE load of rs into tbl and
// returns the table's row count afterwards.
func g2LoadAndCount(
	ctx context.Context,
	t *testing.T,
	client *bigquery.Client,
	tbl *bigquery.Table,
	rs *bigquery.ReaderSource,
) int64 {
	t.Helper()
	rs.Schema = g2IDNameSchema
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

	q := client.Query(fmt.Sprintf("SELECT COUNT(*) AS n FROM `%s.%s.%s`", g2Project, tbl.DatasetID, tbl.TableID))
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	var row struct{ N int64 }
	if err := it.Next(&row); err != nil && err != iterator.Done {
		t.Fatalf("iterate: %v", err)
	}
	return row.N
}

// The Go client labels the multipart media part with whatever
// http.DetectContentType sniffs from the first 512 bytes.
func TestG2LoadCSVMultipart(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := g2Client(ctx, t)
	defer client.Close()
	tbl := g2IDNameTable(ctx, t, client, "g2_go_csv")

	rs := bigquery.NewReaderSource(bytes.NewReader([]byte("id,name\n1,alice\n2,bob\n3,carol\n4,dan\n")))
	rs.SourceFormat = bigquery.CSV
	rs.SkipLeadingRows = 1
	if n := g2LoadAndCount(ctx, t, client, tbl, rs); n != 4 {
		t.Fatalf("want 4 rows, got %d", n)
	}
}

// A CSV whose first bytes match a binary signature is sniffed as that
// binary type. BigQuery ignores the media part's type and parses per
// sourceFormat, so the load must succeed regardless of the label.
func TestG2LoadCSVMultipartSniffedAsNonTextType(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := g2Client(ctx, t)
	defer client.Close()
	tbl := g2IDNameTable(ctx, t, client, "g2_go_csv_sniffed")

	for _, tc := range []struct {
		csv      string
		sniffed  string
		wantRows int64
	}{
		{"BMI_band,label\n1,under\n2,normal\n", "image/bmp", 2},
		{"<b>id</b>,name\n1,alice\n", "text/html; charset=utf-8", 1},
	} {
		if got := http.DetectContentType([]byte(tc.csv)); got != tc.sniffed {
			t.Fatalf("precondition: DetectContentType(%q) = %q, want %q", tc.csv, got, tc.sniffed)
		}
		rs := bigquery.NewReaderSource(strings.NewReader(tc.csv))
		rs.SourceFormat = bigquery.CSV
		rs.SkipLeadingRows = 1
		if n := g2LoadAndCount(ctx, t, client, tbl, rs); n != tc.wantRows {
			t.Fatalf("%s: want %d rows, got %d", tc.sniffed, tc.wantRows, n)
		}
	}
}

// A multi-MiB payload is still below the client's upload chunk size, so
// it also goes out as one multipart request.
func TestG2LoadNDJSONMultipartMultiMiB(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	client := g2Client(ctx, t)
	defer client.Close()
	tbl := g2IDNameTable(ctx, t, client, "g2_go_json")

	var b strings.Builder
	for i := 0; i < 60000; i += 1 {
		fmt.Fprintf(&b, "{\"id\":%d,\"name\":\"name-%d\"}\n", i, i)
	}
	ndjson := b.String()
	if len(ndjson) < 1_000_000 || len(ndjson) >= googleapi.DefaultUploadChunkSize {
		t.Fatalf("payload must be multi-MiB but under one upload chunk: %d", len(ndjson))
	}

	rs := bigquery.NewReaderSource(strings.NewReader(ndjson))
	rs.SourceFormat = bigquery.JSON
	if n := g2LoadAndCount(ctx, t, client, tbl, rs); n != 60000 {
		t.Fatalf("want 60000 rows, got %d", n)
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
	if meta.Schema[0].Name != "id" || meta.Schema[0].Type != bigquery.IntegerFieldType {
		t.Errorf("Expected id INTEGER, got %v %v", meta.Schema[0].Name, meta.Schema[0].Type)
	}
	if meta.Schema[1].Name != "name" || meta.Schema[1].Type != bigquery.StringFieldType {
		t.Errorf("Expected name STRING, got %v %v", meta.Schema[1].Name, meta.Schema[1].Type)
	}
	if meta.Schema[2].Name != "score" || meta.Schema[2].Type != bigquery.FloatFieldType {
		t.Errorf("Expected score FLOAT, got %v %v", meta.Schema[2].Name, meta.Schema[2].Type)
	}
}
