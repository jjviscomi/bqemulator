"""Integration tests for BigQuery scripting via the jobs.query REST endpoint."""

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
    async with httpx.AsyncClient(base_url=running_server.rest_url, timeout=20.0) as c:
        await c.post(
            "/bigquery/v2/projects/p/datasets",
            json={"datasetReference": {"projectId": "p", "datasetId": "ds"}},
        )
        yield c


async def _run(client: httpx.AsyncClient, sql: str) -> dict[str, object]:
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": sql, "useLegacySql": False},
    )
    r.raise_for_status()
    return r.json()


class TestScriptingFlow:
    async def test_declare_and_select(self, client: httpx.AsyncClient) -> None:
        resp = await _run(client, "DECLARE x INT64 DEFAULT 42; SELECT x;")
        assert resp["rows"][0]["f"][0]["v"] == "42"

    async def test_loop_sums(self, client: httpx.AsyncClient) -> None:
        script = """
DECLARE total INT64 DEFAULT 0;
DECLARE i INT64 DEFAULT 0;
WHILE i < 5 DO
  SET i = i + 1;
  SET total = total + i;
END WHILE;
SELECT total;
"""
        resp = await _run(client, script)
        assert resp["rows"][0]["f"][0]["v"] == "15"

    async def test_for_over_query(self, client: httpx.AsyncClient) -> None:
        script = """
DECLARE total INT64 DEFAULT 0;
FOR row IN (SELECT x FROM UNNEST([1,2,3]) AS x) DO
  SET total = total + row.x;
END FOR;
SELECT total;
"""
        resp = await _run(client, script)
        assert resp["rows"][0]["f"][0]["v"] == "6"

    async def test_exception_handler(self, client: httpx.AsyncClient) -> None:
        script = """
DECLARE result_val STRING DEFAULT 'unset';
BEGIN
  SELECT CAST('abc' AS INT64);
EXCEPTION WHEN ERROR THEN
  SET result_val = 'handled';
END;
SELECT result_val;
"""
        resp = await _run(client, script)
        assert resp["rows"][0]["f"][0]["v"] == "handled"

    async def test_stored_procedure_create_and_call(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        create_proc = """
CREATE PROCEDURE ds.square(x INT64)
BEGIN
  SELECT x * x;
END;
"""
        await _run(client, create_proc)
        resp = await _run(client, "CALL ds.square(7);")
        # The CALL statement's last SELECT inside the procedure is the
        # final table of the script, so we see the squared value.
        assert resp["rows"][0]["f"][0]["v"] == "49"

    async def test_script_statistics(self, client: httpx.AsyncClient) -> None:
        r = await client.post(
            "/bigquery/v2/projects/p/jobs",
            json={
                "configuration": {
                    "query": {
                        "query": (
                            "DECLARE a INT64 DEFAULT 1;DECLARE b INT64 DEFAULT 2;SELECT a + b;"
                        ),
                        "useLegacySql": False,
                    },
                },
            },
        )
        r.raise_for_status()
        body = r.json()
        stats = body["statistics"]["scriptStatistics"]
        assert int(stats["statementCount"]) >= 3


class TestShipCriterion:
    """The actual ship-criterion script from the phase doc."""

    async def test_ship_criterion_script(self, client: httpx.AsyncClient) -> None:
        # Create the three routines referenced by the script.
        for rid, body in [
            (
                "sql_inc",
                {
                    "routineType": "SCALAR_FUNCTION",
                    "language": "SQL",
                    "arguments": [{"name": "x", "dataType": {"typeKind": "INT64"}}],
                    "returnType": {"typeKind": "INT64"},
                    "definitionBody": "x + 1",
                },
            ),
            (
                "js_double",
                {
                    "routineType": "SCALAR_FUNCTION",
                    "language": "JAVASCRIPT",
                    "arguments": [{"name": "x", "dataType": {"typeKind": "INT64"}}],
                    "returnType": {"typeKind": "INT64"},
                    "definitionBody": "return x * 2;",
                },
            ),
            (
                "one_to_n",
                {
                    "routineType": "TABLE_VALUED_FUNCTION",
                    "language": "SQL",
                    "arguments": [{"name": "n", "dataType": {"typeKind": "INT64"}}],
                    "definitionBody": "SELECT i AS value FROM UNNEST(GENERATE_ARRAY(1, n)) AS i",
                },
            ),
        ]:
            ref = {"projectId": "p", "datasetId": "ds", "routineId": rid}
            r = await client.post(
                "/bigquery/v2/projects/p/datasets/ds/routines",
                json={"routineReference": ref, **body},
            )
            r.raise_for_status()

        ship_script = """
DECLARE n INT64 DEFAULT 3;
DECLARE total INT64 DEFAULT 0;
BEGIN
  FOR row IN (SELECT value FROM ds.one_to_n(n)) DO
    SET total = total + ds.js_double(ds.sql_inc(row.value));
  END FOR;
EXCEPTION WHEN ERROR THEN
  SET total = -1;
END;
IF total > 0 THEN
  SELECT total AS answer;
ELSE
  SELECT -1 AS answer;
END IF;
"""
        resp = await _run(client, ship_script)
        # 1->2->4; 2->3->6; 3->4->8; total = 18
        assert resp["rows"][0]["f"][0]["v"] == "18"


class TestTempRoutines:
    """``CREATE TEMP FUNCTION`` inside a script registers a session-scoped routine."""

    async def test_temp_sql_function_round_trips(self, client: httpx.AsyncClient) -> None:
        # The temp function is declared and immediately invoked in the
        # same script. It is registered into the synthetic-dataset
        # temp-routine registry on first declaration.
        script = """
CREATE TEMP FUNCTION inc(x INT64) AS (x + 1);
SELECT inc(41);
"""
        resp = await _run(client, script)
        assert resp["rows"][0]["f"][0]["v"] == "42"

    async def test_temp_function_callable_from_subsequent_statement(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        script = """
CREATE TEMP FUNCTION double_it(x INT64) AS (x * 2);
SELECT SUM(double_it(n)) FROM UNNEST([1, 2, 3]) AS n;
"""
        resp = await _run(client, script)
        # 1+2+3 = 6, doubled = 12.
        assert resp["rows"][0]["f"][0]["v"] == "12"

    async def test_temp_function_chains(self, client: httpx.AsyncClient) -> None:
        # Per ADR 0023 §1.D, TEMP routines can reference each other by
        # bare name within a script — the interpreter pre-rewrites the
        # bare call to the materialised qualified name.
        script = """
CREATE TEMP FUNCTION inc(x INT64) AS (x + 1);
CREATE TEMP FUNCTION inc_twice(x INT64) AS (inc(inc(x)));
SELECT inc_twice(10);
"""
        resp = await _run(client, script)
        assert resp["rows"][0]["f"][0]["v"] == "12"
