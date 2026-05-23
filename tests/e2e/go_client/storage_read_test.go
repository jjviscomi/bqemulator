// Phase 4 Storage Read API E2E for the bqemulator via the Go
// BigQuery Storage Read gRPC client. Covers CreateReadSession +
// ReadRows with Arrow IPC payloads.

package e2e

import (
	"bytes"
	"context"
	"io"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	storage "cloud.google.com/go/bigquery/storage/apiv1"
	storagepb "cloud.google.com/go/bigquery/storage/apiv1/storagepb"
	"github.com/apache/arrow/go/v15/arrow/ipc"
	"google.golang.org/api/option"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const storage_readProject = "e2e-go-storage_read"

type readRow struct {
	ID    int64  `bigquery:"id"`
	Name  string `bigquery:"name"`
	Score int64  `bigquery:"score"`
}

func setupReadTable(t *testing.T, ctx context.Context) func() {
	t.Helper()
	client, err := bigquery.NewClient(
		ctx,
		storage_readProject,
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("rest client: %v", err)
	}
	datasetID := "storage_read_go_ds"
	tableID := "places"
	ds := client.Dataset(datasetID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("dataset create: %v", err)
	}
	schema := bigquery.Schema{
		{Name: "id", Type: bigquery.IntegerFieldType, Required: true},
		{Name: "name", Type: bigquery.StringFieldType},
		{Name: "score", Type: bigquery.IntegerFieldType},
	}
	if err := ds.Table(tableID).Create(ctx, &bigquery.TableMetadata{Schema: schema}); err != nil {
		t.Fatalf("table create: %v", err)
	}
	if err := ds.Table(tableID).Inserter().Put(ctx, []readRow{
		{ID: 1, Name: "Alice", Score: 90},
		{ID: 2, Name: "Bob", Score: 70},
		{ID: 3, Name: "Carol", Score: 85},
	}); err != nil {
		t.Fatalf("insert: %v", err)
	}
	return func() {
		_ = ds.DeleteWithContents(ctx)
		_ = client.Close()
	}
}

// TestStorageReadStorageReadShipCriterion exercises the ship-criterion for
// the Storage Read API from Go: CreateReadSession over a real gRPC
// channel, stream ReadRows responses, decode Arrow IPC record batches,
// and assert the seeded row count round-trips.
func TestStorageReadStorageReadShipCriterion(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	teardown := setupReadTable(t, ctx)
	defer teardown()

	conn, err := grpc.NewClient(
		grpcEndpoint(),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("grpc.NewClient: %v", err)
	}
	defer conn.Close()

	readClient, err := storage.NewBigQueryReadClient(
		ctx,
		option.WithGRPCConn(conn),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("NewBigQueryReadClient: %v", err)
	}
	defer readClient.Close()

	session, err := readClient.CreateReadSession(ctx, &storagepb.CreateReadSessionRequest{
		Parent: "projects/" + storage_readProject,
		ReadSession: &storagepb.ReadSession{
			Table:      "projects/" + storage_readProject + "/datasets/storage_read_go_ds/tables/places",
			DataFormat: storagepb.DataFormat_ARROW,
		},
		MaxStreamCount: 1,
	})
	if err != nil {
		t.Fatalf("CreateReadSession: %v", err)
	}
	if len(session.GetStreams()) == 0 {
		t.Fatal("CreateReadSession returned no streams")
	}

	// ``serialized_record_batch`` carries a BARE Arrow IPC
	// record-batch message (no schema-message prefix, no EOS-marker
	// suffix). The schema travels separately on
	// ``session.arrow_schema.serialized_schema`` (which we emit as a
	// one-message stream with trailing EOS — same shape pyarrow's
	// ``new_stream(schema).close()`` produces). ``ipc.NewReader``
	// expects a full stream, so we synthesise one per batch by
	// concatenating the schema-stream bytes (minus their trailing
	// EOS) + the batch message + an EOS marker.
	schemaStream := session.GetArrowSchema().GetSerializedSchema()
	if len(schemaStream) <= 8 {
		t.Fatalf("session.arrow_schema is empty or malformed (%d bytes)", len(schemaStream))
	}
	schemaOnly := schemaStream[:len(schemaStream)-8]
	eosMarker := []byte{0xff, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00}

	total := 0
	for _, stream := range session.GetStreams() {
		rows, err := readClient.ReadRows(ctx, &storagepb.ReadRowsRequest{ReadStream: stream.GetName()})
		if err != nil {
			t.Fatalf("ReadRows: %v", err)
		}
		for {
			resp, err := rows.Recv()
			if err == io.EOF {
				break
			}
			if err != nil {
				t.Fatalf("ReadRows.Recv: %v", err)
			}
			batch := resp.GetArrowRecordBatch().GetSerializedRecordBatch()
			if len(batch) == 0 {
				continue
			}
			merged := make([]byte, 0, len(schemaOnly)+len(batch)+8)
			merged = append(merged, schemaOnly...)
			merged = append(merged, batch...)
			merged = append(merged, eosMarker...)
			reader, err := ipc.NewReader(bytes.NewReader(merged))
			if err != nil {
				t.Fatalf("ipc.NewReader: %v", err)
			}
			for reader.Next() {
				rec := reader.Record()
				total += int(rec.NumRows())
			}
			reader.Release()
		}
	}
	if total != 3 {
		t.Fatalf("expected 3 rows from ReadRows, got %d", total)
	}
}
