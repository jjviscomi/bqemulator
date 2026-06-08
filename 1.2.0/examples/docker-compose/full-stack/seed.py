"""Seed the demo dataset against the compose-hosted bqemulator."""

from __future__ import annotations

import os
import sys

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery


def main() -> None:
    project = os.environ.get("BQ_PROJECT", "bqemu-demo")
    dataset = os.environ.get("BQ_DATASET", "full_stack_demo")
    rest = os.environ.get("BQEMU_REST_URL", "http://localhost:9050")

    client = bigquery.Client(
        project=project,
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=rest),
    )
    try:
        ds = bigquery.Dataset(f"{project}.{dataset}")
        ds.location = "US"
        client.create_dataset(ds, exists_ok=True)
        table = bigquery.Table(
            f"{project}.{dataset}.customers",
            schema=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
            ],
        )
        client.create_table(table, exists_ok=True)
        errors = client.insert_rows_json(
            f"{project}.{dataset}.customers",
            [
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
                {"id": 3, "name": "Carol"},
            ],
        )
        if errors:
            print(f"insert errors: {errors}", file=sys.stderr)
            sys.exit(1)
        print(f"seeded {project}.{dataset}.customers")
    finally:
        client.close()


if __name__ == "__main__":
    main()
