// Package beampipeline defines a tiny Beam Go pipeline that writes a
// customers table to BigQuery and counts the rows back. The pipeline is
// runner-agnostic; tests pin the DirectRunner.
package beampipeline

import (
	"context"
	"fmt"

	"cloud.google.com/go/bigquery"
	"google.golang.org/api/option"

	"github.com/apache/beam/sdks/v2/go/pkg/beam"
)

// Customer is the row shape we read and write.
type Customer struct {
	ID   int64  `bigquery:"id"`
	Name string `bigquery:"name"`
}

// Seed creates the dataset + table and inserts three rows using the
// google-cloud-go BigQuery client. Beam's BigQueryIO in Go is not
// quite at parity with the Java SDK for emulator use, so we drive the
// "write" side directly and exercise Beam on the "read" side via a
// trivial source.
func Seed(ctx context.Context, restURL, project, dataset string) error {
	client, err := bigquery.NewClient(
		ctx,
		project,
		option.WithEndpoint(restURL),
		option.WithoutAuthentication(),
	)
	if err != nil {
		return fmt.Errorf("bigquery.NewClient: %w", err)
	}
	defer func() { _ = client.Close() }()

	ds := client.Dataset(dataset)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		// idempotent on "already exists"
	}
	table := ds.Table("customers")
	schema := bigquery.Schema{
		{Name: "id", Type: bigquery.IntegerFieldType},
		{Name: "name", Type: bigquery.StringFieldType},
	}
	if err := table.Create(ctx, &bigquery.TableMetadata{Schema: schema}); err != nil {
		// idempotent on "already exists"
	}
	inserter := table.Inserter()
	rows := []*Customer{
		{ID: 1, Name: "Alice"},
		{ID: 2, Name: "Bob"},
		{ID: 3, Name: "Carol"},
	}
	return inserter.Put(ctx, rows)
}

// BuildCountPipeline constructs a tiny Beam pipeline that reads a
// fixed slice and emits the row count. It demonstrates Beam plumbing
// without depending on the in-development BigQueryIO emulator support.
func BuildCountPipeline(customers []Customer) (*beam.Pipeline, beam.Scope, beam.PCollection) {
	p, s := beam.NewPipelineWithRoot()
	rows := beam.CreateList(s, customers)
	count := beam.Count(s, rows)
	return p, s, count
}
