"""Integration tests: Storage Read API.

Tests the gRPC BigQueryRead service using the actual
google-cloud-bigquery-storage Python client.
"""

from __future__ import annotations

import grpc
import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def _make_bq_client(bqemu_server: EmulatorServer):
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )


def _setup_test_table(bqemu_server: EmulatorServer) -> None:
    """Create a test dataset + table with data."""
    from google.cloud import bigquery

    client = _make_bq_client(bqemu_server)
    try:
        client.get_dataset("storage_read")
    except Exception:  # noqa: BLE001
        client.create_dataset("storage_read")
        schema = [
            bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("value", "STRING"),
        ]
        table = client.create_table(
            bigquery.Table("test-project.storage_read.data", schema=schema),
        )
        client.insert_rows_json(table, [{"id": i, "value": f"row_{i}"} for i in range(10)])


def test_create_read_session_via_grpc(bqemu_server: EmulatorServer) -> None:
    """Test CreateReadSession via raw gRPC."""
    _setup_test_table(bqemu_server)

    from google.cloud.bigquery_storage_v1 import types

    # Connect to the gRPC endpoint.
    channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)

    # Build CreateReadSession request.
    request = types.CreateReadSessionRequest(
        parent="projects/test-project",
        read_session=types.ReadSession(
            table="projects/test-project/datasets/storage_read/tables/data",
            data_format=types.DataFormat.ARROW,
        ),
        max_stream_count=2,
    )

    # Call the service.
    request_bytes = types.CreateReadSessionRequest.serialize(request)
    response_bytes = channel.unary_unary(
        "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
    )(request_bytes)

    # Deserialize response.
    session = types.ReadSession.deserialize(response_bytes)
    assert session.name.startswith("projects/test-project/")
    assert len(session.streams) >= 1
    assert session.arrow_schema.serialized_schema

    # Read rows from the first stream.
    read_request = types.ReadRowsRequest(
        read_stream=session.streams[0].name,
    )
    read_bytes = types.ReadRowsRequest.serialize(read_request)

    responses = list(
        channel.unary_stream(
            "/google.cloud.bigquery.storage.v1.BigQueryRead/ReadRows",
        )(read_bytes)
    )

    assert len(responses) >= 1

    # Deserialize and verify Arrow data.
    import pyarrow as pa

    total_rows = 0
    for resp_bytes in responses:
        resp = types.ReadRowsResponse.deserialize(resp_bytes)
        if resp.arrow_record_batch.serialized_record_batch:
            reader = pa.ipc.open_stream(resp.arrow_record_batch.serialized_record_batch)
            batch_table = reader.read_all()
            total_rows += batch_table.num_rows

    assert total_rows > 0

    channel.close()

    # Cleanup.
    client = _make_bq_client(bqemu_server)
    client.delete_dataset("storage_read", delete_contents=True)


def test_create_read_session_with_projection(bqemu_server: EmulatorServer) -> None:
    """Test column projection in CreateReadSession."""
    _setup_test_table(bqemu_server)

    from google.cloud.bigquery_storage_v1 import types

    channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)

    request = types.CreateReadSessionRequest(
        parent="projects/test-project",
        read_session=types.ReadSession(
            table="projects/test-project/datasets/storage_read/tables/data",
            data_format=types.DataFormat.ARROW,
            read_options=types.ReadSession.TableReadOptions(
                selected_fields=["value"],
            ),
        ),
        max_stream_count=1,
    )

    response_bytes = channel.unary_unary(
        "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
    )(types.CreateReadSessionRequest.serialize(request))

    session = types.ReadSession.deserialize(response_bytes)
    assert len(session.streams) >= 1

    # Read and verify only the projected column is present.
    read_request = types.ReadRowsRequest(read_stream=session.streams[0].name)
    responses = list(
        channel.unary_stream(
            "/google.cloud.bigquery.storage.v1.BigQueryRead/ReadRows",
        )(types.ReadRowsRequest.serialize(read_request))
    )

    import pyarrow as pa

    for resp_bytes in responses:
        resp = types.ReadRowsResponse.deserialize(resp_bytes)
        if resp.arrow_record_batch.serialized_record_batch:
            reader = pa.ipc.open_stream(resp.arrow_record_batch.serialized_record_batch)
            batch_table = reader.read_all()
            assert batch_table.column_names == ["value"]

    channel.close()
    _make_bq_client(bqemu_server).delete_dataset("storage_read", delete_contents=True)
