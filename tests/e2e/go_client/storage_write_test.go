// Phase 5 Storage Write API E2E for the bqemulator via raw gRPC.
// Covers CreateWriteStream + AppendRows (proto) on a COMMITTED stream.

package e2e

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	storage "cloud.google.com/go/bigquery/storage/apiv1"
	storagepb "cloud.google.com/go/bigquery/storage/apiv1/storagepb"
	"google.golang.org/api/option"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protodesc"
	"google.golang.org/protobuf/reflect/protoreflect"
	"google.golang.org/protobuf/types/descriptorpb"
	"google.golang.org/protobuf/types/dynamicpb"
)

func grpcEndpoint() string {
	if v := os.Getenv("BQEMU_GRPC_ENDPOINT"); v != "" {
		return v
	}
	return "localhost:9060"
}

func setupWriteTable(t *testing.T, ctx context.Context, datasetID, tableID string) func() {
	t.Helper()
	client, err := bigquery.NewClient(
		ctx,
		project(),
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("rest client: %v", err)
	}
	ds := client.Dataset(datasetID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("dataset create: %v", err)
	}
	schema := bigquery.Schema{
		{Name: "id", Type: bigquery.IntegerFieldType, Required: true},
		{Name: "name", Type: bigquery.StringFieldType},
	}
	if err := ds.Table(tableID).Create(ctx, &bigquery.TableMetadata{Schema: schema}); err != nil {
		t.Fatalf("table create: %v", err)
	}
	return func() {
		_ = ds.DeleteWithContents(ctx)
		_ = client.Close()
	}
}

