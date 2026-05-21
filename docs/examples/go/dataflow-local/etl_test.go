package dataflowlocal_test

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	"google.golang.org/api/option"

	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	etl "github.com/jjviscomi/bqemu/examples/go/dataflow-local"
)

func TestTransformLowercasesEmail(t *testing.T) {
	out := etl.Transform(etl.RawEvent{ID: 1, Name: "Alice", Email: "  Alice@EXAMPLE.test "})
	if out.EmailLower != "alice@example.test" {
		t.Fatalf("unexpected: %s", out.EmailLower)
	}
}

func TestTransformDefaultsEmptyEmail(t *testing.T) {
	out := etl.Transform(etl.RawEvent{ID: 2, Name: "Bob"})
	if out.EmailLower != "unknown@example.test" {
		t.Fatalf("unexpected: %s", out.EmailLower)
	}
}

func TestReadEventsParsesNDJSON(t *testing.T) {
	ndjson := `{"id":1,"name":"Alice","email":"a@x.test"}
{"id":2,"name":"Bob","email":"b@x.test"}
`
	rows, err := etl.ReadEvents(strings.NewReader(ndjson))
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 {
		t.Fatalf("expected 2 rows, got %d", len(rows))
	}
}

func bqemuImage() string {
	if v := os.Getenv("BQEMU_IMAGE"); v != "" {
		return v
	}
	return "ghcr.io/jjviscomi/bqemulator:dev"
}

func TestSinkEndToEnd(t *testing.T) {
	ctx := context.Background()
	req := testcontainers.ContainerRequest{
		Image:        bqemuImage(),
		ExposedPorts: []string{"9050/tcp"},
		Env: map[string]string{
			"BQEMU_REST_HOST":     "0.0.0.0",
			"BQEMU_ADMIN_ENABLED": "1",
		},
		WaitingFor: wait.ForHTTP("/healthz").WithPort("9050/tcp").
			WithStartupTimeout(60 * time.Second),
	}
	c, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: req, Started: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = c.Terminate(ctx) }()

	host, _ := c.Host(ctx)
	port, _ := c.MappedPort(ctx, "9050/tcp")
	restURL := fmt.Sprintf("http://%s:%s", host, port.Port())

	events := []etl.CleanEvent{
		{ID: 1, Name: "Alice", EmailLower: "alice@x.test"},
		{ID: 2, Name: "Bob", EmailLower: "bob@x.test"},
	}
	if err := etl.Sink(ctx, restURL, "bqemu-demo", "dataflow_test", events); err != nil {
		t.Fatal(err)
	}

	// Sanity-check: COUNT(*) returns 2.
	client, err := bigquery.NewClient(
		ctx, "bqemu-demo",
		option.WithEndpoint(restURL),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = client.Close() }()
	q := client.Query("SELECT COUNT(*) AS n FROM `bqemu-demo.dataflow_test.clean_events`")
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatal(err)
	}
	var row struct{ N int64 }
	if err := it.Next(&row); err != nil {
		t.Fatal(err)
	}
	if row.N != 2 {
		t.Fatalf("expected 2 rows, got %d", row.N)
	}
}
