package beampipeline_test

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/apache/beam/sdks/v2/go/pkg/beam"
	"github.com/apache/beam/sdks/v2/go/pkg/beam/runners/direct"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	pipeline "github.com/jjviscomi/bqemu/examples/go/beam-pipeline"
)

func bqemuImage() string {
	if v := os.Getenv("BQEMU_IMAGE"); v != "" {
		return v
	}
	return "ghcr.io/jjviscomi/bqemulator:dev"
}

func TestPipelineAgainstEmulator(t *testing.T) {
	ctx := context.Background()
	req := testcontainers.ContainerRequest{
		Image:        bqemuImage(),
		ExposedPorts: []string{"9050/tcp", "9060/tcp"},
		Env: map[string]string{
			"BQEMU_REST_HOST":     "0.0.0.0",
			"BQEMU_GRPC_HOST":     "0.0.0.0",
			"BQEMU_ADMIN_ENABLED": "1",
		},
		WaitingFor: wait.ForHTTP("/healthz").
			WithPort("9050/tcp").
			WithStartupTimeout(60 * time.Second),
	}
	container, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: req,
		Started:          true,
	})
	if err != nil {
		t.Fatalf("start bqemulator: %v", err)
	}
	defer func() { _ = container.Terminate(ctx) }()

	host, err := container.Host(ctx)
	if err != nil {
		t.Fatalf("host: %v", err)
	}
	port, err := container.MappedPort(ctx, "9050/tcp")
	if err != nil {
		t.Fatalf("port: %v", err)
	}
	restURL := fmt.Sprintf("http://%s:%s", host, port.Port())

	if err := pipeline.Seed(ctx, restURL, "bqemu-demo", "beam_demo"); err != nil {
		t.Fatalf("seed: %v", err)
	}

	customers := []pipeline.Customer{
		{ID: 1, Name: "Alice"},
		{ID: 2, Name: "Bob"},
		{ID: 3, Name: "Carol"},
	}
	p, _, _ := pipeline.BuildCountPipeline(customers)
	if _, err := direct.Execute(ctx, p); err != nil {
		t.Fatalf("execute: %v", err)
	}
	// PipelineOptions wiring sanity check.
	beam.PipelineOptions.Set("project", "bqemu-demo")
}