func countRows(t *testing.T, datasetID, tableID string) int {
	t.Helper()
	url := fmt.Sprintf(
		"%s/bigquery/v2/projects/%s/datasets/%s/tables/%s/data",
		restURL(), project(), datasetID, tableID,
	)
	resp, err := http.Get(url)
	if err != nil {
		t.Fatalf("tabledata.list: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var decoded struct {
		TotalRows string `json:"totalRows"`
	}
	if err := json.Unmarshal(body, &decoded); err != nil {
		t.Fatalf("decode: %v (%s)", err, string(body))
	}
	var n int
	fmt.Sscanf(decoded.TotalRows, "%d", &n)
	return n
}

// Build a dynamic descriptor for {id: int64, name: string}.
func buildRowDescriptor(t *testing.T) (*descriptorpb.DescriptorProto, protoreflect.MessageDescriptor) {
	t.Helper()
	int64Type := descriptorpb.FieldDescriptorProto_TYPE_INT64
	stringType := descriptorpb.FieldDescriptorProto_TYPE_STRING
	optionalLabel := descriptorpb.FieldDescriptorProto_LABEL_OPTIONAL

	msg := &descriptorpb.DescriptorProto{
		Name: proto.String("Row"),
		Field: []*descriptorpb.FieldDescriptorProto{
			{
				Name:   proto.String("id"),
				Number: proto.Int32(1),
				Type:   &int64Type,
				Label:  &optionalLabel,
			},
			{
				Name:   proto.String("name"),
				Number: proto.Int32(2),
				Type:   &stringType,
				Label:  &optionalLabel,
			},
		},
	}

	fileName := "row.proto"
	fileProto := &descriptorpb.FileDescriptorProto{
		Name:        proto.String(fileName),
		Syntax:      proto.String("proto2"),
		MessageType: []*descriptorpb.DescriptorProto{msg},
	}
	files, err := protodesc.NewFiles(&descriptorpb.FileDescriptorSet{
		File: []*descriptorpb.FileDescriptorProto{fileProto},
	})
	if err != nil {
		t.Fatalf("protodesc NewFiles: %v", err)
	}
	fileDesc, err := files.FindFileByPath(fileName)
	if err != nil {
		t.Fatalf("FindFileByPath: %v", err)
	}
	msgDesc := fileDesc.Messages().Get(0)
	return msg, msgDesc
}

func TestStorageWriteCommittedStreamProtoAppend(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	datasetID := "e2e_go_write"
	tableID := "target"
	teardown := setupWriteTable(t, ctx, datasetID, tableID)
	defer teardown()

	conn, err := grpc.NewClient(
		grpcEndpoint(),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("grpc.NewClient: %v", err)
	}
	defer conn.Close()

	client, err := storage.NewBigQueryWriteClient(
		ctx,
		option.WithGRPCConn(conn),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("NewBigQueryWriteClient: %v", err)
	}
	defer client.Close()

	// Create COMMITTED stream.
	parent := fmt.Sprintf(
		"projects/%s/datasets/%s/tables/%s",
		project(), datasetID, tableID,
	)
	stream, err := client.CreateWriteStream(ctx, &storagepb.CreateWriteStreamRequest{
		Parent:      parent,
		WriteStream: &storagepb.WriteStream{Type: storagepb.WriteStream_COMMITTED},
	})
	if err != nil {
		t.Fatalf("CreateWriteStream: %v", err)
	}

	// Encode two proto rows against the dynamic descriptor.
	descriptor, msgDesc := buildRowDescriptor(t)
	row1 := dynamicpb.NewMessage(msgDesc)
	row1.Set(msgDesc.Fields().ByName("id"), protoreflect.ValueOfInt64(1))
	row1.Set(msgDesc.Fields().ByName("name"), protoreflect.ValueOfString("alice"))
	row2 := dynamicpb.NewMessage(msgDesc)
	row2.Set(msgDesc.Fields().ByName("id"), protoreflect.ValueOfInt64(2))
	row2.Set(msgDesc.Fields().ByName("name"), protoreflect.ValueOfString("bob"))
	b1, _ := proto.Marshal(row1)
	b2, _ := proto.Marshal(row2)

	append, err := client.AppendRows(ctx)
	if err != nil {
		t.Fatalf("AppendRows: %v", err)
	}
	req := &storagepb.AppendRowsRequest{
		WriteStream: stream.Name,
		Rows: &storagepb.AppendRowsRequest_ProtoRows{
			ProtoRows: &storagepb.AppendRowsRequest_ProtoData{
				WriterSchema: &storagepb.ProtoSchema{ProtoDescriptor: descriptor},
				Rows:         &storagepb.ProtoRows{SerializedRows: [][]byte{b1, b2}},
			},
		},
	}
	if err := append.Send(req); err != nil {
		t.Fatalf("Send: %v", err)
	}
	if err := append.CloseSend(); err != nil {
		t.Fatalf("CloseSend: %v", err)
	}
	resp, err := append.Recv()
	if err != nil {
		t.Fatalf("Recv: %v", err)
	}
	if resp.GetError() != nil && resp.GetError().GetCode() != 0 {
		t.Fatalf("AppendRows returned error: %v", resp.GetError())
	}

	// Finalize isn't strictly required on COMMITTED but rows should be visible.
	if n := countRows(t, datasetID, tableID); n != 2 {
		t.Fatalf("expected 2 rows after append, got %d", n)
	}
}

// TestStorageWritePendingStreamCommit exercises the PENDING stream lifecycle:
// rows aren't visible until FinalizeWriteStream + BatchCommitWriteStreams.
// Mirrors Python's test_pending_stream_proto_batchcommit_e2e.
func TestStorageWritePendingStreamCommit(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	datasetID := "e2e_go_pending"
	tableID := "target"
	teardown := setupWriteTable(t, ctx, datasetID, tableID)
	defer teardown()

	conn, err := grpc.NewClient(
		grpcEndpoint(),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("grpc.NewClient: %v", err)
	}
	defer conn.Close()

	client, err := storage.NewBigQueryWriteClient(
		ctx,
		option.WithGRPCConn(conn),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("NewBigQueryWriteClient: %v", err)
	}
	defer client.Close()

	parent := fmt.Sprintf(
		"projects/%s/datasets/%s/tables/%s",
		project(), datasetID, tableID,
	)
	stream, err := client.CreateWriteStream(ctx, &storagepb.CreateWriteStreamRequest{
		Parent:      parent,
		WriteStream: &storagepb.WriteStream{Type: storagepb.WriteStream_PENDING},
	})
	if err != nil {
		t.Fatalf("CreateWriteStream(PENDING): %v", err)
	}

	descriptor, msgDesc := buildRowDescriptor(t)
	row1 := dynamicpb.NewMessage(msgDesc)
	row1.Set(msgDesc.Fields().ByName("id"), protoreflect.ValueOfInt64(10))
	row1.Set(msgDesc.Fields().ByName("name"), protoreflect.ValueOfString("pending-a"))
	row2 := dynamicpb.NewMessage(msgDesc)
	row2.Set(msgDesc.Fields().ByName("id"), protoreflect.ValueOfInt64(20))
	row2.Set(msgDesc.Fields().ByName("name"), protoreflect.ValueOfString("pending-b"))
	b1, _ := proto.Marshal(row1)
	b2, _ := proto.Marshal(row2)

	appendStream, err := client.AppendRows(ctx)
	if err != nil {
		t.Fatalf("AppendRows: %v", err)
	}
	if err := appendStream.Send(&storagepb.AppendRowsRequest{
		WriteStream: stream.Name,
		Rows: &storagepb.AppendRowsRequest_ProtoRows{
			ProtoRows: &storagepb.AppendRowsRequest_ProtoData{
				WriterSchema: &storagepb.ProtoSchema{ProtoDescriptor: descriptor},
				Rows:         &storagepb.ProtoRows{SerializedRows: [][]byte{b1, b2}},
			},
		},
	}); err != nil {
		t.Fatalf("Send: %v", err)
	}
	if err := appendStream.CloseSend(); err != nil {
		t.Fatalf("CloseSend: %v", err)
	}
	if _, err := appendStream.Recv(); err != nil {
		t.Fatalf("Recv: %v", err)
	}

	// Pending data is not visible until the BatchCommit step.
	if n := countRows(t, datasetID, tableID); n != 0 {
		t.Fatalf("PENDING stream should be invisible pre-commit, got %d rows", n)
	}

	if _, err := client.FinalizeWriteStream(ctx, &storagepb.FinalizeWriteStreamRequest{
		Name: stream.Name,
	}); err != nil {
		t.Fatalf("FinalizeWriteStream: %v", err)
	}

	commitParent := fmt.Sprintf("projects/%s/datasets/%s", project(), datasetID)
	commitResp, err := client.BatchCommitWriteStreams(ctx, &storagepb.BatchCommitWriteStreamsRequest{
		Parent:       commitParent,
		WriteStreams: []string{stream.Name},
	})
	if err != nil {
		t.Fatalf("BatchCommitWriteStreams: %v", err)
	}
	if len(commitResp.GetStreamErrors()) > 0 {
		t.Fatalf("BatchCommit errors: %v", commitResp.GetStreamErrors())
	}

	if n := countRows(t, datasetID, tableID); n != 2 {
		t.Fatalf("expected 2 rows after commit, got %d", n)
	}
}
