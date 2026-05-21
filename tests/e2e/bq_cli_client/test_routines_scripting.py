"""E2E: Phase 6 routines + scripting via bq CLI.

``bq mk`` creates routines via ``--routine_type`` / ``--language`` /
``--definition_body``. Scripts (DECLARE / SET / IF / LOOP / EXCEPTION)
run through ``bq query``.
"""

from __future__ import annotations

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e


def _mk_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    assert bq_runner.run("mk", "--dataset", "--location=US", ds_id).succeeded()


def _rm_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    bq_runner.run("rm", "-r", "-f", "-d", ds_id)


def test_sql_scalar_udf_round_trip(bq_runner: BqRunner) -> None:
    """``CREATE FUNCTION`` via bq query + call from a query."""
    ds_id = "bq_cli_routines_scripting_sql_udf"
    try:
        _mk_dataset(bq_runner, ds_id)
        # bq's ``mk --routine`` flag matrix varies across SDK versions;
        # the portable path is ``CREATE FUNCTION`` via ``bq query``.
        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            f"CREATE FUNCTION `{ds_id}.add_one`(x INT64) AS (x + 1)",
        )
        assert result.succeeded(), result.stderr

        out = bq_runner.query_json(f"SELECT `{ds_id}.add_one`(41) AS r")
        assert out == [{"r": "42"}]

        # INFORMATION_SCHEMA.ROUTINES lists the new function.
        out = bq_runner.query_json(
            f"SELECT routine_name FROM `{ds_id}`.INFORMATION_SCHEMA.ROUTINES",
        )
        assert out == [{"routine_name": "add_one"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_scripting_declare_set_if(bq_runner: BqRunner) -> None:
    """A scripting block with DECLARE/SET/IF returns the final SELECT."""
    ds_id = "bq_cli_routines_scripting_script"
    try:
        _mk_dataset(bq_runner, ds_id)
        # Use ``DIV`` for explicit integer division — BigQuery's ``/``
        # always returns FLOAT64 from INT64 operands, which can't be
        # assigned back to an INT64 variable without an explicit cast.
        script = """
DECLARE n INT64 DEFAULT 5;
DECLARE total INT64 DEFAULT 0;
SET total = total + DIV(n * (n + 1), 2);
IF total > 0 THEN
  SELECT total AS answer;
ELSE
  SELECT -1 AS answer;
END IF;
"""
        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            "--format=json",
            script,
        )
        assert result.succeeded(), result.stderr
        rows = result.json()
        assert rows == [{"answer": "15"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_table_valued_function(bq_runner: BqRunner) -> None:
    """A TVF (CREATE TABLE FUNCTION) returns a row stream via a SELECT."""
    ds_id = "bq_cli_routines_scripting_tvf"
    try:
        _mk_dataset(bq_runner, ds_id)
        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            (
                f"CREATE TABLE FUNCTION `{ds_id}.one_to_n`(n INT64) AS "
                "SELECT i AS value FROM UNNEST(GENERATE_ARRAY(1, n)) AS i"
            ),
        )
        assert result.succeeded(), result.stderr

        out = bq_runner.query_json(
            f"SELECT value FROM `{ds_id}.one_to_n`(3) ORDER BY value",
        )
        assert out == [{"value": "1"}, {"value": "2"}, {"value": "3"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_javascript_udf(bq_runner: BqRunner) -> None:
    """A JavaScript UDF body executes via the V8 runtime."""
    ds_id = "bq_cli_routines_scripting_js_udf"
    try:
        _mk_dataset(bq_runner, ds_id)
        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            (
                f"CREATE FUNCTION `{ds_id}.js_double`(x INT64) RETURNS INT64 "
                'LANGUAGE js AS """return x * 2;"""'
            ),
        )
        assert result.succeeded(), result.stderr

        out = bq_runner.query_json(f"SELECT `{ds_id}.js_double`(21) AS r")
        assert out == [{"r": "42"}]
    finally:
        _rm_dataset(bq_runner, ds_id)
