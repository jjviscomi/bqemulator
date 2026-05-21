"""Integration tests for Phase 6 routines — REST CRUD + invocation via queries."""

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


async def _create_routine(
    client: httpx.AsyncClient,
    rid: str,
    **body_overrides: object,
) -> None:
    body: dict[str, object] = {
        "routineReference": {"projectId": "p", "datasetId": "ds", "routineId": rid},
        "routineType": "SCALAR_FUNCTION",
        "language": "SQL",
        "arguments": [{"name": "x", "dataType": {"typeKind": "INT64"}}],
        "returnType": {"typeKind": "INT64"},
        "definitionBody": "x + 1",
    }
    body.update(body_overrides)
    r = await client.post("/bigquery/v2/projects/p/datasets/ds/routines", json=body)
    r.raise_for_status()


async def _query(client: httpx.AsyncClient, sql: str) -> dict[str, object]:
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": sql, "useLegacySql": False},
    )
    r.raise_for_status()
    return r.json()


class TestSQLUDF:
    async def test_scalar_sql_udf(self, client: httpx.AsyncClient) -> None:
        await _create_routine(client, "add_one")
        resp = await _query(client, "SELECT ds.add_one(41) AS v")
        assert resp["rows"][0]["f"][0]["v"] == "42"


class TestJSUDF:
    async def test_basic_javascript_udf(self, client: httpx.AsyncClient) -> None:
        await _create_routine(
            client,
            "js_double",
            language="JAVASCRIPT",
            definitionBody="return x * 2;",
        )
        resp = await _query(client, "SELECT ds.js_double(21) AS v")
        assert resp["rows"][0]["f"][0]["v"] == "42"

    async def test_invalid_javascript_fails_at_create(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        body = {
            "routineReference": {"projectId": "p", "datasetId": "ds", "routineId": "bad"},
            "routineType": "SCALAR_FUNCTION",
            "language": "JAVASCRIPT",
            "arguments": [{"name": "x", "dataType": {"typeKind": "INT64"}}],
            "returnType": {"typeKind": "INT64"},
            "definitionBody": "not valid ((",
        }
        r = await client.post(
            "/bigquery/v2/projects/p/datasets/ds/routines",
            json=body,
        )
        assert r.status_code == 400


class TestTVF:
    async def test_table_function(self, client: httpx.AsyncClient) -> None:
        body = {
            "routineReference": {"projectId": "p", "datasetId": "ds", "routineId": "gen"},
            "routineType": "TABLE_VALUED_FUNCTION",
            "language": "SQL",
            "arguments": [{"name": "n", "dataType": {"typeKind": "INT64"}}],
            "definitionBody": "SELECT v FROM UNNEST(GENERATE_ARRAY(1, n)) AS v",
        }
        r = await client.post(
            "/bigquery/v2/projects/p/datasets/ds/routines",
            json=body,
        )
        r.raise_for_status()
        resp = await _query(client, "SELECT v FROM ds.gen(3) ORDER BY v")
        values = [int(row["f"][0]["v"]) for row in resp["rows"]]
        assert values == [1, 2, 3]


class TestInformationSchema:
    async def test_list_via_information_schema(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        await _create_routine(client, "one")
        await _create_routine(client, "two")
        resp = await _query(
            client,
            "SELECT routine_name FROM ds.INFORMATION_SCHEMA.ROUTINES ORDER BY routine_name",
        )
        names = [row["f"][0]["v"] for row in resp["rows"]]
        assert names == ["one", "two"]
