"""E2E: G3 Storage Read API Avro wire format against a live container.

Mirrors :mod:`tests.e2e.python_client.test_storage_read` but
asks the BQ Storage Read client for AVRO. Two tests:

* explicit-Avro request → assert decoded rows via fastavro.
* round-trip to disk → write rows into an Avro Object Container File,
  re-read with ``fastavro.reader``, assert decoded equality.

The decoded-row assertion (not just byte length) is the load-bearing
guardrail per ADR 0030: bytes that proto-validate but no Avro decoder
accepts would otherwise slip through.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import fastavro
from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
import grpc
import pytest

pytestmark = pytest.mark.e2e

_READ = "/google.cloud.bigquery.storage.v1.BigQueryRead"


def _bq_client(rest_url: str) -> bigquery.Client:
    return bigquery.Client(
        project="e2e-py-avro",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=rest_url),
    )


def _seed(rest_url: str) -> None:
    client = _bq_client(rest_url)
    try:
        client.create_dataset(
            bigquery.Dataset(f"{client.project}.avro_reads"),
            exists_ok=True,
        )
        table = bigquery.Table(
            f"{client.project}.avro_reads.t",
            schema=[
                bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("score", "INT64"),
            ],
        )
        client.create_table(table, exists_ok=True)
        client.insert_rows_json(
            table,
            [
                {"id": 1, "name": "Alice", "score": 90},
                {"id": 2, "name": "Bob", "score": 70},
                {"id": 3, "name": "Carol", "score": 85},
            ],
        )
    finally:
        client.close()


def _cleanup(rest_url: str) -> None:
    client = _bq_client(rest_url)
    try:
        client.delete_dataset("avro_reads", delete_contents=True, not_found_ok=True)
    finally:
        client.close()


def test_avro_session_decodes_via_fastavro(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
) -> None:
    """Explicit AVRO request → rows decode and equal the seed data."""
    from google.cloud.bigquery_storage_v1 import types

    _seed(bqemu_rest_url)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            req = types.CreateReadSessionRequest(
                parent="projects/e2e-py-avro",
                read_session=types.ReadSession(
                    table="projects/e2e-py-avro/datasets/avro_reads/tables/t",
                    data_format=types.DataFormat.AVRO,
                ),
                max_stream_count=1,
            )
            session = types.ReadSession.deserialize(
                channel.unary_unary(f"{_READ}/CreateReadSession")(
                    types.CreateReadSessionRequest.serialize(req),
                ),
            )
            assert session.data_format == types.DataFormat.AVRO
            schema_json = session.avro_schema.schema
            assert schema_json
            parsed = fastavro.parse_schema(json.loads(schema_json))

            decoded_rows: list[dict] = []
            for stream in session.streams:
                read_req = types.ReadRowsRequest(read_stream=stream.name)
                for resp_bytes in channel.unary_stream(f"{_READ}/ReadRows")(
                    types.ReadRowsRequest.serialize(read_req),
                ):
                    resp = types.ReadRowsResponse.deserialize(resp_bytes)
                    payload = resp.avro_rows.serialized_binary_rows
                    if not payload:
                        continue
                    reader = io.BytesIO(payload)
                    decoded_rows.extend(
                        fastavro.schemaless_reader(reader, parsed) for _ in range(resp.row_count)
                    )

            assert len(decoded_rows) == 3
            by_id = {r["id"]: r for r in decoded_rows}
            assert by_id[1]["name"] == "Alice"
            assert by_id[1]["score"] == 90
            assert by_id[3]["name"] == "Carol"
        finally:
            channel.close()
    finally:
        _cleanup(bqemu_rest_url)


def test_avro_round_trip_to_disk(
    bqemu_rest_url: str,
    bqemu_grpc_endpoint: str,
    tmp_path: Path,
) -> None:
    """Read Avro from the wire → write OCF to disk → re-read → assert."""
    from google.cloud.bigquery_storage_v1 import types

    _seed(bqemu_rest_url)
    try:
        channel = grpc.insecure_channel(bqemu_grpc_endpoint)
        try:
            req = types.CreateReadSessionRequest(
                parent="projects/e2e-py-avro",
                read_session=types.ReadSession(
                    table="projects/e2e-py-avro/datasets/avro_reads/tables/t",
                    data_format=types.DataFormat.AVRO,
                ),
                max_stream_count=1,
            )
            session = types.ReadSession.deserialize(
                channel.unary_unary(f"{_READ}/CreateReadSession")(
                    types.CreateReadSessionRequest.serialize(req),
                ),
            )
            parsed = fastavro.parse_schema(json.loads(session.avro_schema.schema))

            rows: list[dict] = []
            for stream in session.streams:
                read_req = types.ReadRowsRequest(read_stream=stream.name)
                for resp_bytes in channel.unary_stream(f"{_READ}/ReadRows")(
                    types.ReadRowsRequest.serialize(read_req),
                ):
                    resp = types.ReadRowsResponse.deserialize(resp_bytes)
                    payload = resp.avro_rows.serialized_binary_rows
                    if not payload:
                        continue
                    reader = io.BytesIO(payload)
                    rows.extend(
                        fastavro.schemaless_reader(reader, parsed) for _ in range(resp.row_count)
                    )
        finally:
            channel.close()

        # Materialise into an OCF on disk; assert the round-trip works.
        ocf_path = tmp_path / "e2e_dump.avro"
        with ocf_path.open("wb") as fh:
            fastavro.writer(fh, parsed, rows)
        assert ocf_path.read_bytes().startswith(b"Obj\x01")
        with ocf_path.open("rb") as fh:
            decoded = list(fastavro.reader(fh))
        assert decoded == rows
        assert len(decoded) == 3
    finally:
        _cleanup(bqemu_rest_url)
