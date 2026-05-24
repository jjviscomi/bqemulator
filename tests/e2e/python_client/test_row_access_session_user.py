"""E2E: ``SESSION_USER()`` in a RAP filter predicate (ADR 0038).

Exercises the canonical "tenant isolation by email domain" pattern
end-to-end through the official ``google-cloud-bigquery`` Python
client:

1. Seed a ``tenants`` table with rows for two email-domain tenants
   (``example.com`` and ``other.com``).
2. Create a RAP policy whose filter predicate is
   ``REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = tenant_id``.
3. Grant the policy to ``allAuthenticatedUsers`` so per-caller
   visibility depends entirely on the *filter predicate*, not the
   grantee match.
4. Issue ``SELECT`` as two callers from different domains and assert
   each sees only their own tenant's rows.

The ``X-Bqemu-Caller`` header is injected at session construction
time (same pattern as :file:`test_row_access.py`).
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

_PROJECT = "e2e-row_access_session_user"
_DATASET = "session_user_ds"


def _client_for_caller(rest_url: str, caller: str | None) -> bigquery.Client:
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
def bq_admin(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    client = _client_for_caller(bqemu_rest_url, None)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def seeded(bqemu_rest_url: str, bq_admin: bigquery.Client) -> Iterator[None]:
    bq_admin.create_dataset(
        bigquery.Dataset(f"{_PROJECT}.{_DATASET}"),
        exists_ok=True,
    )
    bq_admin.create_table(
        bigquery.Table(
            f"{_PROJECT}.{_DATASET}.tenants",
            schema=[
                bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
                bigquery.SchemaField("tenant_id", "STRING"),
            ],
        ),
        exists_ok=True,
    )
    bq_admin.query(
        f"INSERT INTO `{_PROJECT}.{_DATASET}.tenants` VALUES "
        "(1, 'example.com'), (2, 'example.com'), "
        "(3, 'other.com'), (4, 'other.com')",
    ).result()
    with httpx.Client(base_url=bqemu_rest_url, timeout=30.0) as c:
        # ``raise_for_status`` so a broken RAP-creation path fails the
        # fixture immediately with a clear error instead of cascading
        # into a later data-mismatch assertion failure that's harder
        # to debug. (CodeRabbit thread PRRT_kwDOSkfuJ86EVwPD.)
        response = c.post(
            f"/bigquery/v2/projects/{_PROJECT}/datasets/{_DATASET}"
            "/tables/tenants/rowAccessPolicies",
            json={
                "rowAccessPolicyReference": {
                    "projectId": _PROJECT,
                    "datasetId": _DATASET,
                    "tableId": "tenants",
                    "policyId": "tenant_by_session_user",
                },
                "filterPredicate": ("REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = tenant_id"),
                "grantees": ["allAuthenticatedUsers"],
            },
        )
        response.raise_for_status()
    yield None
    with httpx.Client(base_url=bqemu_rest_url, timeout=30.0) as c:
        c.delete(
            f"/bigquery/v2/projects/{_PROJECT}/datasets/{_DATASET}",
            params={"deleteContents": "true"},
        )


def _row_ids(job: bigquery.QueryJob) -> list[int]:
    return [int(row["id"]) for row in job.result()]


def test_session_user_filter_example_com_caller(
    bqemu_rest_url: str,
    seeded: None,
) -> None:
    """``@example.com`` caller sees only the ``example.com`` tenant rows."""
    bq = _client_for_caller(bqemu_rest_url, "user:alice@example.com")
    try:
        job = bq.query(
            f"SELECT id FROM `{_PROJECT}.{_DATASET}.tenants` ORDER BY id",
        )
        assert _row_ids(job) == [1, 2]
    finally:
        bq.close()


def test_session_user_filter_other_com_caller(
    bqemu_rest_url: str,
    seeded: None,
) -> None:
    """``@other.com`` caller sees only the ``other.com`` tenant rows."""
    bq = _client_for_caller(bqemu_rest_url, "user:bob@other.com")
    try:
        job = bq.query(
            f"SELECT id FROM `{_PROJECT}.{_DATASET}.tenants` ORDER BY id",
        )
        assert _row_ids(job) == [3, 4]
    finally:
        bq.close()


def test_bare_select_current_user(bqemu_rest_url: str) -> None:
    """``SELECT CURRENT_USER()`` returns the caller's bare email (ADR 0040).

    ``CURRENT_USER()`` is documented as a co-equal alias for
    ``SESSION_USER()`` in BigQuery's reference; same caller-identity
    semantics, same pre-translator substitution.
    """
    bq = _client_for_caller(bqemu_rest_url, "user:dani@example.com")
    try:
        job = bq.query("SELECT CURRENT_USER() AS who")
        rows = list(job.result())
        assert rows[0]["who"] == "dani@example.com"
    finally:
        bq.close()


def test_bare_select_session_user_system_var(bqemu_rest_url: str) -> None:
    """``SELECT @@session.user`` returns the caller's bare email (ADR 0040).

    The system-variable spelling resolves via the same path
    as the function form — pinned here so a future SQLGlot AST
    change for ``@@session.user`` would surface as a test
    failure rather than silently producing the
    ``ANONYMOUS_CALLER`` literal.
    """
    bq = _client_for_caller(bqemu_rest_url, "user:eli@example.com")
    try:
        job = bq.query("SELECT @@session.user AS who")
        rows = list(job.result())
        assert rows[0]["who"] == "eli@example.com"
    finally:
        bq.close()


def test_bare_select_session_user(bqemu_rest_url: str) -> None:
    """``SELECT SESSION_USER()`` returns the caller's bare email.

    Not a RAP test, but uses the same pre-translator substitution path —
    pinning it here ensures the function works in free-form queries as
    well as inside row-access filter predicates.
    """
    bq = _client_for_caller(bqemu_rest_url, "user:claire@example.com")
    try:
        job = bq.query("SELECT SESSION_USER() AS who")
        rows = list(job.result())
        assert rows[0]["who"] == "claire@example.com"
    finally:
        bq.close()
