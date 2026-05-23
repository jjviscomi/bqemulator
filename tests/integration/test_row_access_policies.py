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
    table_id: str = "orders",
) -> None:
    body = {
        "rowAccessPolicyReference": {
            "projectId": "p",
            "datasetId": "ds",
            "tableId": table_id,
            "policyId": policy_id,
        },
        "filterPredicate": filter_predicate,
        "grantees": grantees,
    }
    r = await c.post(
        f"/bigquery/v2/projects/p/datasets/ds/tables/{table_id}/rowAccessPolicies",
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


# ---------------------------------------------------------------------------
# SESSION_USER() in the RAP filter predicate — ADR 0038
# ---------------------------------------------------------------------------
#
# The canonical "tenant isolation by email domain" production pattern:
# a RAP filter calls ``SESSION_USER()``, extracts the domain via
# ``REGEXP_EXTRACT``, and matches it against a per-row tenant key. Real
# BigQuery users rely on this; the emulator's pre-translator (ADR 0038)
# substitutes ``SESSION_USER()`` with the caller's resolved email
# literal before SQLGlot transpiles to DuckDB.
#
# These tests seed a SECOND table (``tenants``) with two domains, grant
# the policy to ``allAuthenticatedUsers`` so the grantee match always
# passes, and rely on the filter predicate alone for per-caller row
# visibility — proving the substitution path end-to-end.


async def _seed_tenants_table(c: httpx.AsyncClient) -> None:
    """Create a ``tenants`` table with rows for two email-domain tenants."""
    await c.post(
        "/bigquery/v2/projects/p/datasets/ds/tables",
        json={
            "tableReference": {
                "projectId": "p",
                "datasetId": "ds",
                "tableId": "tenants",
            },
            "schema": {
                "fields": [
                    {"name": "id", "type": "INT64"},
                    {"name": "tenant_id", "type": "STRING"},
                ],
            },
        },
    )
    await _run(
        c,
        (
            "INSERT INTO ds.tenants VALUES "
            "(1, 'example.com'), (2, 'example.com'), "
            "(3, 'other.com'), (4, 'other.com')"
        ),
    )


async def _create_session_user_policy(c: httpx.AsyncClient) -> None:
    """Create the canonical SESSION_USER-driven tenant-isolation RAP."""
    await _create_policy(
        c,
        policy_id="tenant_by_session_user",
        filter_predicate=("REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = tenant_id"),
        grantees=["allAuthenticatedUsers"],
        table_id="tenants",
    )


@pytest.mark.asyncio
async def test_session_user_filter_example_com_caller(
    client: httpx.AsyncClient,
) -> None:
    """A ``@example.com`` caller sees only the example.com tenant rows."""
    await _seed_tenants_table(client)
    await _create_session_user_policy(client)
    res = await _run(
        client,
        "SELECT id FROM ds.tenants ORDER BY id",
        caller="user:alice@example.com",
    )
    rows = [int(r["f"][0]["v"]) for r in res["rows"]]
    assert rows == [1, 2]


@pytest.mark.asyncio
async def test_session_user_filter_other_com_caller(
    client: httpx.AsyncClient,
) -> None:
    """A ``@other.com`` caller sees only the other.com tenant rows."""
    await _seed_tenants_table(client)
    await _create_session_user_policy(client)
    res = await _run(
        client,
        "SELECT id FROM ds.tenants ORDER BY id",
        caller="user:bob@other.com",
    )
    rows = [int(r["f"][0]["v"]) for r in res["rows"]]
    assert rows == [3, 4]


@pytest.mark.asyncio
async def test_session_user_filter_anonymous_caller_sees_no_rows(
    client: httpx.AsyncClient,
) -> None:
    """No caller header → ``SESSION_USER()`` → 'anonymous' → no match."""
    await _seed_tenants_table(client)
    await _create_session_user_policy(client)
    # The grantee is ``allAuthenticatedUsers`` and the default
    # caller is *unauthenticated* — the grantee check itself fails
    # first, so the table reads as "has policies but none match the
    # caller" → ``WHERE FALSE`` wrap → zero rows.
    res = await _run(client, "SELECT id FROM ds.tenants")
    assert res["rows"] == []


@pytest.mark.asyncio
async def test_session_user_filter_service_account_caller(
    client: httpx.AsyncClient,
) -> None:
    """A service-account caller resolves to its full email."""
    await _seed_tenants_table(client)
    # The filter predicate matches the part after ``@`` —
    # service accounts have addresses like
    # ``svc@example.iam.gserviceaccount.com``, which would *not*
    # match either tenant_id we seeded. Use a SA whose host happens
    # to be ``example.com`` to assert the substitution applies the
    # ``serviceAccount:`` prefix strip + the standard regex extract
    # (this is unusual in production but is the minimal probe for
    # the SA path on the e2e wire).
    await _create_policy(
        client,
        policy_id="tenant_sa",
        filter_predicate=("REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = tenant_id"),
        grantees=["allAuthenticatedUsers"],
        table_id="tenants",
    )
    res = await _run(
        client,
        "SELECT id FROM ds.tenants ORDER BY id",
        caller="serviceAccount:job@example.com",
    )
    rows = [int(r["f"][0]["v"]) for r in res["rows"]]
    assert rows == [1, 2]


@pytest.mark.asyncio
async def test_bare_select_session_user_returns_caller_email(
    client: httpx.AsyncClient,
) -> None:
    """``SELECT SESSION_USER()`` returns the caller's bare email.

    Not strictly a RAP test, but the same substitution path; pinning
    it here keeps both surfaces (RAP filter + free-form query) in one
    place.
    """
    res = await _run(
        client,
        "SELECT SESSION_USER() AS who",
        caller="user:claire@example.com",
    )
    assert res["rows"][0]["f"][0]["v"] == "claire@example.com"
