"""Integration tests for DML in Phase 6's third SQL rule wave."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from bqemulator.config import PersistenceMode, Settings
from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        persistence_mode=PersistenceMode.EPHEMERAL,
        rest_port=0,
        grpc_port=0,
    )
    s = EmulatorServer(settings)
    await s.start()
    try:
        async with httpx.AsyncClient(base_url=s.rest_url, timeout=20.0) as c:
            await c.post(
                "/bigquery/v2/projects/p/datasets",
                json={"datasetReference": {"projectId": "p", "datasetId": "ds"}},
            )
            await c.post(
                "/bigquery/v2/projects/p/datasets/ds/tables",
                json={
                    "tableReference": {
                        "projectId": "p",
                        "datasetId": "ds",
                        "tableId": "t",
                    },
                    "schema": {
                        "fields": [
                            {"name": "id", "type": "INT64"},
                            {"name": "val", "type": "STRING"},
                        ],
                    },
                },
            )
            yield c
    finally:
        await s.stop()


async def _run(client: httpx.AsyncClient, sql: str) -> dict[str, object]:
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": sql, "useLegacySql": False},
    )
    r.raise_for_status()
    return r.json()


class TestDML:
    async def test_insert(self, client: httpx.AsyncClient) -> None:
        await _run(client, "INSERT INTO ds.t VALUES (1, 'a'), (2, 'b')")
        r = await _run(client, "SELECT COUNT(*) FROM ds.t")
        assert r["rows"][0]["f"][0]["v"] == "2"

    async def test_update(self, client: httpx.AsyncClient) -> None:
        await _run(client, "INSERT INTO ds.t VALUES (1, 'a'), (2, 'b')")
        await _run(client, "UPDATE ds.t SET val = 'x' WHERE id = 1")
        r = await _run(client, "SELECT val FROM ds.t WHERE id = 1")
        assert r["rows"][0]["f"][0]["v"] == "x"

    async def test_delete(self, client: httpx.AsyncClient) -> None:
        await _run(client, "INSERT INTO ds.t VALUES (1, 'a'), (2, 'b')")
        await _run(client, "DELETE FROM ds.t WHERE id = 1")
        r = await _run(client, "SELECT COUNT(*) FROM ds.t")
        assert r["rows"][0]["f"][0]["v"] == "1"

    async def test_truncate(self, client: httpx.AsyncClient) -> None:
        await _run(client, "INSERT INTO ds.t VALUES (1, 'a')")
        await _run(client, "TRUNCATE TABLE ds.t")
        r = await _run(client, "SELECT COUNT(*) FROM ds.t")
        assert r["rows"][0]["f"][0]["v"] == "0"


class TestWindow:
    async def test_window_with_frame(self, client: httpx.AsyncClient) -> None:
        await _run(
            client,
            "INSERT INTO ds.t VALUES (1,'a'), (2,'b'), (3,'c')",
        )
        r = await _run(
            client,
            "SELECT id, SUM(id) OVER (ORDER BY id ROWS BETWEEN "
            "UNBOUNDED PRECEDING AND CURRENT ROW) AS running "
            "FROM ds.t ORDER BY id",
        )
        running = [int(row["f"][1]["v"]) for row in r["rows"]]
        assert running == [1, 3, 6]

    async def test_nested_partition(self, client: httpx.AsyncClient) -> None:
        await _run(
            client,
            "INSERT INTO ds.t VALUES (1,'a'), (2,'a'), (3,'b')",
        )
        r = await _run(
            client,
            "SELECT id, ROW_NUMBER() OVER (PARTITION BY val ORDER BY id) AS rn "
            "FROM ds.t ORDER BY id",
        )
        assert len(r["rows"]) == 3


class TestUnnestOffset:
    async def test_unnest_with_offset(self, client: httpx.AsyncClient) -> None:
        r = await _run(
            client,
            "SELECT x, off FROM UNNEST([10, 20, 30]) AS x WITH OFFSET AS off ORDER BY off",
        )
        pairs = [(int(row["f"][0]["v"]), int(row["f"][1]["v"])) for row in r["rows"]]
        assert pairs == [(10, 0), (20, 1), (30, 2)]
