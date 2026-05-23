"""E2E: Phase 4 Storage Read API against a live container.

Verifies the full gRPC wire protocol — CreateReadSession + ReadRows
over a real channel, using the proto-plus types shipped by
``google-cloud-bigquery-storage``.
"""

from __future__ import annotations

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
import grpc
import pyarrow as pa
import pytest

pytestmark = pytest.mark.e2e

_READ = "/google.cloud.bigquery.storage.v1.BigQueryRead"


def _bq_client(rest_url: str) -> bigquery.Client:
    return bigquery.Client(
        project="e2e-read",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=rest_url),
    )


def _seed(rest_url: str) -> None:
    client = _bq_client(rest_url)
    try:
        client.create_dataset(
            bigquery.Dataset(f"{client.project}.reads"),
            exists_ok=True,
        )
        table = bigquery.Table(
            f"{client.project}.reads.t",
            schema=[
                bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("score", "INT64"),
            ],
        )
        client.create_table(table, exists_ok=True)
        rows = [
            {"id": 1, "name": "Alice", "score": 90},
            {"id": 2, "name": "Bob", "score": 70},
            {"id": 3, "name": "Carol", "score": 85},
        ]
        client.insert_rows_json(table, rows)
    finally:
        client.close()


def _cleanup(rest_url: str) -> None:
    client = _bq_client(rest_url)
    try:
        client.delete_dataset("reads", delete_contents=True, not_found_ok=True)
    finally:
        client.close()


def test_create_read_session_and_read_rows(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """Create a session, read it back, verify every row made the round-trip."""
    from google.cloud.bigquery_storage_v1 import types

    _seed(bqemu_rest_url)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            req = types.CreateReadSessionRequest(
                parent="projects/e2e-read",
                read_session=types.ReadSession(
                    table="projects/e2e-read/datasets/reads/tables/t",
                    data_format=types.DataFormat.ARROW,
                ),
                max_stream_count=2,
            )
            resp_bytes = channel.unary_unary(f"{_READ}/CreateReadSession")(
                types.CreateReadSessionRequest.serialize(req),
            )
            session = types.ReadSession.deserialize(resp_bytes)
            assert len(session.streams) >= 1

            schema = pa.ipc.open_stream(session.arrow_schema.serialized_schema).schema
            total = 0
            for stream in session.streams:
                read_req = types.ReadRowsRequest(read_stream=stream.name)
                for resp_bytes in channel.unary_stream(f"{_READ}/ReadRows")(
                    types.ReadRowsRequest.serialize(read_req),
                ):
                    resp = types.ReadRowsResponse.deserialize(resp_bytes)
                    if resp.arrow_record_batch.serialized_record_batch:
                        batch = pa.ipc.read_record_batch(
                            resp.arrow_record_batch.serialized_record_batch,
                            schema,
                        )
                        total += batch.num_rows
            assert total == 3
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url)


def test_row_filter_pushdown(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """Row restriction applied on the server side filters correctly."""
    from google.cloud.bigquery_storage_v1 import types

    _seed(bqemu_rest_url)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            req = types.CreateReadSessionRequest(
                parent="projects/e2e-read",
                read_session=types.ReadSession(
                    table="projects/e2e-read/datasets/reads/tables/t",
                    data_format=types.DataFormat.ARROW,
                    read_options=types.ReadSession.TableReadOptions(
                        row_restriction="score >= 85",
                    ),
                ),
                max_stream_count=1,
            )
            resp = types.ReadSession.deserialize(
                channel.unary_unary(f"{_READ}/CreateReadSession")(
                    types.CreateReadSessionRequest.serialize(req),
                ),
            )
            schema = pa.ipc.open_stream(resp.arrow_schema.serialized_schema).schema
            total = 0
            read_req = types.ReadRowsRequest(read_stream=resp.streams[0].name)
            for resp_bytes in channel.unary_stream(f"{_READ}/ReadRows")(
                types.ReadRowsRequest.serialize(read_req),
            ):
                rr = types.ReadRowsResponse.deserialize(resp_bytes)
                if rr.arrow_record_batch.serialized_record_batch:
                    batch = pa.ipc.read_record_batch(
                        rr.arrow_record_batch.serialized_record_batch,
                        schema,
                    )
                    total += batch.num_rows
            # Alice (90) + Carol (85) pass, Bob (70) filtered.
            assert total == 2
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url)
