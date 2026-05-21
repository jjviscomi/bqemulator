"""Integration tests for ``CREATE TABLE ... CLONE``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

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


async def _run(client: httpx.AsyncClient, sql: str) -> dict[str, Any]:
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": sql, "useLegacySql": False},
    )
    r.raise_for_status()
    return r.json()


def _rows(payload: dict[str, Any]) -> list[list[str]]:
    return [[c["v"] for c in row["f"]] for row in payload.get("rows", [])]


async def test_clone_copies_rows_and_diverges_independently(
    client: httpx.AsyncClient,
) -> None:
    await _run(client, "INSERT INTO ds.t VALUES (1, 'a'), (2, 'b')")
    await _run(client, "CREATE TABLE ds.workcopy CLONE ds.t")

    # Mutate the clone.
    await _run(client, "INSERT INTO ds.workcopy VALUES (99, 'z')")

    src_rows = await _run(client, "SELECT id FROM ds.t ORDER BY id")
    clone_rows = await _run(client, "SELECT id FROM ds.workcopy ORDER BY id")
    assert _rows(src_rows) == [["1"], ["2"]]
    assert _rows(clone_rows) == [["1"], ["2"], ["99"]]


async def test_clone_listed_with_clone_type(
    client: httpx.AsyncClient,
) -> None:
    await _run(client, "INSERT INTO ds.t VALUES (1, 'a')")
    await _run(client, "CREATE TABLE ds.workcopy CLONE ds.t")

    r = await client.get("/bigquery/v2/projects/p/datasets/ds/tables")
    r.raise_for_status()
    tables = {t["tableReference"]["tableId"]: t for t in r.json()["tables"]}
    assert tables["workcopy"]["type"] == "CLONE"


async def test_clone_into_other_dataset(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        "/bigquery/v2/projects/p/datasets",
        json={"datasetReference": {"projectId": "p", "datasetId": "archive"}},
    )
    await _run(client, "INSERT INTO ds.t VALUES (1, 'a'), (2, 'b')")
    await _run(client, "CREATE TABLE archive.t CLONE ds.t")

    rows = await _run(client, "SELECT id FROM archive.t ORDER BY id")
    assert _rows(rows) == [["1"], ["2"]]


async def test_clone_missing_source_returns_404(
    client: httpx.AsyncClient,
) -> None:
    # P3.a / ADR 0022 §3: SQL execution errors return HTTP 200 with
    # the job's ``errors[0].reason`` set (matching real BigQuery's
    # wire behaviour — the Python client maps reason → exception
    # class), not a direct 4xx HTTP response.
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": "CREATE TABLE ds.copy CLONE ds.missing", "useLegacySql": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["errors"][0]["reason"] == "notFound"
