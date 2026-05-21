// Binary entry point for the example pipeline.
//
// Run:
//
//	go run ./... --rest_url=http://localhost:9050 \
//	    --project=bqemu-demo --dataset=beam_demo
package main

import (
	"context"
	"flag"
	"fmt"
	"log"

	"github.com/apache/beam/sdks/v2/go/pkg/beam"
	"github.com/apache/beam/sdks/v2/go/pkg/beam/runners/direct"
	_ "github.com/apache/beam/sdks/v2/go/pkg/beam/runners/direct"

	pipeline "github.com/jjviscomi/bqemu/examples/go/beam-pipeline"
)

var (
	restURL = flag.String("rest_url", "http://localhost:9050", "bqemulator REST URL")
	project = flag.String("project", "bqemu-demo", "BigQuery project")
	dataset = flag.String("dataset", "beam_demo", "BigQuery dataset")
)

func main() {
	flag.Parse()
	ctx := context.Background()
	if err := pipeline.Seed(ctx, *restURL, *project, *dataset); err != nil {
		log.Fatalf("seed: %v", err)
	}
	customers := []pipeline.Customer{
		{ID: 1, Name: "Alice"},
		{ID: 2, Name: "Bob"},
		{ID: 3, Name: "Carol"},
	}
	p, _, count := pipeline.BuildCountPipeline(customers)
	if err := direct.Execute(ctx, p); err != nil {
		log.Fatalf("direct.Execute: %v", err)
	}
	beam.PipelineOptions.Set("project", *project)
	fmt.Printf("OK: pipeline ran (count PCollection=%v)\n", count)
}
