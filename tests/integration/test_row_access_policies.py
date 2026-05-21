"""Integration tests: row access policies enforced end-to-end via REST."""

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
        async with httpx.AsyncClient(base_url=s.rest_url, timeout=30.0) as c:
            await _seed_dataset_and_table(c)
            yield c
    finally:
        await s.stop()


async def _seed_dataset_and_table(c: httpx.AsyncClient) -> None:
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
                    {"name": "id", "type": "INT64"},
                    {"name": "region", "type": "STRING"},
                ],
            },
        },
    )
    # Seed rows: 2 EU + 2 US.
    await _run(c, "INSERT INTO ds.orders VALUES (1, 'EU'), (2, 'EU'), (3, 'US'), (4, 'US')")


async def _run(c: httpx.AsyncClient, sql: str, *, caller: str | None = None) -> dict:
    headers = {}
    if caller is not None:
        headers["X-Bqemu-Caller"] = caller
    r = await c.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": sql, "useLegacySql": False},
        headers=headers,
    )
    r.raise_for_status()
    return r.json()


async def _create_policy(
    c: httpx.AsyncClient,
    *,
    policy_id: str,
    filter_predicate: str,
    grantees: list[str],
) -> None:
    body = {
        "rowAccessPolicyReference": {
            "projectId": "p",
            "datasetId": "ds",
            "tableId": "orders",
            "policyId": policy_id,
        },
        "filterPredicate": filter_predicate,
        "grantees": grantees,
    }
    r = await c.post(
        "/bigquery/v2/projects/p/datasets/ds/tables/orders/rowAccessPolicies",
        json=body,
    )
    r.raise_for_status()


@pytest.mark.asyncio
async def test_eu_caller_sees_only_eu_rows(client: httpx.AsyncClient) -> None:
    await _create_policy(
        client,
        policy_id="eu_only",
        filter_predicate="region = 'EU'",
        grantees=["user:eu@example.com"],
    )
    res = await _run(
        client,
        "SELECT id FROM ds.orders ORDER BY id",
        caller="user:eu@example.com",
    )
    rows = [int(r["f"][0]["v"]) for r in res["rows"]]
    assert rows == [1, 2]


@pytest.mark.asyncio
async def test_other_caller_sees_no_rows(client: httpx.AsyncClient) -> None:
    await _create_policy(
        client,
        policy_id="eu_only",
        filter_predicate="region = 'EU'",
        grantees=["user:eu@example.com"],
    )
    res = await _run(
        client,
        "SELECT id FROM ds.orders",
        caller="user:other@example.com",
    )
    assert res["rows"] == []


@pytest.mark.asyncio
async def test_table_without_policies_unchanged(client: httpx.AsyncClient) -> None:
    # No policies created.
    res = await _run(
        client,
        "SELECT id FROM ds.orders ORDER BY id",
        caller="user:other@example.com",
    )
    rows = [int(r["f"][0]["v"]) for r in res["rows"]]
    assert rows == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_multiple_policies_or_combined(client: httpx.AsyncClient) -> None:
    await _create_policy(
        client,
        policy_id="eu_only",
        filter_predicate="region = 'EU'",
        grantees=["user:m@example.com"],
    )
    await _create_policy(
        client,
        policy_id="id_one_only",
        filter_predicate="id = 3",
        grantees=["user:m@example.com"],
    )
    res = await _run(
        client,
        "SELECT id FROM ds.orders ORDER BY id",
        caller="user:m@example.com",
    )
    rows = [int(r["f"][0]["v"]) for r in res["rows"]]
    assert rows == [1, 2, 3]


@pytest.mark.asyncio
async def test_information_schema_row_access_policies(
    client: httpx.AsyncClient,
) -> None:
    await _create_policy(
        client,
        policy_id="eu_only",
        filter_predicate="region = 'EU'",
        grantees=["user:eu@example.com"],
    )
    res = await _run(
        client,
        "SELECT policy_name, table_name FROM ds.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
    )
    rows = [(r["f"][0]["v"], r["f"][1]["v"]) for r in res["rows"]]
    assert ("eu_only", "orders") in rows


