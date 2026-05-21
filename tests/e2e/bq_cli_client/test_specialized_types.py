"""E2E: Phase 9 GEOGRAPHY / RANGE / INTERVAL via bq CLI.

bq's query path renders these specialised types as strings in
``--format=json``; the assertions below match that contract (the
SDK suites assert against typed values via the official client
libraries).
"""

from __future__ import annotations

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e


def _mk_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    assert bq_runner.run("mk", "--dataset", "--location=US", ds_id).succeeded()


def _rm_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    bq_runner.run("rm", "-r", "-f", "-d", ds_id)


def test_st_dwithin(bq_runner: BqRunner) -> None:
    """``ST_DWITHIN`` filters geography points within a radius."""
    ds_id = "bq_cli_specialized_types_dwithin"
    table_fq = f"{ds_id}.places"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER,loc:GEOGRAPHY")
        bq_runner.run(
            "insert",
            table_fq,
            input_bytes=(
                b'{"id": 1, "loc": "POINT(0 0)"}\n'
                b'{"id": 2, "loc": "POINT(3 4)"}\n'
                b'{"id": 3, "loc": "POINT(10 10)"}\n'
            ),
        )
        out = bq_runner.query_json(
            f"SELECT id FROM `{table_fq}` "
            "WHERE ST_DWITHIN(loc, ST_GEOGFROMTEXT('POINT(0 0)'), 600000) "
            "ORDER BY id",
        )
        assert out == [{"id": "1"}, {"id": "2"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_st_intersects(bq_runner: BqRunner) -> None:
    """``ST_INTERSECTS`` detects geometric crossings."""
    ds_id = "bq_cli_specialized_types_intersects"
    table_fq = f"{ds_id}.shapes"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "name:STRING,shape:GEOGRAPHY")
        bq_runner.run(
            "insert",
            table_fq,
            input_bytes=(
                b'{"name": "horizontal", "shape": "LINESTRING(0 1, 5 1)"}\n'
                b'{"name": "vertical",   "shape": "LINESTRING(2 0, 2 5)"}\n'
                b'{"name": "far_away",   "shape": "LINESTRING(100 100, 200 200)"}\n'
            ),
        )
        out = bq_runner.query_json(
            f"SELECT name FROM `{table_fq}` "
            "WHERE ST_INTERSECTS(shape, ST_GEOGFROMTEXT('LINESTRING(0 0, 5 5)')) "
            "ORDER BY name",
        )
        assert out == [{"name": "horizontal"}, {"name": "vertical"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_range_contains(bq_runner: BqRunner) -> None:
    """``RANGE_CONTAINS`` evaluates half-open membership."""
    out = bq_runner.query_json(
        "SELECT "
        "RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), "
        "  DATE '2024-06-15') AS mid, "
        "RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), "
        "  DATE '2024-01-01') AS at_start, "
        "RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), "
        "  DATE '2024-12-31') AS at_end_exclusive",
    )
    assert out == [
        {"mid": "true", "at_start": "true", "at_end_exclusive": "false"},
    ]


def test_interval_arithmetic(bq_runner: BqRunner) -> None:
    """INTERVAL arithmetic on DATE/TIMESTAMP types.

    Real BigQuery widens ``DATE + INTERVAL DAY`` to ``DATETIME`` per
    the conformance fixture in ``sql_corpus/specialized_types/
    interval_arith_add``. The emulator's DuckDB backend produces a
    ``TIMESTAMP``; the rendered string differs by separator (``T``
    vs space) — both are valid wire shapes for "midnight on the next
    day". Cast the result back to ``DATE`` so the string assertion
    pins the date-level semantics without locking to either
    serialisation form.
    """
    out = bq_runner.query_json(
        "SELECT "
        "CAST(CAST(DATE '2024-01-15' + INTERVAL 1 DAY AS DATE) AS STRING) AS d_next, "
        "CAST(TIMESTAMP '2024-01-15 12:00:00 UTC' - INTERVAL 1 HOUR AS STRING) "
        "AS ts_prev",
    )
    assert out == [
        {
            "d_next": "2024-01-16",
            "ts_prev": "2024-01-15 11:00:00+00",
        },
    ]
