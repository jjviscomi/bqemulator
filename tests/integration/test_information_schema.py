"""Integration tests for G4 INFORMATION_SCHEMA views.

Covers SCHEMATA / TABLES / COLUMNS / TABLE_OPTIONS / VIEWS / PARTITIONS
end-to-end against an in-process emulator. Each view is exercised
through a full create-then-query round trip so the catalog write +
rewriter expansion + DuckDB execution all interlock.
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
async def running_server() -> AsyncIterator[EmulatorServer]:
    settings = Settings(
        persistence_mode=PersistenceMode.EPHEMERAL,
        rest_port=0,
        grpc_port=0,
    )
    s = EmulatorServer(settings)
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


@pytest_asyncio.fixture
async def client(running_server: EmulatorServer) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=running_server.rest_url, timeout=15.0) as c:
        await c.post(
            "/bigquery/v2/projects/p/datasets",
            json={"datasetReference": {"projectId": "p", "datasetId": "ds"}},
        )
        yield c


async def _query(client: httpx.AsyncClient, sql: str) -> dict[str, object]:
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": sql, "useLegacySql": False},
    )
    r.raise_for_status()
    return r.json()


async def _create_table(
    client: httpx.AsyncClient,
    table_id: str,
    schema_fields: list[dict[str, object]],
    *,
    description: str | None = None,
    time_partitioning: dict[str, object] | None = None,
) -> None:
    body: dict[str, object] = {
        "tableReference": {
            "projectId": "p",
            "datasetId": "ds",
            "tableId": table_id,
        },
        "schema": {"fields": schema_fields},
    }
    if description is not None:
        body["description"] = description
    if time_partitioning is not None:
        body["timePartitioning"] = time_partitioning
    r = await client.post(
        "/bigquery/v2/projects/p/datasets/ds/tables",
        json=body,
    )
    r.raise_for_status()


class TestSchemata:
    async def test_schemata_lists_datasets(self, client: httpx.AsyncClient) -> None:
        await client.post(
            "/bigquery/v2/projects/p/datasets",
            json={"datasetReference": {"projectId": "p", "datasetId": "ds2"}},
        )
        resp = await _query(
            client,
            "SELECT schema_name FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY schema_name",
        )
        names = [row["f"][0]["v"] for row in resp["rows"]]
        assert "ds" in names
        assert "ds2" in names


class TestTables:
    async def test_tables_appears_after_create(self, client: httpx.AsyncClient) -> None:
        await _create_table(
            client,
            "orders",
            [{"name": "id", "type": "INT64"}],
        )
        resp = await _query(
            client,
            "SELECT table_name, table_type FROM ds.INFORMATION_SCHEMA.TABLES "
            "WHERE table_name = 'orders'",
        )
        assert len(resp["rows"]) == 1
        assert resp["rows"][0]["f"][0]["v"] == "orders"
        assert resp["rows"][0]["f"][1]["v"] == "BASE TABLE"


class TestColumns:
    async def test_columns_reflects_schema(self, client: httpx.AsyncClient) -> None:
        await _create_table(
            client,
            "events",
            [
                {"name": "id", "type": "INT64", "mode": "REQUIRED"},
                {"name": "name", "type": "STRING"},
                {
                    "name": "addr",
                    "type": "RECORD",
                    "fields": [
                        {"name": "city", "type": "STRING"},
                        {"name": "zip", "type": "INT64"},
                    ],
                },
            ],
        )
        resp = await _query(
            client,
            "SELECT column_name, ordinal_position, data_type "
            "FROM ds.INFORMATION_SCHEMA.COLUMNS "
            "WHERE table_name = 'events' "
            "ORDER BY ordinal_position",
        )
        rows = resp["rows"]
        assert len(rows) == 3
        # ordinal_position drives the order
        assert rows[0]["f"][0]["v"] == "id"
        assert rows[0]["f"][1]["v"] == "1"
        assert rows[0]["f"][2]["v"] == "INT64"
        assert rows[1]["f"][0]["v"] == "name"
        assert rows[2]["f"][0]["v"] == "addr"
        # struct rendering
        assert "STRUCT<city STRING, zip INT64>" in rows[2]["f"][2]["v"]


class TestTableOptions:
    async def test_table_options_reflects_description(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        await _create_table(
            client,
            "described",
            [{"name": "id", "type": "INT64"}],
            description="orders fact table",
        )
        resp = await _query(
            client,
            "SELECT option_name, option_value "
            "FROM ds.INFORMATION_SCHEMA.TABLE_OPTIONS "
            "WHERE table_name = 'described'",
        )
        rows = resp["rows"]
        assert any(
            row["f"][0]["v"] == "description" and "orders fact table" in row["f"][1]["v"]
            for row in rows
        )


class TestViews:
    async def test_views_appears_after_create_view(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        # Create a base table + a view over it via SQL DDL (the
        # canonical BQ way to create a view through queries).
        await _query(
            client,
            "CREATE TABLE ds.src_for_view (id INT64)",
        )
        await _query(
            client,
            "CREATE VIEW ds.v_src AS SELECT id FROM ds.src_for_view",
        )
        resp = await _query(
            client,
            "SELECT table_name, use_standard_sql "
            "FROM ds.INFORMATION_SCHEMA.VIEWS "
            "WHERE table_name = 'v_src'",
        )
        rows = resp["rows"]
        assert len(rows) == 1
        assert rows[0]["f"][0]["v"] == "v_src"
        assert rows[0]["f"][1]["v"] == "YES"


class TestPartitions:
    async def test_partitions_appears_for_partitioned_table(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        await _create_table(
            client,
            "events_p",
            [{"name": "dt", "type": "DATE"}, {"name": "value", "type": "INT64"}],
            time_partitioning={"type": "DAY", "field": "dt"},
        )
        # Seed two partitions
        await _query(
            client,
            "INSERT INTO ds.events_p VALUES "
            "(DATE '2026-05-20', 1), (DATE '2026-05-20', 2), "
            "(DATE '2026-05-21', 3)",
        )
        resp = await _query(
            client,
            "SELECT partition_id, total_rows "
            "FROM ds.INFORMATION_SCHEMA.PARTITIONS "
            "WHERE table_name = 'events_p' "
            "ORDER BY partition_id",
        )
        rows = resp["rows"]
        # Two day-partitions, neither empty
        partition_ids = [row["f"][0]["v"] for row in rows]
        assert "20260520" in partition_ids
        assert "20260521" in partition_ids
        by_pid = {row["f"][0]["v"]: int(row["f"][1]["v"]) for row in rows}
        assert by_pid["20260520"] == 2
        assert by_pid["20260521"] == 1
