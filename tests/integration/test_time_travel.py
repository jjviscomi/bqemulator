"""Integration tests for ``FOR SYSTEM_TIME AS OF``."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio

from bqemulator.config import PersistenceMode, Settings
from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def server() -> AsyncIterator[EmulatorServer]:
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
async def client(server: EmulatorServer) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=server.rest_url, timeout=20.0) as c:
        await c.post(
            "/bigquery/v2/projects/p/datasets",
            json={"datasetReference": {"projectId": "p", "datasetId": "ds"}},
        )
        await c.post(
            "/bigquery/v2/projects/p/datasets/ds/tables",
            json={
                "tableReference": {"projectId": "p", "datasetId": "ds", "tableId": "t"},
                "schema": {
                    "fields": [
                        {"name": "id", "type": "INT64"},
                        {"name": "val", "type": "STRING"},
                    ],
                },
            },
        )
        yield c


async def _run(client: httpx.AsyncClient, sql: str) -> dict[str, Any]:
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": sql, "useLegacySql": False},
    )
    r.raise_for_status()
    return r.json()


def _rows(payload: dict[str, Any]) -> list[list[str]]:
    return [[c["v"] for c in row["f"]] for row in payload.get("rows", [])]


async def test_time_travel_returns_pre_change_state(
    server: EmulatorServer,
    client: httpx.AsyncClient,
) -> None:
    """A FOR SYSTEM_TIME AS OF query reads the pre-change snapshot."""
    await _run(client, "INSERT INTO ds.t VALUES (1, 'a')")

    # Wait long enough that the next write produces a strictly later
    # snapshot timestamp.
    await asyncio.sleep(0.05)
    boundary = datetime.now(tz=UTC)
    await asyncio.sleep(0.05)

    await _run(client, "INSERT INTO ds.t VALUES (2, 'b')")

    target = boundary.isoformat(sep=" ", timespec="microseconds")
    r = await _run(
        client,
        f"SELECT id FROM ds.t FOR SYSTEM_TIME AS OF TIMESTAMP '{target}' ORDER BY id",
    )
    ids = [row[0] for row in _rows(r)]
    assert ids == ["1"]

    # The live table sees both rows.
    live = await _run(client, "SELECT id FROM ds.t ORDER BY id")
    assert [row[0] for row in _rows(live)] == ["1", "2"]


async def test_time_travel_falls_back_to_live_when_no_snapshot(
    client: httpx.AsyncClient,
) -> None:
    """If no snapshot is captured before the target, the live table is used."""
    await _run(client, "INSERT INTO ds.t VALUES (1, 'a')")
    target = datetime.now(tz=UTC).isoformat(sep=" ", timespec="microseconds")
    r = await _run(
        client,
        f"SELECT id FROM ds.t FOR SYSTEM_TIME AS OF TIMESTAMP '{target}' ORDER BY id",
    )
    assert _rows(r) == [["1"]]


async def test_time_travel_future_target_returns_400(
    client: httpx.AsyncClient,
) -> None:
    # P3.a / ADR 0022 §3: SQL execution errors surface as HTTP 200 with
    # the job's ``errors[0]`` (real BigQuery wire behaviour), not as a
    # direct 4xx response. The ``OUT_OF_RANGE`` semantic is preserved
    # via the error envelope's ``reason='outOfRange'``.
    future = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(sep=" ")
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={
            "query": (f"SELECT id FROM ds.t FOR SYSTEM_TIME AS OF TIMESTAMP '{future}'"),
            "useLegacySql": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["errors"][0]["reason"] == "outOfRange"


async def test_time_travel_beyond_retention_returns_400(
    client: httpx.AsyncClient,
) -> None:
    # P3.a / ADR 0022 §3: see ``test_time_travel_future_target_returns_400``
    # above.
    long_ago = (datetime.now(tz=UTC) - timedelta(days=30)).isoformat(sep=" ")
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={
            "query": (f"SELECT id FROM ds.t FOR SYSTEM_TIME AS OF TIMESTAMP '{long_ago}'"),
            "useLegacySql": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["errors"][0]["reason"] == "outOfRange"
