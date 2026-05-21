"""E2E: Phase 8 row access policies + authorized views.

Exercises the Phase 8 ship criterion against a live container:

* A row access policy granting only ``user:eu-analyst@example.com``
  rows where ``region = 'EU'`` is enforced — that caller sees only
  EU rows; another caller sees zero rows; the default fallback caller
  also sees zero rows.
* When an authorized view sits between the caller and the protected
  base table, RAP is STILL enforced — the view does NOT bypass the
  caller-bound policy (P2.d follow-up #1 reversal, ADR 0018).
* ``INFORMATION_SCHEMA.ROW_ACCESS_POLICIES`` lists the active policy.

The official ``google-cloud-bigquery`` client does not expose a
per-request header API, so each E2E uses an ``AuthorizedSession``
with the ``X-Bqemu-Caller`` header injected at session construction
time (see ADR 0018).
"""

from __future__ import annotations

from collections.abc import Iterator

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.auth.transport.requests import AuthorizedSession
from google.cloud import bigquery
import httpx
import pytest

pytestmark = pytest.mark.e2e

_PROJECT = "e2e-row_access"


def _client_for_caller(rest_url: str, caller: str | None) -> bigquery.Client:
    """Build a BigQuery client whose every request carries ``X-Bqemu-Caller``."""
    session = AuthorizedSession(AnonymousCredentials())
    if caller is not None:
        session.headers["X-Bqemu-Caller"] = caller
    return bigquery.Client(
        project=_PROJECT,
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=rest_url),
        _http=session,
    )


@pytest.fixture
def bq_eu(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    client = _client_for_caller(bqemu_rest_url, "user:eu-analyst@example.com")
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def bq_other(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    client = _client_for_caller(bqemu_rest_url, "user:other@example.com")
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def bq_admin(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    """An admin-shaped client used to seed schema / policies (no caller)."""
    client = _client_for_caller(bqemu_rest_url, None)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def seeded(
    bqemu_rest_url: str,
    bq_admin: bigquery.Client,
) -> Iterator[None]:
    """Seed dataset + protected table + policy + authorized view."""
    ds_id = "row_access_ds"
    bq_admin.create_dataset(
        bigquery.Dataset(f"{_PROJECT}.{ds_id}"),
        exists_ok=True,
    )
    bq_admin.create_dataset(
        bigquery.Dataset(f"{_PROJECT}.row_access_v_ds"),
        exists_ok=True,
    )

    # Create the protected base table.
    bq_admin.create_table(
        bigquery.Table(
            f"{_PROJECT}.{ds_id}.orders",
            schema=[
                bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
                bigquery.SchemaField("region", "STRING"),
            ],
        ),
        exists_ok=True,
    )
    bq_admin.query(
        f"INSERT INTO `{_PROJECT}.{ds_id}.orders` "
        "VALUES (1, 'EU'), (2, 'EU'), (3, 'US'), (4, 'US')",
    ).result()

    # Create an authorized view in a different dataset.
    bq_admin.create_table(
        bigquery.Table(
            f"{_PROJECT}.row_access_v_ds.all_orders",
            schema=[],
        ),
        # `view` is set via the BigQuery client's view_query attribute on
        # the resource; create_table accepts the BigQuery REST shape too,
        # but easier to set directly afterwards via PATCH.
        exists_ok=True,
    )
    # Use raw REST to set the view body and authorize the view.
    with httpx.Client(base_url=bqemu_rest_url, timeout=30.0) as c:
        # Recreate with view body using REST API for clarity.
        c.delete(
            f"/bigquery/v2/projects/{_PROJECT}/datasets/row_access_v_ds/tables/all_orders",
        )
        c.post(
            f"/bigquery/v2/projects/{_PROJECT}/datasets/row_access_v_ds/tables",
            json={
                "tableReference": {
                    "projectId": _PROJECT,
                    "datasetId": "row_access_v_ds",
                    "tableId": "all_orders",
                },
                "view": {
                    "query": (f"SELECT id, region FROM `{_PROJECT}`.`{ds_id}`.`orders`"),
                },
            },
        )
        # Authorize the view in the protected dataset.
        c.patch(
            f"/bigquery/v2/projects/{_PROJECT}/datasets/{ds_id}",
            json={
                "access": [
                    {
                        "view": {
                            "projectId": _PROJECT,
                            "datasetId": "row_access_v_ds",
                            "tableId": "all_orders",
                        },
                    },
                ],
            },
        )

        # Create the row access policy.
        c.post(
            f"/bigquery/v2/projects/{_PROJECT}/datasets/{ds_id}/tables/orders/rowAccessPolicies",
            json={
                "rowAccessPolicyReference": {
                    "projectId": _PROJECT,
                    "datasetId": ds_id,
                    "tableId": "orders",
                    "policyId": "eu_only",
                },
                "filterPredicate": "region = 'EU'",
                "grantees": ["user:eu-analyst@example.com"],
            },
        )

    yield None

    # Teardown: drop the dataset + view dataset to leave a clean slate
    # for parallel runs.
    with httpx.Client(base_url=bqemu_rest_url, timeout=30.0) as c:
        c.delete(
            f"/bigquery/v2/projects/{_PROJECT}/datasets/{ds_id}",
            params={"deleteContents": "true"},
        )
        c.delete(
            f"/bigquery/v2/projects/{_PROJECT}/datasets/row_access_v_ds",
            params={"deleteContents": "true"},
        )


def _row_ids(job: bigquery.QueryJob) -> list[int]:
    return [int(row["id"]) for row in job.result()]


def test_row_access_eu_caller_sees_only_eu_rows(
    bq_eu: bigquery.Client,
    seeded: None,
) -> None:
    """The grantee sees the rows the policy filter matches."""
    job = bq_eu.query(f"SELECT id FROM `{_PROJECT}`.row_access_ds.orders ORDER BY id")
    assert _row_ids(job) == [1, 2]


def test_row_access_other_caller_sees_no_rows(
    bq_other: bigquery.Client,
    seeded: None,
) -> None:
    """A caller without a grant sees zero rows."""
    job = bq_other.query(f"SELECT id FROM `{_PROJECT}`.row_access_ds.orders")
    assert _row_ids(job) == []


def test_row_access_authorized_view_still_enforces_rap(
    bq_other: bigquery.Client,
    seeded: None,
) -> None:
    """An authorized view does NOT bypass RAP — caller-bound policy still applies.

    P2.d follow-up #1 (2026-05-18) reversed the ADR 0018 authorized-view
    bypass decision after empirical recording proved real BigQuery
    enforces row-level security UNIVERSALLY through views (the 5
    ``authz_view_*`` conformance fixtures recorded against real BQ in
    both same-dataset and cross-dataset topology all returned 0 rows
    for an UNGRANTED caller). The integration counterpart was renamed
    + flipped the same day; this E2E test was missed and updated in
    P2.d follow-up #2 (2026-05-18).
    """
    job = bq_other.query(
        f"SELECT id FROM `{_PROJECT}`.row_access_v_ds.all_orders ORDER BY id",
    )
    assert _row_ids(job) == []


def test_row_access_information_schema_row_access_policies(
    bq_admin: bigquery.Client,
    seeded: None,
) -> None:
    """``INFORMATION_SCHEMA.ROW_ACCESS_POLICIES`` lists the active policy."""
    job = bq_admin.query(
        "SELECT policy_name, table_name "
        f"FROM `{_PROJECT}`.row_access_ds.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
    )
    rows = [(row["policy_name"], row["table_name"]) for row in job.result()]
    assert ("eu_only", "orders") in rows
