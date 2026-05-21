"""E2E: G4 INFORMATION_SCHEMA virtual views against a live container.

Two tests verify the canonical IS surfaces that dbt/Looker/Dataform
emit constantly: scanning ``INFORMATION_SCHEMA.TABLES`` for a
dataset's tables, and scanning ``INFORMATION_SCHEMA.COLUMNS`` for a
table's columns in ordinal-position order.
"""

from __future__ import annotations

from collections.abc import Iterator

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def bq_client(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    client = bigquery.Client(
        project="e2e-g4",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_rest_url),
    )
    try:
        yield client
    finally:
        client.close()


def test_information_schema_tables_lists_base_tables(
    bq_client: bigquery.Client,
) -> None:
    ds_id = "g4_tables_ds"
    dataset = bigquery.Dataset(f"{bq_client.project}.{ds_id}")
    bq_client.create_dataset(dataset, exists_ok=True)
    try:
        for tbl in ("orders", "customers"):
            table = bigquery.Table(
                f"{bq_client.project}.{ds_id}.{tbl}",
                schema=[bigquery.SchemaField("id", "INT64")],
            )
            bq_client.create_table(table, exists_ok=True)

        job = bq_client.query(
            f"SELECT table_name FROM `{bq_client.project}.{ds_id}`.INFORMATION_SCHEMA.TABLES "
            "WHERE table_type = 'BASE TABLE' ORDER BY table_name",
        )
        names = [row["table_name"] for row in job.result()]
        assert names == ["customers", "orders"]
    finally:
        bq_client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)


def test_information_schema_columns_ordered_by_ordinal_position(
    bq_client: bigquery.Client,
) -> None:
    ds_id = "g4_cols_ds"
    dataset = bigquery.Dataset(f"{bq_client.project}.{ds_id}")
    bq_client.create_dataset(dataset, exists_ok=True)
    try:
        table = bigquery.Table(
            f"{bq_client.project}.{ds_id}.events",
            schema=[
                bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
                bigquery.SchemaField("ts", "TIMESTAMP"),
                bigquery.SchemaField("payload", "STRING"),
            ],
        )
        bq_client.create_table(table, exists_ok=True)

        job = bq_client.query(
            f"SELECT column_name, data_type "
            f"FROM `{bq_client.project}.{ds_id}`.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE table_name = 'events' ORDER BY ordinal_position",
        )
        rows = list(job.result())
        assert [r["column_name"] for r in rows] == ["id", "ts", "payload"]
        assert rows[0]["data_type"] == "INT64"
        assert rows[1]["data_type"] == "TIMESTAMP"
        assert rows[2]["data_type"] == "STRING"
    finally:
        bq_client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)
