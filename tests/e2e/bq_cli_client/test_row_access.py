"""E2E: Phase 8 row access policies + authorized views via bq CLI.

Real BigQuery exposes RAP creation via ``CREATE ROW ACCESS POLICY``
DDL through any query interface; ``bq query`` is the canonical CLI
entry point. The emulator honors a caller header
(``X-Bqemu-Caller``) for testing — but bq itself doesn't set a
custom header, so caller-bound enforcement is exercised here as
"default-caller sees zero rows" which is what bq's anonymous
session looks like to the policy enforcer.

The full caller-grant matrix (grantee sees rows / other caller sees
none / authorized view does not bypass RAP) is covered by the
Python suite (which can inject ``X-Bqemu-Caller`` via
``AuthorizedSession``). The bq suite covers the DDL contract +
INFORMATION_SCHEMA enumeration.

ADR 0038's ``SESSION_USER()``-in-RAP-filter pattern is similarly out
of scope here for the same caller-header-injection reason — see
:file:`tests/e2e/python_client/test_row_access_session_user.py`
(+ the Node / Go / Java siblings) for the canonical "tenant
isolation by email domain" coverage.
"""

from __future__ import annotations

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e


def _mk_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    assert bq_runner.run("mk", "--dataset", "--location=US", ds_id).succeeded()


def _rm_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    bq_runner.run("rm", "-r", "-f", "-d", ds_id)


def test_create_row_access_policy_via_ddl(bq_runner: BqRunner) -> None:
    """``CREATE ROW ACCESS POLICY`` via ``bq query`` registers the policy."""
    ds_id = "bq_cli_row_access_rap"
    table_fq = f"{ds_id}.orders"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER,region:STRING")
        bq_runner.run(
            "insert",
            table_fq,
            input_bytes=(
                b'{"id":1,"region":"EU"}\n{"id":2,"region":"EU"}\n{"id":3,"region":"US"}\n'
            ),
        )

        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            (
                f"CREATE ROW ACCESS POLICY eu_only ON `{table_fq}` "
                "GRANT TO ('user:eu-analyst@example.test') "
                "FILTER USING (region = 'EU')"
            ),
        )
        assert result.succeeded(), result.stderr

        # INFORMATION_SCHEMA.ROW_ACCESS_POLICIES surfaces the new policy.
        out = bq_runner.query_json(
            f"SELECT policy_name FROM `{ds_id}`.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
        )
        assert out == [{"policy_name": "eu_only"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_authorized_view_creation_via_ddl(bq_runner: BqRunner) -> None:
    """``CREATE VIEW`` over a protected base table round-trips via bq query."""
    base_ds = "bq_cli_row_access_base"
    view_ds = "bq_cli_row_access_views"
    base = f"{base_ds}.orders"
    view = f"{view_ds}.all_orders"
    try:
        _mk_dataset(bq_runner, base_ds)
        _mk_dataset(bq_runner, view_ds)
        bq_runner.run("mk", "--table", base, "id:INTEGER,region:STRING")
        bq_runner.run("insert", base, input_bytes=b'{"id":1,"region":"EU"}\n')

        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            f"CREATE VIEW `{view}` AS SELECT id, region FROM `{base}`",
        )
        assert result.succeeded(), result.stderr

        # The view appears with table_type='VIEW' in INFORMATION_SCHEMA.TABLES.
        out = bq_runner.query_json(
            f"SELECT table_type FROM `{view_ds}`.INFORMATION_SCHEMA.TABLES "
            "WHERE table_name = 'all_orders'",
        )
        assert out == [{"table_type": "VIEW"}]
    finally:
        _rm_dataset(bq_runner, base_ds)
        _rm_dataset(bq_runner, view_ds)


def test_drop_row_access_policy(bq_runner: BqRunner) -> None:
    """``DROP ROW ACCESS POLICY`` removes the policy from INFORMATION_SCHEMA."""
    ds_id = "bq_cli_row_access_drop"
    table_fq = f"{ds_id}.t"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER,region:STRING")
        bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            (
                f"CREATE ROW ACCESS POLICY p1 ON `{table_fq}` "
                "GRANT TO ('user:a@example.test') FILTER USING (region = 'EU')"
            ),
        )
        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            f"DROP ROW ACCESS POLICY p1 ON `{table_fq}`",
        )
        assert result.succeeded(), result.stderr

        out = bq_runner.query_json(
            f"SELECT COUNT(*) AS n FROM `{ds_id}`.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
        )
        assert out == [{"n": "0"}]
    finally:
        _rm_dataset(bq_runner, ds_id)