@pytest.mark.asyncio
async def test_authorized_view_still_enforces_rap(client: httpx.AsyncClient) -> None:
    """RAP applies even when querying through an authorized view.

    BigQuery does NOT bypass row-level security for authorized views;
    ADR 0018 §"Authorized-view bypass — does not exist for RAP (revised
    2026-05-18)" captures the empirical evidence (the 5 ``authz_view_*``
    conformance fixtures recorded against real BQ in both same-dataset
    and cross-dataset topology all returned 0 rows). The access entry
    confers IAM-level read access on the base data (so the caller does
    not need direct ``bigquery.dataViewer`` on the base dataset), but
    caller-bound RAP filters still evaluate against every base-table
    reference inside the view body.
    """
    # Create a second dataset and view that selects from the protected
    # base table, and authorize the view in the base dataset's access.
    await client.post(
        "/bigquery/v2/projects/p/datasets",
        json={"datasetReference": {"projectId": "p", "datasetId": "v_ds"}},
    )
    await client.post(
        "/bigquery/v2/projects/p/datasets/v_ds/tables",
        json={
            "tableReference": {
                "projectId": "p",
                "datasetId": "v_ds",
                "tableId": "all_orders",
            },
            "view": {"query": "SELECT id, region FROM ds.orders"},
        },
    )
    # Update the base dataset to authorize the view.
    await client.patch(
        "/bigquery/v2/projects/p/datasets/ds",
        json={
            "access": [
                {
                    "view": {
                        "projectId": "p",
                        "datasetId": "v_ds",
                        "tableId": "all_orders",
                    },
                },
            ],
        },
    )
    # Add a policy that hides every row from anyone other than the
    # eu-only grantee. RAP enforcement is per calling user; this caller
    # ("user:other@example.com") is NOT a grantee, so the rewriter
    # injects the zero-rows fallback even when the read routes through
    # the authorized view.
    await _create_policy(
        client,
        policy_id="eu_only",
        filter_predicate="region = 'EU'",
        grantees=["user:eu@example.com"],
    )
    res = await _run(
        client,
        "SELECT id FROM v_ds.all_orders ORDER BY id",
        caller="user:other@example.com",
    )
    rows = [int(r["f"][0]["v"]) for r in res.get("rows", [])]
    assert rows == []


@pytest.mark.asyncio
async def test_unauthorized_view_does_not_bypass(client: httpx.AsyncClient) -> None:
    # Create a view but DON'T grant access on the base dataset.
    await client.post(
        "/bigquery/v2/projects/p/datasets",
        json={"datasetReference": {"projectId": "p", "datasetId": "v_ds"}},
    )
    await client.post(
        "/bigquery/v2/projects/p/datasets/v_ds/tables",
        json={
            "tableReference": {
                "projectId": "p",
                "datasetId": "v_ds",
                "tableId": "view_no_auth",
            },
            "view": {"query": "SELECT id, region FROM ds.orders"},
        },
    )
    await _create_policy(
        client,
        policy_id="eu_only",
        filter_predicate="region = 'EU'",
        grantees=["user:eu@example.com"],
    )
    res = await _run(
        client,
        "SELECT id FROM v_ds.view_no_auth",
        caller="user:other@example.com",
    )
    assert res["rows"] == []


@pytest.mark.asyncio
async def test_default_caller_blocked(client: httpx.AsyncClient) -> None:
    """No caller header => default fallback => no-match => zero rows."""
    await _create_policy(
        client,
        policy_id="eu_only",
        filter_predicate="region = 'EU'",
        grantees=["user:eu@example.com"],
    )
    res = await _run(client, "SELECT id FROM ds.orders")
    assert res["rows"] == []


@pytest.mark.asyncio
async def test_x_goog_user_project_fallback(client: httpx.AsyncClient) -> None:
    """X-Goog-User-Project maps to a synthetic identity per ADR 0018."""
    await _create_policy(
        client,
        policy_id="proj_caller",
        filter_predicate="id IN (1, 2, 3)",
        grantees=["user:caller@my-proj.iam.gserviceaccount.com"],
    )
    r = await client.post(
        "/bigquery/v2/projects/p/queries",
        json={
            "query": "SELECT id FROM ds.orders ORDER BY id",
            "useLegacySql": False,
        },
        headers={"X-Goog-User-Project": "my-proj"},
    )
    r.raise_for_status()
    rows = [int(row["f"][0]["v"]) for row in r.json()["rows"]]
    assert rows == [1, 2, 3]
