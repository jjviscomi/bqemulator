"""E2E: Phase 7 versioning — snapshots, clones, materialized views via bq CLI.

bq's snapshot/clone CLI shape is ``bq cp --snapshot`` and ``bq cp --clone``;
materialized views are created via ``CREATE MATERIALIZED VIEW`` through
``bq query`` (mirroring the SDK suites). Time travel is a SQL-level
feature (``FOR SYSTEM_TIME AS OF``) — same path as the SDK suites.
"""

from __future__ import annotations

import time

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e


def _mk_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    assert bq_runner.run("mk", "--dataset", "--location=US", ds_id).succeeded()


def _rm_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    bq_runner.run("rm", "-r", "-f", "-d", ds_id)


def _seed_orders(bq_runner: BqRunner, ds_id: str, rows: bytes) -> None:
    table_fq = f"{ds_id}.orders"
    bq_runner.run("mk", "--table", table_fq, "id:INTEGER,region:STRING,amount:INTEGER")
    bq_runner.run("insert", table_fq, input_bytes=rows)


def test_snapshot_via_bq_cp(bq_runner: BqRunner) -> None:
    """``bq cp --snapshot src dst`` captures an immutable copy."""
    ds_id = "bq_cli_versioning_snap"
    src = f"{ds_id}.orders"
    snap = f"{ds_id}.orders_snap"
    try:
        _mk_dataset(bq_runner, ds_id)
        _seed_orders(
            bq_runner,
            ds_id,
            b'{"id":1,"region":"US","amount":10}\n{"id":2,"region":"US","amount":20}\n',
        )

        result = bq_runner.run("cp", "--snapshot", "-f", src, snap)
        assert result.succeeded(), result.stderr

        # Mutate the source after the snapshot.
        bq_runner.run("insert", src, input_bytes=b'{"id":3,"region":"CA","amount":30}\n')

        # Source has 3 rows; snapshot has 2.
        src_count = bq_runner.query_json(f"SELECT COUNT(*) AS n FROM `{src}`")
        assert src_count == [{"n": "3"}]
        snap_count = bq_runner.query_json(f"SELECT COUNT(*) AS n FROM `{snap}`")
        assert snap_count == [{"n": "2"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_clone_via_bq_cp(bq_runner: BqRunner) -> None:
    """``bq cp --clone src dst`` produces a divergent table."""
    ds_id = "bq_cli_versioning_clone"
    src = f"{ds_id}.orders"
    clone = f"{ds_id}.workcopy"
    try:
        _mk_dataset(bq_runner, ds_id)
        _seed_orders(bq_runner, ds_id, b'{"id":1,"region":"US","amount":10}\n')

        result = bq_runner.run("cp", "--clone", "-f", src, clone)
        assert result.succeeded(), result.stderr

        bq_runner.run("insert", clone, input_bytes=b'{"id":99,"region":"NZ","amount":999}\n')

        src_rows = bq_runner.query_json(f"SELECT id FROM `{src}` ORDER BY id")
        assert src_rows == [{"id": "1"}]
        clone_rows = bq_runner.query_json(f"SELECT id FROM `{clone}` ORDER BY id")
        assert clone_rows == [{"id": "1"}, {"id": "99"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_materialized_view_refreshes_after_dml(bq_runner: BqRunner) -> None:
    """``CREATE MATERIALIZED VIEW`` keeps aggregates in sync after base-table DML."""
    ds_id = "bq_cli_versioning_mv"
    base = f"{ds_id}.orders"
    mv = f"{ds_id}.country_totals"
    try:
        _mk_dataset(bq_runner, ds_id)
        _seed_orders(
            bq_runner,
            ds_id,
            b'{"id":1,"region":"US","amount":10}\n'
            b'{"id":2,"region":"US","amount":5}\n'
            b'{"id":3,"region":"CA","amount":20}\n',
        )

        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            (
                f"CREATE MATERIALIZED VIEW `{mv}` AS "
                f"SELECT region, SUM(amount) AS total FROM `{base}` GROUP BY region"
            ),
        )
        assert result.succeeded(), result.stderr

        before = sorted(
            (r["region"], r["total"])
            for r in bq_runner.query_json(f"SELECT region, total FROM `{mv}`")
        )
        assert before == [("CA", "20"), ("US", "15")]

        # Add a row to the base table; MV must auto-refresh.
        bq_runner.run("insert", base, input_bytes=b'{"id":4,"region":"US","amount":100}\n')

        after = sorted(
            (r["region"], r["total"])
            for r in bq_runner.query_json(f"SELECT region, total FROM `{mv}`")
        )
        assert after == [("CA", "20"), ("US", "115")]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_time_travel_for_system_time(bq_runner: BqRunner) -> None:
    """``FOR SYSTEM_TIME AS OF`` via ``bq query`` returns historical rows.

    Captures the boundary via ``FORMAT_TIMESTAMP`` so the round-trip
    avoids the BigQuery REST TIMESTAMP wire-format ambiguity (integer
    microseconds for the official Python SDK vs floating-point seconds
    for ``bq query --format=json``'s formatter, which produces
    ``<date out of range for display>`` when handed the microsecond
    integer form).
    """
    ds_id = "bq_cli_versioning_tt"
    table_fq = f"{ds_id}.orders"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER")
        bq_runner.run("insert", table_fq, input_bytes=b'{"id":1}\n{"id":2}\n')

        # Wait, then capture a boundary timestamp as a string, then mutate.
        time.sleep(0.1)
        before_change = bq_runner.query_json(
            "SELECT FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S+00', CURRENT_TIMESTAMP()) AS ts",
        )
        boundary = before_change[0]["ts"]
        assert isinstance(boundary, str)
        time.sleep(0.1)

        bq_runner.run("insert", table_fq, input_bytes=b'{"id":3}\n')

        out = bq_runner.query_json(
            f"SELECT id FROM `{table_fq}` FOR SYSTEM_TIME AS OF TIMESTAMP '{boundary}' ORDER BY id",
        )
        # Time-travel returns the pre-change row set.
        assert out == [{"id": "1"}, {"id": "2"}]
    finally:
        _rm_dataset(bq_runner, ds_id)
