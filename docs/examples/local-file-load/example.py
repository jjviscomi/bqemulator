"""Runnable example: local-file loads against the emulator (G2).

Demonstrates the Python client idioms for uploading local data to
BigQuery — ``load_table_from_file`` and ``load_table_from_json`` — routed
through the emulator's ``/upload/bigquery/v2/...`` endpoints. CI executes
this script via ``make test`` so the example does not rot.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterator

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery

from bqemulator.config import PersistenceMode, Settings
from bqemulator.testing._thread_runner import ThreadedEmulator


PROJECT = "example-local-file-load"


def _emulator_url() -> Iterator[str]:
    """Yield the REST URL of either an existing or a freshly-started emulator.

    Reuses ``BIGQUERY_EMULATOR_HOST`` when set (CI / shared instance);
    otherwise spins up an in-process emulator on a random port and
    tears it down at exit.
    """
    existing = os.environ.get("BIGQUERY_EMULATOR_HOST")
    if existing:
        if not existing.startswith("http"):
            existing = f"http://{existing}"
        yield existing
        return

    settings = Settings(
        persistence_mode=PersistenceMode.EPHEMERAL,
        rest_port=0,
        grpc_port=0,
    )
    runner = ThreadedEmulator(settings)
    runner.start()
    try:
        yield runner.server.rest_url
    finally:
        runner.stop()


def main() -> None:
    """Run the example. Asserts the load round-trip succeeds."""
    for url in _emulator_url():
        client = bigquery.Client(
            project=PROJECT,
            credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
            client_options=ClientOptions(api_endpoint=url),
        )
        try:
            dataset = bigquery.Dataset(f"{PROJECT}.example")
            dataset.location = "US"
            client.create_dataset(dataset, exists_ok=True)
            table = bigquery.Table(
                f"{PROJECT}.example.customers",
                schema=[
                    bigquery.SchemaField("id", "INTEGER"),
                    bigquery.SchemaField("name", "STRING"),
                ],
            )
            client.create_table(table, exists_ok=True)

            csv_bytes = b"id,name\n1,alice\n2,bob\n3,carol\n"
            job_config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.CSV,
                skip_leading_rows=1,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                schema=[
                    bigquery.SchemaField("id", "INTEGER"),
                    bigquery.SchemaField("name", "STRING"),
                ],
            )
            # Passing ``size`` (under 5 MiB) makes the client send one
            # multipart request; without it the client uses the
            # resumable protocol. The emulator serves both.
            load_job = client.load_table_from_file(
                io.BytesIO(csv_bytes),
                f"{PROJECT}.example.customers",
                size=len(csv_bytes),
                job_config=job_config,
            )
            result = load_job.result(timeout=60)
            assert result.state == "DONE"

            # ``load_table_from_json`` serialises the rows to NDJSON and
            # uploads them the same way.
            json_job = client.load_table_from_json(
                [{"id": 4, "name": "dave"}, {"id": 5, "name": "erin"}],
                f"{PROJECT}.example.customers",
                job_config=bigquery.LoadJobConfig(
                    source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                    write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                ),
            )
            assert json_job.result(timeout=60).state == "DONE"

            rows = list(
                client.query(
                    f"SELECT COUNT(*) AS n FROM `{PROJECT}.example.customers`",
                ).result()
            )
            assert rows[0].n == 5, f"expected 5 rows, got {rows[0].n}"
            print(f"OK: loaded {rows[0].n} rows from a local BytesIO and JSON rows")
        finally:
            client.close()


if __name__ == "__main__":
    main()
