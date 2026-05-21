"""E2E: G1 load Avro + extract Avro + load ORC against a live container.

AGENTS.md non-negotiable: every new feature gets four-language E2E.
This is the Python suite — Node/Go/Java siblings live next to it.

The container is configured (in :mod:`tests.e2e.conftest`) with
``BQEMU_GCS_LOCAL_ROOT=/var/lib/bqemu-gcs`` and a bind mount of a
session-scoped host directory onto that path. The canonical Avro+ORC
fixtures live at ``g1-e2e/load_{avro,orc}_basic.{avro,orc}`` — staged
by :mod:`scripts.stage_g1_e2e_fixtures` so all four languages exercise
the same bytes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import stat

import fastavro
from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
import httpx
import pytest

pytestmark = pytest.mark.e2e

_BUCKET = "g1-e2e"


@pytest.fixture
def bq_client(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    client = bigquery.Client(
        project="e2e-g1",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_rest_url),
    )
    try:
        yield client
    finally:
        client.close()


def _create_dataset_and_table(client: bigquery.Client, ds_id: str, tbl_id: str) -> None:
    dataset = bigquery.Dataset(f"{client.project}.{ds_id}")
    dataset.location = "US"
    client.create_dataset(dataset, exists_ok=True)
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("name", "STRING"),
    ]
    client.create_table(
        bigquery.Table(f"{client.project}.{ds_id}.{tbl_id}", schema=schema),
        exists_ok=True,
    )


def test_loads_avro_file_against_live_container(
    bq_client: bigquery.Client,
    bqemu_rest_url: str,
    bqemu_gcs_root_host: Path,
) -> None:
    """Load the canonical 3-row Avro file via jobs.insert."""
    ds_id = "g1_avro_load"
    _create_dataset_and_table(bq_client, ds_id, "items")

    try:
        r = httpx.post(
            f"{bqemu_rest_url}/bigquery/v2/projects/{bq_client.project}/jobs",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {
                            "projectId": bq_client.project,
                            "datasetId": ds_id,
                            "tableId": "items",
                        },
                        "sourceUris": [f"gs://{_BUCKET}/load_avro_basic.avro"],
                        "sourceFormat": "AVRO",
                    },
                },
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text

        rows = list(
            bq_client.query(f"SELECT id, name FROM {ds_id}.items ORDER BY id").result(),
        )
        assert len(rows) == 3
        assert rows[0].name == "alpha"
        assert rows[2].name == "gamma"
    finally:
        bq_client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)


def test_extracts_to_avro_against_live_container(
    bq_client: bigquery.Client,
    bqemu_rest_url: str,
    bqemu_gcs_root_host: Path,
) -> None:
    """Extract a 2-row table to .avro; verify file is readable by fastavro."""
    ds_id = "g1_avro_extract"
    _create_dataset_and_table(bq_client, ds_id, "src")
    table = bq_client.get_table(f"{bq_client.project}.{ds_id}.src")
    bq_client.insert_rows_json(
        table,
        [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}],
    )

    # Destination dir must be writable by the container's bqemu user.
    bucket_dir = bqemu_gcs_root_host / _BUCKET
    bucket_dir.mkdir(parents=True, exist_ok=True)
    bucket_dir.chmod(
        0o755 | stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH,
    )
    dest_uri = f"gs://{_BUCKET}/extract_python.avro"

    try:
        r = httpx.post(
            f"{bqemu_rest_url}/bigquery/v2/projects/{bq_client.project}/jobs",
            json={
                "configuration": {
                    "extract": {
                        "sourceTable": {
                            "projectId": bq_client.project,
                            "datasetId": ds_id,
                            "tableId": "src",
                        },
                        "destinationUris": [dest_uri],
                        "destinationFormat": "AVRO",
                    },
                },
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        host_file = bqemu_gcs_root_host / _BUCKET / "extract_python.avro"
        assert host_file.exists(), f"expected extract at {host_file}"
        with host_file.open("rb") as fh:
            records = list(fastavro.reader(fh))
        records.sort(key=lambda r: r["id"])
        assert [r["name"] for r in records] == ["alpha", "beta"]
    finally:
        bq_client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)


def test_loads_orc_file_against_live_container(
    bq_client: bigquery.Client,
    bqemu_rest_url: str,
    bqemu_gcs_root_host: Path,
) -> None:
    """Python sibling of the Java ORC E2E test (P4.e parity)."""
    ds_id = "g1_orc_load"
    _create_dataset_and_table(bq_client, ds_id, "items")

    try:
        r = httpx.post(
            f"{bqemu_rest_url}/bigquery/v2/projects/{bq_client.project}/jobs",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {
                            "projectId": bq_client.project,
                            "datasetId": ds_id,
                            "tableId": "items",
                        },
                        "sourceUris": [f"gs://{_BUCKET}/load_orc_basic.orc"],
                        "sourceFormat": "ORC",
                    },
                },
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text

        rows = list(
            bq_client.query(f"SELECT id, name FROM {ds_id}.items ORDER BY id").result(),
        )
        assert len(rows) == 3
        assert rows[1].name == "beta"
    finally:
        bq_client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)
