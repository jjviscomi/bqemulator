// EXPORT DATA → Cloud Storage (CSV) E2E against a live bqemulator container.
//
// EXPORT DATA runs as a query job: the inner SELECT is materialised and
// written to the wildcard `uri` under BQEMU_GCS_LOCAL_ROOT. With a single
// output shard the `*` expands to a 12-digit zero-padded counter, so
// `export_go/*.csv` becomes `export_go/000000000000.csv`. The Makefile
// target (`test-e2e-go`) bind-mounts a host directory onto
// /var/lib/bqemu-gcs and exports its host path as BQEMU_GCS_HOST_ROOT, so
// we read the exported file straight off the mount.

package e2e

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	"google.golang.org/api/option"
)

func TestExportDataToCSV(t *testing.T) {
	hostRoot := os.Getenv("BQEMU_GCS_HOST_ROOT")
	if hostRoot == "" {
		t.Skip("BQEMU_GCS_HOST_ROOT not set (run via `make test-e2e-go`)")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	const project = "e2e-go-export"
	const bucket = "g1-e2e"

	client, err := bigquery.NewClient(
		ctx,
		project,
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	defer client.Close()

	ds := client.Dataset("export_go_ds")
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() { _ = ds.DeleteWithContents(ctx) }()

	src := ds.Table("src")
	if err := src.Create(ctx, &bigquery.TableMetadata{
		Schema: bigquery.Schema{
			{Name: "id", Type: bigquery.IntegerFieldType, Required: true},
			{Name: "name", Type: bigquery.StringFieldType},
		},
	}); err != nil {
		t.Fatalf("create table: %v", err)
	}

	type row struct {
		ID   int64  `bigquery:"id"`
		Name string `bigquery:"name"`
	}
	if err := src.Inserter().Put(ctx, []row{{1, "alpha"}, {2, "beta"}, {3, "gamma"}}); err != nil {
		t.Fatalf("insert: %v", err)
	}

	exportSQL := "EXPORT DATA OPTIONS (" +
		"uri = 'gs://" + bucket + "/export_go/*.csv', " +
		"format = 'CSV', overwrite = true) AS " +
		"SELECT id, name FROM export_go_ds.src ORDER BY id"
	job, err := client.Query(exportSQL).Run(ctx)
	if err != nil {
		t.Fatalf("export run: %v", err)
	}
	status, err := job.Wait(ctx)
	if err != nil {
		t.Fatalf("export wait: %v", err)
	}
	if err := status.Err(); err != nil {
		t.Fatalf("export job failed: %v", err)
	}

	shard := filepath.Join(hostRoot, bucket, "export_go", "000000000000.csv")
	data, err := os.ReadFile(shard)
	if err != nil {
		t.Fatalf("read shard %s: %v", shard, err)
	}

	var lines []string
	for _, l := range strings.Split(string(data), "\n") {
		l = strings.TrimRight(l, "\r")
		if l != "" {
			lines = append(lines, l)
		}
	}
	want := []string{"id,name", "1,alpha", "2,beta", "3,gamma"}
	if len(lines) != len(want) {
		t.Fatalf("unexpected lines: got %v want %v", lines, want)
	}
	for i := range want {
		if lines[i] != want[i] {
			t.Fatalf("line %d: got %q want %q", i, lines[i], want[i])
		}
	}
}
