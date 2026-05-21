// Binary entry point: read NDJSON on stdin, transform, write to BQ.
package main

import (
	"context"
	"flag"
	"log"
	"os"

	etl "github.com/jjviscomi/bqemu/examples/go/dataflow-local"
)

var (
	restURL = flag.String("rest_url", "http://localhost:9050", "bqemulator REST URL")
	project = flag.String("project", "bqemu-demo", "BigQuery project")
	dataset = flag.String("dataset", "dataflow_demo", "BigQuery dataset")
)

func main() {
	flag.Parse()
	ctx := context.Background()
	raws, err := etl.ReadEvents(os.Stdin)
	if err != nil {
		log.Fatalf("read: %v", err)
	}
	cleaned := make([]etl.CleanEvent, len(raws))
	for i, r := range raws {
		cleaned[i] = etl.Transform(r)
	}
	if err := etl.Sink(ctx, *restURL, *project, *dataset, cleaned); err != nil {
		log.Fatalf("sink: %v", err)
	}
	log.Printf("OK: wrote %d cleaned rows to %s.%s.clean_events", len(cleaned), *project, *dataset)
}
