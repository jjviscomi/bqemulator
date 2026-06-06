"""Runnable example: ``EXPORT DATA`` → Cloud Storage (CSV).

Demonstrates the GoogleSQL ``EXPORT DATA OPTIONS(...) AS SELECT`` statement
against the emulator. ``gs://`` URIs resolve under ``BQEMU_GCS_LOCAL_ROOT``,
so the example starts an in-process emulator rooted at a temporary
directory, runs the export as a query job, then reads the exported file
straight off that directory and asserts its contents. CI executes this
script via ``make test`` so the example does not rot.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import tempfile

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery

from bqemulator.config import PersistenceMode, Settings
from bqemulator.testing._thread_runner import ThreadedEmulator


PROJECT = "example-export-to-gcs"
DATASET = "sales"
BUCKET = "my-bucket"


@contextmanager
def _emulator() -> Iterator[tuple[str, Path]]:
    """Start an in-process emulator rooted at a temp ``gs://`` directory.

    Yields the REST URL and the local path that ``gs://`` URIs resolve
    under (``BQEMU_GCS_LOCAL_ROOT``), then tears the emulator down and
    removes the directory on exit.
    """
    with tempfile.TemporaryDirectory(prefix="bqemu-gcs-") as gcs_root:
        settings = Settings(
            persistence_mode=PersistenceMode.EPHEMERAL,
            rest_port=0,
            grpc_port=0,
            gcs_local_root=Path(gcs_root),
        )
        runner = ThreadedEmulator(settings)
        runner.start()
        try:
            yield runner.server.rest_url, Path(gcs_root)
        finally:
            runner.stop()


def main() -> None:
    """Run the example. Asserts the export round-trip succeeds."""
    with _emulator() as (url, gcs_root):
        client = bigquery.Client(
            project=PROJECT,
            credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
            client_options=ClientOptions(api_endpoint=url),
        )
        try:
            dataset = bigquery.Dataset(f"{PROJECT}.{DATASET}")
            dataset.location = "US"
            client.create_dataset(dataset, exists_ok=True)

            client.query(
                f"CREATE TABLE IF NOT EXISTS `{PROJECT}.{DATASET}.customers` "
                "(id INT64, name STRING)"
            ).result()
            client.query(
                f"INSERT INTO `{PROJECT}.{DATASET}.customers` (id, name) "
                "VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma')"
            ).result()

            # A single '*' wildcard shards the output; a small result is
            # one file whose '*' expands to a 12-digit counter.
            export = client.query(
                "EXPORT DATA OPTIONS ("
                f"  uri = 'gs://{BUCKET}/exports/customers_*.csv',"
                "  format = 'CSV', overwrite = true) AS "
                f"SELECT id, name FROM `{PROJECT}.{DATASET}.customers` ORDER BY id"
            )
            export.result()  # blocks until the query job is DONE
            assert export.statement_type == "EXPORT_DATA", export.statement_type

            shard = gcs_root / BUCKET / "exports" / "customers_000000000000.csv"
            assert shard.exists(), f"expected export shard at {shard}"
            with shard.open(newline="") as fh:
                rows = list(csv.reader(fh))
            assert rows[0] == ["id", "name"], rows[0]
            assert rows[1:] == [["1", "alpha"], ["2", "beta"], ["3", "gamma"]], rows[1:]
            print(f"OK: exported {len(rows) - 1} rows to {shard.name}")
        finally:
            client.close()


if __name__ == "__main__":
    main()
