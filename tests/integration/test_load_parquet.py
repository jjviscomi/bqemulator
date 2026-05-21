"""Integration test: load from Parquet file."""

from __future__ import annotations

from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def test_load_parquet_file(bqemu_server: EmulatorServer, tmp_path: Path) -> None:
    """Load a Parquet file into a table and verify the data."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    client.create_dataset("pq_load")
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("val", "STRING"),
    ]
    client.create_table(
        bigquery.Table("test-project.pq_load.data", schema=schema),
    )

    # Write Parquet file.
    pq_path = tmp_path / "data.parquet"
    arrow_table = pa.table(
        {
            "id": pa.array([10, 20, 30], type=pa.int64()),
            "val": pa.array(["x", "y", "z"], type=pa.string()),
        }
    )
    pq.write_table(arrow_table, pq_path)

    # Load via jobs.insert.
    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "load": {
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "pq_load",
                        "tableId": "data",
                    },
                    "sourceUris": [str(pq_path)],
                    "sourceFormat": "PARQUET",
                },
            },
        },
        timeout=30,
    )
    assert r.status_code == 200

    rows = list(
        client.query("SELECT id, val FROM pq_load.data ORDER BY id").result(),
    )
    assert len(rows) == 3
    assert rows[0].id == 10
    assert rows[2].val == "z"

    client.delete_dataset("pq_load", delete_contents=True)


def test_load_unsupported_format_returns_error(
    bqemu_server: EmulatorServer,
    tmp_path: Path,
) -> None:
    """An unknown sourceFormat returns a clear 400.

    G1 (2026-05-20): AVRO and ORC are now supported; the original
    assertion that AVRO returns 501 was retired alongside the
    executor's UnsupportedFeatureError branch. This test now exercises
    a truly unknown format string so the "Unknown source format"
    InvalidQueryError branch keeps its coverage.
    """
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    client.create_dataset("avro_test")
    schema = [bigquery.SchemaField("x", "INT64")]
    client.create_table(
        bigquery.Table("test-project.avro_test.data", schema=schema),
    )

    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "load": {
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "avro_test",
                        "tableId": "data",
                    },
                    "sourceUris": [str(tmp_path / "fake.thrift")],
                    "sourceFormat": "THRIFT",
                },
            },
        },
        timeout=30,
    )
    assert r.status_code == 400

    client.delete_dataset("avro_test", delete_contents=True)
