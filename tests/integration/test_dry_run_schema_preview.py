"""Integration tests for dry-run schema preview on jobs.insert.

BigQuery's ``dryRun=true`` flag returns the schema of the table that
*would* be created (for ``CREATE TABLE``) or the destination table's
pre-mutation schema (for ``INSERT/UPDATE/DELETE/MERGE``). These tests
cover the sqlglot-driven AST walker in
``bqemulator.api.routes.jobs._dry_run_preview_schema`` and friends.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from bqemulator.config import PersistenceMode, Settings
from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def server() -> AsyncIterator[EmulatorServer]:
    s = EmulatorServer(
        Settings(
            persistence_mode=PersistenceMode.EPHEMERAL,
            rest_port=0,
            grpc_port=0,
        )
    )
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


@pytest_asyncio.fixture
async def client(server: EmulatorServer) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=server.rest_url, timeout=20.0) as c:
        await c.post(
            "/bigquery/v2/projects/p/datasets",
            json={"datasetReference": {"projectId": "p", "datasetId": "ds"}},
        )
        yield c


async def _dry_run(client: httpx.AsyncClient, sql: str) -> dict:
    """POST a dry-run job and return the schema fields on the job-config response."""
    r = await client.post(
        "/bigquery/v2/projects/p/jobs",
        json={
            "configuration": {
                "dryRun": True,
                "query": {"query": sql, "useLegacySql": False},
            }
        },
    )
    r.raise_for_status()
    return r.json()


async def _create_orders_table(client: httpx.AsyncClient) -> None:
    await client.post(
        "/bigquery/v2/projects/p/datasets/ds/tables",
        json={
            "tableReference": {"projectId": "p", "datasetId": "ds", "tableId": "orders"},
            "schema": {
                "fields": [
                    {"name": "id", "type": "INTEGER", "mode": "REQUIRED"},
                    {"name": "customer", "type": "STRING"},
                    {"name": "amount", "type": "NUMERIC"},
                ]
            },
        },
    )


class TestDryRunCreateTable:
    """``CREATE TABLE`` dry-run returns the schema of the table that would
    be created — exercises ``_schema_from_create_table`` + ``_column_def_to_bq_type``.
    """

    async def test_create_table_returns_declared_schema(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        sql = "CREATE TABLE ds.new_orders (  id INT64,   customer STRING,   amount NUMERIC)"
        resp = await _dry_run(client, sql)
        fields = resp.get("statistics", {}).get("query", {}).get("schema", {}).get("fields", [])
        names = {f["name"] for f in fields}
        # The exact subset present depends on the translator's AST shape;
        # assert at least one declared column surfaces (validates the
        # ``_schema_from_create_table`` walk reaches the column-def AST).
        assert {"id", "customer", "amount"}.issubset(names)


class TestDryRunDmlSchemaPreview:
    """``INSERT/UPDATE/DELETE/MERGE`` dry-run returns the destination's
    pre-mutation schema — exercises ``_destination_table_from_dml`` +
    ``_table_meta_schema_to_response``.
    """

    async def test_insert_returns_destination_schema(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        await _create_orders_table(client)
        sql = "INSERT INTO ds.orders (id, customer, amount) VALUES (1, 'Alice', 100)"
        resp = await _dry_run(client, sql)
        fields = resp.get("statistics", {}).get("query", {}).get("schema", {}).get("fields", [])
        names = {f["name"] for f in fields}
        # Pre-mutation schema → the destination table's existing columns.
        assert names == {"id", "customer", "amount"}

    async def test_delete_returns_destination_schema(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        await _create_orders_table(client)
        sql = "DELETE FROM ds.orders WHERE id = 1"
        resp = await _dry_run(client, sql)
        fields = resp.get("statistics", {}).get("query", {}).get("schema", {}).get("fields", [])
        names = {f["name"] for f in fields}
        assert names == {"id", "customer", "amount"}

    async def test_update_returns_destination_schema(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        await _create_orders_table(client)
        sql = "UPDATE ds.orders SET customer = 'Bob' WHERE id = 1"
        resp = await _dry_run(client, sql)
        fields = resp.get("statistics", {}).get("query", {}).get("schema", {}).get("fields", [])
        assert {"id", "customer", "amount"}.issubset({f["name"] for f in fields})

    async def test_dml_on_missing_table_falls_back(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        # The DML path falls back to the 0-column preview when the
        # destination doesn't exist in the catalog. Should not 500.
        sql = "DELETE FROM ds.nonexistent_table WHERE id = 1"
        resp = await _dry_run(client, sql)
        # Job still succeeds as a dry-run; the schema is empty / absent.
        # The point of this test is that the path doesn't crash.
        assert "id" in resp
