"""E2E: Phase 1 REST CRUD + query against a live container.

Exercises the full CRUD lifecycle (dataset -> table -> insertAll ->
query -> paginate -> cleanup) so we verify the published container
image speaks the same protocol as the official
``google-cloud-bigquery`` Python client.
"""

from __future__ import annotations

from collections.abc import Iterator

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import NotFound
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def bq_client(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    """BigQuery Python client bound to the emulator REST endpoint."""
    client = bigquery.Client(
        project="e2e-rest_crud",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_rest_url),
    )
    try:
        yield client
    finally:
        client.close()


def test_dataset_table_insert_query(bq_client: bigquery.Client) -> None:
    """Full dataset/table/insert/query loop matches the real-service shape."""
    ds_id = "e2e_ds1"
    tbl_id = "customers"

    # Create dataset.
    dataset = bigquery.Dataset(f"{bq_client.project}.{ds_id}")
    dataset.location = "US"
    try:
        bq_client.create_dataset(dataset, exists_ok=True)

        # Create table.
        schema = [
            bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("name", "STRING"),
            bigquery.SchemaField("email", "STRING"),
        ]
        table = bigquery.Table(
            f"{bq_client.project}.{ds_id}.{tbl_id}",
            schema=schema,
        )
        table = bq_client.create_table(table, exists_ok=True)

        # insertAll.
        rows = [
            {"id": 1, "name": "Alice", "email": "a@x.test"},
            {"id": 2, "name": "Bob", "email": "b@x.test"},
            {"id": 3, "name": "Carol", "email": "c@x.test"},
        ]
        errors = bq_client.insert_rows_json(table, rows)
        assert errors == []

        # Query.
        job = bq_client.query(f"SELECT COUNT(*) AS n FROM `{bq_client.project}.{ds_id}.{tbl_id}`")
        result = list(job.result())
        assert result[0]["n"] == 3

        # Parameterised query.
        job = bq_client.query(
            f"SELECT name FROM `{bq_client.project}.{ds_id}.{tbl_id}` WHERE id = @id",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("id", "INT64", 2)],
            ),
        )
        result = list(job.result())
        assert result[0]["name"] == "Bob"
    finally:
        bq_client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)


def test_tabledata_list_pagination(bq_client: bigquery.Client) -> None:
    """``tabledata.list`` pagination returns rows across multiple pages."""
    ds_id = "e2e_ds2"
    try:
        bq_client.create_dataset(
            bigquery.Dataset(f"{bq_client.project}.{ds_id}"),
            exists_ok=True,
        )
        table = bigquery.Table(
            f"{bq_client.project}.{ds_id}.paged",
            schema=[bigquery.SchemaField("id", "INT64")],
        )
        table = bq_client.create_table(table, exists_ok=True)
        rows = [{"id": i} for i in range(20)]
        assert bq_client.insert_rows_json(table, rows) == []

        iterator = bq_client.list_rows(table, max_results=5)
        seen = [row["id"] for row in iterator]
        assert len(seen) == 5
    finally:
        bq_client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)


def test_drop_table_via_query_removes_from_catalog(bq_client: bigquery.Client) -> None:
    """``DROP TABLE`` via jobs.query removes the table from the catalog.

    Real BigQuery makes a dropped table immediately invisible to
    ``tables.get`` (404) and ``tables.list``. Pins the DROP TABLE
    catalog-sync fix end-to-end through the Python SDK + REST surface.
    """
    ds_id = "e2e_ds_drop"
    tbl_id = "to_drop"
    table_ref = f"{bq_client.project}.{ds_id}.{tbl_id}"
    try:
        bq_client.create_dataset(
            bigquery.Dataset(f"{bq_client.project}.{ds_id}"),
            exists_ok=True,
        )
        bq_client.create_table(
            bigquery.Table(table_ref, schema=[bigquery.SchemaField("id", "INT64")]),
            exists_ok=True,
        )

        # Visible before the drop.
        assert bq_client.get_table(table_ref) is not None
        assert any(t.table_id == tbl_id for t in bq_client.list_tables(ds_id))

        # Drop via DDL submitted through jobs.query.
        bq_client.query(f"DROP TABLE `{table_ref}`").result()

        # Gone from tables.get (404) and tables.list, matching BigQuery.
        with pytest.raises(NotFound):
            bq_client.get_table(table_ref)
        assert all(t.table_id != tbl_id for t in bq_client.list_tables(ds_id))
    finally:
        bq_client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)
