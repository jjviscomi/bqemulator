"""Integration test: table CRUD via the google-cloud-bigquery client."""

from __future__ import annotations

import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def test_create_get_patch_delete_table(bqemu_server: EmulatorServer) -> None:
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    # Setup
    client.create_dataset("tbl_test")

    # Create table
    schema = [
        bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("score", "FLOAT64"),
    ]
    table = client.create_table(
        bigquery.Table("test-project.tbl_test.scores", schema=schema),
    )
    assert table.table_id == "scores"
    assert len(table.schema) == 2

    # Get table
    fetched = client.get_table("test-project.tbl_test.scores")
    assert fetched.table_id == "scores"

    # Patch table (update description)
    fetched.description = "Score board"
    patched = client.update_table(fetched, ["description"])
    assert patched.description == "Score board"

    # List tables
    tables = list(client.list_tables("tbl_test"))
    assert any(t.table_id == "scores" for t in tables)

    # Delete table
    client.delete_table("test-project.tbl_test.scores")
    tables_after = list(client.list_tables("tbl_test"))
    assert all(t.table_id != "scores" for t in tables_after)

    # Cleanup
    client.delete_dataset("tbl_test", delete_contents=True)


def test_insert_and_read_tabledata(bqemu_server: EmulatorServer) -> None:
    """Test tabledata.insertAll + tabledata.list."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    client.create_dataset("read_test")
    schema = [
        bigquery.SchemaField("x", "INT64"),
        bigquery.SchemaField("y", "STRING"),
    ]
    table = client.create_table(
        bigquery.Table("test-project.read_test.data", schema=schema),
    )

    # Insert
    rows = [{"x": "1", "y": "hello"}, {"x": "2", "y": "world"}]
    errors = client.insert_rows_json(table, rows)
    assert errors == []

    # Read via tabledata.list
    row_iter = client.list_rows(table)
    all_rows = list(row_iter)
    assert len(all_rows) == 2

    # Read via query
    result = list(client.query("SELECT x, y FROM read_test.data ORDER BY x").result())
    assert result[0].x == 1
    assert result[0].y == "hello"

    client.delete_dataset("read_test", delete_contents=True)
