"""Integration test: full ship-criterion workflow.

Create dataset → create table → insert rows → query → paginate results.
This is the Phase 1 ship criterion exercised via the real
google-cloud-bigquery Python client.
"""

from __future__ import annotations

import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def test_create_table_insert_query_paginate(bqemu_server: EmulatorServer) -> None:
    """Phase 1 ship criterion: end-to-end workflow."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    # 1. Create dataset.
    ds = client.create_dataset("sales")
    assert ds.dataset_id == "sales"

    # 2. Create table with typed schema.
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("amount", "NUMERIC"),
        bigquery.SchemaField("placed_at", "TIMESTAMP"),
    ]
    table_ref = "test-project.sales.orders"
    table = bigquery.Table(table_ref, schema=schema)
    created_table = client.create_table(table)
    assert created_table.table_id == "orders"
    assert len(created_table.schema) == 3

    # 3. Insert rows.
    rows = [
        {"id": 1, "amount": "12.50", "placed_at": "2026-04-15T00:00:00Z"},
        {"id": 2, "amount": "25.00", "placed_at": "2026-04-15T01:00:00Z"},
        {"id": 3, "amount": "7.99", "placed_at": "2026-04-15T02:00:00Z"},
    ]
    errors = client.insert_rows_json(created_table, rows)
    assert errors == []

    # 4. Query.
    query_sql = "SELECT id, amount FROM sales.orders ORDER BY id"
    query_job = client.query(query_sql)
    result_rows = list(query_job.result())

    assert len(result_rows) == 3
    assert result_rows[0].id == 1
    assert result_rows[1].id == 2
    assert result_rows[2].id == 3

    # 5. Aggregate query.
    agg_sql = "SELECT SUM(amount) AS total FROM sales.orders"
    agg_job = client.query(agg_sql)
    agg_rows = list(agg_job.result())
    assert len(agg_rows) == 1
    total = agg_rows[0].total
    # 12.50 + 25.00 + 7.99 = 45.49
    assert abs(float(total) - 45.49) < 0.01

    # 6. Get table metadata shows updated row count.
    refreshed = client.get_table(created_table)
    assert refreshed.num_rows >= 3

    # Cleanup.
    client.delete_dataset("sales", delete_contents=True)


def test_query_with_where_clause(bqemu_server: EmulatorServer) -> None:
    """Verify WHERE filtering works end-to-end."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    client.create_dataset("test_where")
    schema = [
        bigquery.SchemaField("name", "STRING"),
        bigquery.SchemaField("age", "INT64"),
    ]
    table = client.create_table(bigquery.Table("test-project.test_where.people", schema=schema))
    client.insert_rows_json(
        table,
        [
            {"name": "Alice", "age": "30"},
            {"name": "Bob", "age": "25"},
            {"name": "Carol", "age": "35"},
        ],
    )

    rows = list(
        client.query("SELECT name FROM test_where.people WHERE age > 28 ORDER BY name").result()
    )
    names = [r.name for r in rows]
    assert names == ["Alice", "Carol"]

    client.delete_dataset("test_where", delete_contents=True)
