// G3 Storage Read API E2E for the bqemulator — Avro wire format
// using the Go BigQuery Storage Read gRPC client + linkedin/goavro
// for decoding. Mirrors storage_read_test.go's Arrow shape but requests
// AVRO and asserts at the decoded-row level so a "bytes that
// proto-validate but no Avro decoder accepts" regression is caught.

package e2e

import (
	"context"
	"io"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	storage "cloud.google.com/go/bigquery/storage/apiv1"
	storagepb "cloud.google.com/go/bigquery/storage/apiv1/storagepb"
	"github.com/linkedin/goavro/v2"
	"google.golang.org/api/option"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const g3Project = "e2e-go-g3"

type avroRow struct {
	ID    int64  `bigquery:"id"`
	Name  string `bigquery:"name"`
	Score int64  `bigquery:"score"`
}

func setupAvroReadTable(t *testing.T, ctx context.Context) func() {
	t.Helper()
	client, err := bigquery.NewClient(
		ctx,
		g3Project,
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("rest client: %v", err)
	}
	datasetID := "g3_go_ds"
	tableID := "avro_rows"
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
	if err := ds.Table(tableID).Inserter().Put(ctx, []avroRow{
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

// TestG3StorageReadAvroDecodesViaGoavro exercises the Avro wire path
// from Go: CreateReadSession + ReadRows with DataFormat_AVRO, decode
// every row via linkedin/goavro, and assert the decoded values equal
// the seeded rows.
func TestG3StorageReadAvroDecodesViaGoavro(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	teardown := setupAvroReadTable(t, ctx)
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
		Parent: "projects/" + g3Project,
		ReadSession: &storagepb.ReadSession{
			Table:      "projects/" + g3Project + "/datasets/g3_go_ds/tables/avro_rows",
			DataFormat: storagepb.DataFormat_AVRO,
		},
		MaxStreamCount: 1,
	})
	if err != nil {
		t.Fatalf("CreateReadSession: %v", err)
	}
	if len(session.GetStreams()) == 0 {
		t.Fatal("CreateReadSession returned no streams")
	}
	if session.GetDataFormat() != storagepb.DataFormat_AVRO {
		t.Fatalf("session data_format = %v, want AVRO", session.GetDataFormat())
	}
	schemaJSON := session.GetAvroSchema().GetSchema()
	if schemaJSON == "" {
		t.Fatal("session missing avro_schema")
	}

	codec, err := goavro.NewCodec(schemaJSON)
	if err != nil {
		t.Fatalf("goavro.NewCodec: %v", err)
	}

	type row map[string]any
	var decoded []row
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
			payload := resp.GetAvroRows().GetSerializedBinaryRows()
			if len(payload) == 0 {
				continue
			}
			// Naked rows — linearly decode resp.GetRowCount() records
			// back-to-back, using goavro's (value, remainder) return
			// shape to track the cursor.
			cursor := payload
			for j := int64(0); j < resp.GetRowCount(); j++ {
				native, remaining, err := codec.NativeFromBinary(cursor)
				if err != nil {
					t.Fatalf("goavro decode at row %d: %v", j, err)
				}
				m, ok := native.(map[string]any)
				if !ok {
					t.Fatalf("expected map row, got %T", native)
				}
				decoded = append(decoded, m)
				cursor = remaining
			}
		}
	}

	if len(decoded) != 3 {
		t.Fatalf("expected 3 decoded Avro rows, got %d", len(decoded))
	}
	// goavro decodes nullable-union values as map[string]any{"<type>": value}
	// (so we walk one level into the union wrapper for non-REQUIRED columns
	// like ``name`` / ``score``) and bare values for REQUIRED columns like
	// ``id`` (the emulator emits a bare ``T`` for ``mode='REQUIRED'`` per
	// real BigQuery's documented Avro shape).
	getString := func(m row, key string) string {
		if wrapper, ok := m[key].(map[string]any); ok {
			if s, ok := wrapper["string"].(string); ok {
				return s
			}
		}
		if s, ok := m[key].(string); ok {
			return s
		}
		return ""
	}
	byID := map[int64]row{}
	for _, r := range decoded {
		id, _ := r["id"].(int64)
		byID[id] = r
	}
	if got := getString(byID[1], "name"); got != "Alice" {
		t.Errorf("row 1 name = %q, want Alice", got)
	}
	if got := getString(byID[3], "name"); got != "Carol" {
		t.Errorf("row 3 name = %q, want Carol", got)
	}
}
