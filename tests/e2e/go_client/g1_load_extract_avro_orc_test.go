// G1 load Avro + extract Avro E2E against a live bqemulator container.
//
// The Makefile target (`test-e2e-go`) pre-stages canonical Avro/ORC
// fixtures under a bind-mounted host directory; we reference them
// via `gs://g1-e2e/<file>` URIs.

package e2e

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	"google.golang.org/api/iterator"
	"google.golang.org/api/option"
)

const (
	g1Project = "e2e-go-g1"
	g1Bucket  = "g1-e2e"
)

func g1Client(ctx context.Context, t *testing.T) *bigquery.Client {
	t.Helper()
	client, err := bigquery.NewClient(
		ctx,
		g1Project,
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func postJob(t *testing.T, configuration map[string]interface{}) {
	t.Helper()
	body, err := json.Marshal(map[string]interface{}{
		"configuration": configuration,
	})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	url := fmt.Sprintf("%s/bigquery/v2/projects/%s/jobs", restURL(), g1Project)
	resp, err := http.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("POST %s: %v", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		var buf bytes.Buffer
		_, _ = buf.ReadFrom(resp.Body)
		t.Fatalf("POST %s -> %d: %s", url, resp.StatusCode, buf.String())
	}
}

func TestG1LoadAvro(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := g1Client(ctx, t)
	defer client.Close()

	ds := client.Dataset("g1_go_avro")
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() { _ = ds.DeleteWithContents(ctx) }()

	tbl := ds.Table("items")
	if err := tbl.Create(ctx, &bigquery.TableMetadata{
		Schema: bigquery.Schema{
			{Name: "id", Type: bigquery.IntegerFieldType, Required: true},
			{Name: "name", Type: bigquery.StringFieldType},
		},
	}); err != nil {
		t.Fatalf("create table: %v", err)
	}

	postJob(t, map[string]interface{}{
		"load": map[string]interface{}{
			"destinationTable": map[string]string{
				"projectId": g1Project,
				"datasetId": "g1_go_avro",
				"tableId":   "items",
			},
			"sourceUris":       []string{fmt.Sprintf("gs://%s/load_avro_basic.avro", g1Bucket)},
			"sourceFormat":     "AVRO",
			"writeDisposition": "WRITE_TRUNCATE",
		},
	})

	q := client.Query("SELECT id, name FROM g1_go_avro.items ORDER BY id")
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	type row struct {
		ID   int64
		Name string
	}
	got := make([]row, 0, 3)
	for {
		var r row
		err := it.Next(&r)
		if err == iterator.Done {
			break
		}
		if err != nil {
			t.Fatalf("iter: %v", err)
		}
		got = append(got, r)
	}
	if len(got) != 3 || got[0].Name != "alpha" || got[2].Name != "gamma" {
		t.Fatalf("unexpected rows: %+v", got)
	}
}

func TestG1ExtractAvroRoundTrip(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := g1Client(ctx, t)
	defer client.Close()

	ds := client.Dataset("g1_go_rt")
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() { _ = ds.DeleteWithContents(ctx) }()

	schema := bigquery.Schema{
		{Name: "id", Type: bigquery.IntegerFieldType, Required: true},
		{Name: "val", Type: bigquery.StringFieldType},
	}
	src := ds.Table("rt_src")
	if err := src.Create(ctx, &bigquery.TableMetadata{Schema: schema}); err != nil {
		t.Fatalf("create src: %v", err)
	}
	dst := ds.Table("rt_dst")
	if err := dst.Create(ctx, &bigquery.TableMetadata{Schema: schema}); err != nil {
		t.Fatalf("create dst: %v", err)
	}

	type srcRow struct {
		ID  int64  `bigquery:"id"`
		Val string `bigquery:"val"`
	}
	if err := src.Inserter().Put(ctx, []srcRow{{1, "x"}, {2, "y"}}); err != nil {
		t.Fatalf("insert: %v", err)
	}

	// Extract.
	postJob(t, map[string]interface{}{
		"extract": map[string]interface{}{
			"sourceTable": map[string]string{
				"projectId": g1Project,
				"datasetId": "g1_go_rt",
				"tableId":   "rt_src",
			},
			"destinationUris":   []string{fmt.Sprintf("gs://%s/extract_go.avro", g1Bucket)},
			"destinationFormat": "AVRO",
		},
	})

	// Re-load (proves the extracted file is valid Avro).
	postJob(t, map[string]interface{}{
		"load": map[string]interface{}{
			"destinationTable": map[string]string{
				"projectId": g1Project,
				"datasetId": "g1_go_rt",
				"tableId":   "rt_dst",
			},
			"sourceUris":       []string{fmt.Sprintf("gs://%s/extract_go.avro", g1Bucket)},
			"sourceFormat":     "AVRO",
			"writeDisposition": "WRITE_TRUNCATE",
		},
	})

	q := client.Query("SELECT id, val FROM g1_go_rt.rt_dst ORDER BY id")
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	type pair struct {
		ID  int64
		Val string
	}
	var got []pair
	for {
		var r pair
		err := it.Next(&r)
		if err == iterator.Done {
			break
		}
		if err != nil {
			t.Fatalf("iter: %v", err)
		}
		got = append(got, r)
	}
	if len(got) != 2 || got[0].Val != "x" || got[1].Val != "y" {
		t.Fatalf("round-trip mismatch: %+v", got)
	}
}
