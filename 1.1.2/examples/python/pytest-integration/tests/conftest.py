"""Wire the emulator-backed ``bigquery.Client`` into the Flask app.

The ``bqemu_client`` fixture is contributed by the ``bqemulator`` pytest
plugin (registered via ``pyproject.toml`` entry point on install). Tests
do not need to know how the emulator is started — they receive a ready
client and seed their own data.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from app import create_app


@pytest.fixture
def dataset(bqemu_client: bigquery.Client) -> Iterator[str]:
    """Per-test dataset, ensuring isolation under a session-scoped server."""
    name = f"demo_{uuid.uuid4().hex[:8]}"
    bqemu_client.create_dataset(
        bigquery.Dataset(f"{bqemu_client.project}.{name}")
    )
    try:
        yield name
    finally:
        bqemu_client.delete_dataset(
            f"{bqemu_client.project}.{name}",
            delete_contents=True,
            not_found_ok=True,
        )


@pytest.fixture
def seeded(bqemu_client: bigquery.Client, dataset: str) -> str:
    """Create ``customers`` table and seed three rows via ``insert_rows``."""
    table_id = f"{bqemu_client.project}.{dataset}.customers"
    table = bigquery.Table(
        table_id,
        schema=[
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("name", "STRING"),
        ],
    )
    bqemu_client.create_table(table)
    errors = bqemu_client.insert_rows_json(
        table_id,
        [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 3, "name": "Carol"},
        ],
    )
    assert not errors, errors
    return dataset


@pytest.fixture
def app_client(bqemu_client: bigquery.Client, seeded: str):
    """Flask test client bound to the emulator-backed BigQuery client."""
    app = create_app(bqemu_client, dataset=seeded)
    app.config["TESTING"] = True
    return app.test_client()
