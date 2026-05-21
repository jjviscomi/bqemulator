"""Integration tests for WRITE_APPEND + schemaUpdateOptions=['ALLOW_FIELD_ADDITION'].

The WRITE_APPEND post-processing path in
``bqemulator.api.routes.jobs._post_process_write_append`` reads the
destination's existing rows, optionally evolves the catalog schema
when ``ALLOW_FIELD_ADDITION`` is set, and concats the new SELECT
results onto the existing table.
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
    async with httpx.AsyncClient(base_url=server.rest_url, timeout=30.0) as c:
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
                    "tableId": "events",
                },
                "schema": {
                    "fields": [
                        {"name": "id", "type": "INT64", "mode": "REQUIRED"},
                        {"name": "kind", "type": "STRING"},
                    ]
                },
            },
        )
        # Seed two rows so the WRITE_APPEND combine has existing data
        # to fold into. ``insertAll`` is the simplest path.
        await c.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/events/insertAll",
            json={
                "rows": [
                    {"json": {"id": 1, "kind": "alpha"}},
                    {"json": {"id": 2, "kind": "beta"}},
                ]
            },
        )
        yield c


async def _query(client: httpx.AsyncClient, sql: str) -> int:
    """Run a synchronous query and return the first int cell."""
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": sql, "useLegacySql": False},
    )
    r.raise_for_status()
    return int(r.json()["rows"][0]["f"][0]["v"])


async def _write_append_query(
    client: httpx.AsyncClient,
    sql: str,
    *,
    allow_field_addition: bool = False,
) -> httpx.Response:
    """POST a query job that targets ``ds.events`` with WRITE_APPEND."""
    job_config = {
        "query": {
            "query": sql,
            "useLegacySql": False,
            "destinationTable": {
                "projectId": "p",
                "datasetId": "ds",
                "tableId": "events",
            },
            "writeDisposition": "WRITE_APPEND",
        }
    }
    if allow_field_addition:
        job_config["query"]["schemaUpdateOptions"] = ["ALLOW_FIELD_ADDITION"]
    return await client.post(
        "/bigquery/v2/projects/p/jobs",
        json={"configuration": job_config},
    )


class TestSchemaMatchingAppend:
    """WRITE_APPEND with a SELECT that matches the destination schema."""

    async def test_completes_without_schema_error(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        # The SELECT projects only existing columns — the post-process
        # path runs the destination read-back + combine but should not
        # raise the schema-superset rejection.
        r = await _write_append_query(
            client,
            "SELECT 3 AS id, 'gamma' AS kind",
        )
        assert r.status_code == 200, r.text


class TestAllowFieldAddition:
    """``ALLOW_FIELD_ADDITION`` lets the SELECT introduce new columns.

    Without the flag, a SELECT that projects a column not in the
    destination raises ``Invalid schema update``. With the flag, the
    destination's catalog schema evolves to include the new column and
    pre-existing rows are padded with NULL.
    """

    async def test_rejects_new_field_without_flag(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        r = await _write_append_query(
            client,
            "SELECT 3 AS id, 'gamma' AS kind, 4.5 AS score",
            allow_field_addition=False,
        )
        # Should fail with a 4xx — "Cannot add fields (field: score)".
        assert r.status_code >= 400
        assert "score" in r.text or "add fields" in r.text

    async def test_accepts_new_field_with_flag(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        r = await _write_append_query(
            client,
            "SELECT 3 AS id, 'gamma' AS kind, 4.5 AS score",
            allow_field_addition=True,
        )
        assert r.status_code == 200, r.text
        # The destination should now have the ``score`` column visible.
        meta_r = await client.get(
            "/bigquery/v2/projects/p/datasets/ds/tables/events",
        )
        meta_r.raise_for_status()
        field_names = {f["name"] for f in meta_r.json()["schema"]["fields"]}
        assert "score" in field_names
