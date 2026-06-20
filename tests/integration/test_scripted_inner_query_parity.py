"""Parity between standalone and scripted inner-query execution.

A BigQuery statement reaches DuckDB through one of two execution
chains: the standalone single-statement path
(``jobs.executor._run_single_sql``) and the scripted path
(``scripting.interpreter.ScriptInterpreter._run_query``), chosen by
whether the job is a multi-statement / control-flow script.

Both chains apply the same pre-translation rewrites so a statement
behaves identically regardless of which path runs it. These tests pin
the two rewrites that must stay consistent across both paths
(materialized view refresh and ``FOR SYSTEM_TIME AS OF`` time-travel) by
running the same query standalone and as the final statement of a
script and asserting identical results.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
            # raise_for_status on the bootstrap calls so a setup failure
            # surfaces here rather than as a misleading assertion later.
            r = await c.post(
                "/bigquery/v2/projects/p/datasets",
                json={"datasetReference": {"projectId": "p", "datasetId": "ds"}},
            )
            r.raise_for_status()
            r = await c.post(
                "/bigquery/v2/projects/p/datasets/ds/tables",
                json={
                    "tableReference": {
                        "projectId": "p",
                        "datasetId": "ds",
                        "tableId": "orders",
                    },
                    "schema": {
                        "fields": [
                            {"name": "id", "type": "INT64"},
                            {"name": "amount", "type": "INT64"},
                        ],
                    },
                },
            )
            r.raise_for_status()
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


def _as_script(sql: str) -> str:
    """Wrap ``sql`` so the job runs through the scripting interpreter.

    A two-statement script forces ``is_scripted=True`` in
    ``execute_query_job``; last-statement-wins means the wrapped query's
    result is the job's result.
    """
    return f"SELECT 1;\n{sql.rstrip().rstrip(';')};"


async def test_scripted_mv_refreshed_like_standalone(
    client: httpx.AsyncClient,
) -> None:
    """A script that reads a stale MV refreshes it, exactly as standalone does."""
    await _run(client, "INSERT INTO ds.orders VALUES (1, 10)")
    await _run(
        client,
        "CREATE MATERIALIZED VIEW ds.totals AS SELECT SUM(amount) AS total FROM ds.orders",
    )
    # Mutate a base table so the MV is now stale.
    await _run(client, "INSERT INTO ds.orders VALUES (2, 90)")

    # The scripted read must be the first reader of the stale MV: a
    # standalone read would refresh it as a side effect and mask the gap.
    # Standalone auto-refresh on read is pinned by test_materialized_views.
    select = "SELECT total FROM ds.totals"
    scripted = await _run(client, _as_script(select))

    assert _rows(scripted) == [["100"]]


async def test_scripted_time_travel_matches_standalone(
    client: httpx.AsyncClient,
) -> None:
    """``FOR SYSTEM_TIME AS OF`` reads the pre-change snapshot in a script too."""
    await _run(client, "INSERT INTO ds.orders VALUES (1, 10)")

    # Establish a boundary strictly between the two writes so the target
    # resolves to the first snapshot.
    await asyncio.sleep(0.05)
    boundary = datetime.now(tz=UTC)
    await asyncio.sleep(0.05)

    await _run(client, "INSERT INTO ds.orders VALUES (2, 90)")

    target = boundary.isoformat(sep=" ", timespec="microseconds")
    select = f"SELECT id FROM ds.orders FOR SYSTEM_TIME AS OF TIMESTAMP '{target}' ORDER BY id"

    standalone = await _run(client, select)
    scripted = await _run(client, _as_script(select))

    # Both must see only the pre-boundary row, not the live table.
    assert [row[0] for row in _rows(standalone)] == ["1"]
    assert [row[0] for row in _rows(scripted)] == ["1"]
