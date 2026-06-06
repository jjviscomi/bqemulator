"""E2E: ``EXPORT DATA`` → Cloud Storage (CSV) against a live container.

AGENTS.md non-negotiable: every new feature gets five-client E2E. This
is the Python suite — Node / Go / Java / ``bq`` CLI siblings live next
to it.

``EXPORT DATA`` runs as a QUERY job (``statementType`` ``EXPORT_DATA``):
the inner ``SELECT`` is materialised and written to the wildcard
``uri`` under ``BQEMU_GCS_LOCAL_ROOT``. With a single output shard the
``*`` expands to a 12-digit, zero-padded counter, so
``export_python/*.csv`` becomes ``export_python/000000000000.csv``. The
container (configured in :mod:`tests.e2e.conftest`) mounts the
session-scoped host GCS root at ``/var/lib/bqemu-gcs``, so this test
reads the exported file straight off the host mount — the same bytes
the executor wrote inside the container.
"""

from __future__ import annotations

from collections.abc import Iterator
import csv
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
import pytest

pytestmark = pytest.mark.e2e

_BUCKET = "g1-e2e"


@pytest.fixture
def bq_client(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    """A BigQuery client pointed at the live emulator container."""
    client = bigquery.Client(
        project="e2e-export",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_rest_url),
    )
    try:
        yield client
    finally:
        client.close()


def test_export_data_to_csv_against_live_container(
    bq_client: bigquery.Client,
    bqemu_gcs_root_host: Path,
) -> None:
    """Run ``EXPORT DATA`` as a query job; read the sharded CSV off the mount."""
    ds_id = "export_csv_ds"
    dataset = bigquery.Dataset(f"{bq_client.project}.{ds_id}")
    dataset.location = "US"
    # Start from a clean slate so reruns against a persistent emulator don't
    # append to a stale table and break the exact-CSV assertions.
    bq_client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)
    bq_client.create_dataset(dataset)

    try:
        table = bq_client.create_table(
            bigquery.Table(
                f"{bq_client.project}.{ds_id}.src",
                schema=[
                    bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
                    bigquery.SchemaField("name", "STRING"),
                ],
            ),
        )
        errors = bq_client.insert_rows_json(
            table,
            [
                {"id": 1, "name": "alpha"},
                {"id": 2, "name": "beta"},
                {"id": 3, "name": "gamma"},
            ],
        )
        assert errors == [], errors

        export_sql = (
            f"EXPORT DATA OPTIONS ("
            f"uri = 'gs://{_BUCKET}/export_python/*.csv', "
            f"format = 'CSV', "
            f"overwrite = true) AS "
            f"SELECT id, name FROM `{bq_client.project}.{ds_id}.src` ORDER BY id"
        )
        job = bq_client.query(export_sql)
        rows = list(job.result())  # block until the job is DONE
        assert rows == []
        assert job.statement_type == "EXPORT_DATA"

        shard = bqemu_gcs_root_host / _BUCKET / "export_python" / "000000000000.csv"
        assert shard.exists(), f"expected export shard at {shard}"
        with shard.open(newline="") as fh:
            csv_rows = list(csv.reader(fh))
        assert csv_rows[0] == ["id", "name"]
        assert csv_rows[1:] == [["1", "alpha"], ["2", "beta"], ["3", "gamma"]]
    finally:
        bq_client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)
