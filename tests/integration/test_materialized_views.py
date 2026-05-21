"""Integration tests for materialized views: create, refresh, INFORMATION_SCHEMA."""

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
                        "tableId": "orders",
                    },
                    "schema": {
                        "fields": [
                            {"name": "country", "type": "STRING"},
                            {"name": "amount", "type": "INT64"},
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


async def test_create_and_query_materialized_view(
    client: httpx.AsyncClient,
) -> None:
    await _run(client, "INSERT INTO ds.orders VALUES ('US', 10), ('US', 5), ('CA', 20)")
    await _run(
        client,
        "CREATE MATERIALIZED VIEW ds.country_totals AS "
        "SELECT country, SUM(amount) AS total FROM ds.orders GROUP BY country",
    )
    r = await _run(
        client,
        "SELECT country, total FROM ds.country_totals ORDER BY country",
    )
    assert _rows(r) == [["CA", "20"], ["US", "15"]]


async def test_mv_auto_refreshes_after_base_change(
    client: httpx.AsyncClient,
) -> None:
    await _run(client, "INSERT INTO ds.orders VALUES ('US', 10)")
    await _run(
        client,
        "CREATE MATERIALIZED VIEW ds.totals AS SELECT SUM(amount) AS total FROM ds.orders",
    )
    # Add a row — staleness should trigger a recompute on the next read.
    await _run(client, "INSERT INTO ds.orders VALUES ('CA', 90)")

    r = await _run(client, "SELECT total FROM ds.totals")
    assert _rows(r) == [["100"]]


async def test_explicit_refresh_recomputes(client: httpx.AsyncClient) -> None:
    await _run(client, "INSERT INTO ds.orders VALUES ('US', 1)")
    await _run(
        client,
        "CREATE MATERIALIZED VIEW ds.totals AS SELECT SUM(amount) AS total FROM ds.orders",
    )
    await _run(client, "INSERT INTO ds.orders VALUES ('CA', 9)")
    await _run(client, "REFRESH MATERIALIZED VIEW ds.totals")

    r = await _run(client, "SELECT total FROM ds.totals")
    assert _rows(r) == [["10"]]


async def test_information_schema_materialized_views(
    client: httpx.AsyncClient,
) -> None:
    await _run(client, "INSERT INTO ds.orders VALUES ('US', 10)")
    await _run(
        client,
        "CREATE MATERIALIZED VIEW ds.totals AS SELECT SUM(amount) AS total FROM ds.orders",
    )
    r = await _run(
        client,
        "SELECT table_name, is_stale FROM ds.INFORMATION_SCHEMA.MATERIALIZED_VIEWS",
    )
    rows = _rows(r)
    assert len(rows) == 1
    assert rows[0][0] == "totals"
    assert rows[0][1] in ("false", "FALSE", "0")  # boolean rendering varies


async def test_dml_against_mv_rejected(client: httpx.AsyncClient) -> None:
    """Direct DML against a materialized view is rejected — they refresh.

    P3.a / ADR 0022 §3: SQL execution errors surface as HTTP 200 with
    the job's ``errors[0]`` (real BigQuery wire behaviour), not as a
    direct 4xx response.
    """
    await _run(client, "INSERT INTO ds.orders VALUES ('US', 1)")
    await _run(
        client,
        "CREATE MATERIALIZED VIEW ds.totals AS SELECT SUM(amount) AS total FROM ds.orders",
    )
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": "INSERT INTO ds.totals VALUES (999)", "useLegacySql": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["errors"][0]["reason"] == "invalidQuery"
    assert "immutable" in body["errors"][0]["message"].lower()


async def test_drop_materialized_view(client: httpx.AsyncClient) -> None:
    await _run(client, "INSERT INTO ds.orders VALUES ('US', 1)")
    await _run(
        client,
        "CREATE MATERIALIZED VIEW ds.totals AS SELECT SUM(amount) AS total FROM ds.orders",
    )
    await _run(client, "DROP MATERIALIZED VIEW ds.totals")
    r = await client.get("/bigquery/v2/projects/p/datasets/ds/tables")
    table_ids = [t["tableReference"]["tableId"] for t in r.json()["tables"]]
    assert "totals" not in table_ids


async def test_mv_with_join_refreshes_when_either_base_changes(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        "/bigquery/v2/projects/p/datasets/ds/tables",
        json={
            "tableReference": {"projectId": "p", "datasetId": "ds", "tableId": "rates"},
            "schema": {
                "fields": [
                    {"name": "country", "type": "STRING"},
                    {"name": "rate", "type": "FLOAT64"},
                ],
            },
        },
    )
    await _run(client, "INSERT INTO ds.orders VALUES ('US', 100), ('CA', 100)")
    await _run(client, "INSERT INTO ds.rates VALUES ('US', 1.0), ('CA', 0.7)")
    await _run(
        client,
        "CREATE MATERIALIZED VIEW ds.usd_totals AS "
        "SELECT o.country, SUM(o.amount * r.rate) AS usd "
        "FROM ds.orders AS o JOIN ds.rates AS r USING (country) GROUP BY o.country",
    )
    # Change a row in the rates side; MV should refresh.
    await _run(client, "UPDATE ds.rates SET rate = 0.5 WHERE country = 'CA'")

    r = await _run(
        client,
        "SELECT country, usd FROM ds.usd_totals ORDER BY country",
    )
    rows = _rows(r)
    assert rows[0][0] == "CA" and rows[0][1].startswith("50")
    assert rows[1][0] == "US" and rows[1][1].startswith("100")
