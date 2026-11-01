"""Verify that functions over a view filtering a STRUCT column bind through ``SELECT *``.

The translator expands ``SELECT *`` from the catalog schema snapshot
before ``annotate_types`` runs, so the snapshot carries every column of
the base table, including the ``STRUCT`` / ``ARRAY`` ones DDL stores as
``RECORD`` / ``REPEATED``. The view's outer ``WHERE meta.change_type …``
binds only while the expanded inner star still carries ``meta``, and the
projection function (``UPPER``, ``CONCAT``, ``||``) is what makes the
translator serialise the qualified tree rather than the original SQL,
so the three projections cover the path a plain ``SELECT col`` skips.

This is the shape every CDC dedup view takes (Datastream, Debezium):
``SELECT * EXCEPT (meta) FROM (SELECT * FROM raw QUALIFY ROW_NUMBER()
OVER (…) = 1) WHERE meta.change_type NOT IN ('DELETE')``.
"""

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
    """Start an in-process ephemeral emulator and create dataset ``p.ds``."""
    settings = Settings(persistence_mode=PersistenceMode.EPHEMERAL, rest_port=0, grpc_port=0)
    s = EmulatorServer(settings)
    await s.start()
    try:
        async with httpx.AsyncClient(base_url=s.rest_url, timeout=20.0) as c:
            r = await c.post(
                "/bigquery/v2/projects/p/datasets",
                json={"datasetReference": {"projectId": "p", "datasetId": "ds"}},
            )
            r.raise_for_status()
            yield c
    finally:
        await s.stop()


async def _run(client: httpx.AsyncClient, sql: str) -> dict[str, Any]:
    """Run *sql* through ``jobs.query`` and return the response payload."""
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": sql, "useLegacySql": False},
    )
    r.raise_for_status()
    return r.json()


def _rows(payload: dict[str, Any]) -> list[list[str]]:
    """Flatten a ``jobs.query`` response into ``[[cell, …], …]``."""
    return [[c["v"] for c in row["f"]] for row in payload.get("rows", [])]


@pytest.mark.parametrize(
    "projection",
    [
        "UPPER(name)",
        "CONCAT(name, '!')",
        "name || '!'",
    ],
)
async def test_function_over_dedup_view_binds_struct_filter(
    client: httpx.AsyncClient,
    projection: str,
) -> None:
    """Check that the STRUCT filter binds and the projected rows match for each function."""
    await _run(
        client,
        "CREATE TABLE ds.raw (id INT64, name STRING, "
        "meta STRUCT<source_timestamp INT64, change_type STRING, sort_keys ARRAY<STRING>>)",
    )
    await _run(
        client,
        "INSERT INTO ds.raw VALUES "
        "(1, 'a', STRUCT(1, 'INSERT', [])), "
        "(1, 'b', STRUCT(2, 'UPDATE-INSERT', [])), "
        "(2, 'c', STRUCT(1, 'INSERT', [])), "
        "(2, 'c', STRUCT(2, 'DELETE', []))",
    )
    await _run(
        client,
        "CREATE VIEW ds.dedup AS "
        "SELECT * EXCEPT (meta) FROM ("
        "  SELECT * FROM ds.raw "
        "  QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY meta.source_timestamp DESC) = 1"
        ") WHERE meta.change_type NOT IN ('DELETE', 'UPDATE-DELETE')",
    )

    plain = await _run(client, "SELECT name FROM ds.dedup ORDER BY id")
    assert _rows(plain) == [["b"]]

    rewritten = await _run(client, f"SELECT {projection} AS n FROM ds.dedup ORDER BY id")
    expected = [["B"]] if projection.startswith("UPPER") else [["b!"]]
    assert _rows(rewritten) == expected
