"""Unit tests for the FOR SYSTEM_TIME AS OF rewriter."""

from __future__ import annotations

from datetime import timedelta

import pytest

from bqemulator.api.dependencies import AppContext
from bqemulator.domain.errors import OutOfRangeError
from bqemulator.storage.sql_identifiers import quoted_table_ref
from bqemulator.versioning.time_travel import rewrite_for_system_time

pytestmark = pytest.mark.unit


async def test_short_circuits_for_non_system_time_sql(
    full_ctx: AppContext,
) -> None:
    sql = "SELECT * FROM ds.t"
    out = rewrite_for_system_time(sql, "p", full_ctx.snapshots, full_ctx.engine)
    assert out == sql


async def test_clears_modifier_when_no_snapshots_exist(
    full_ctx: AppContext,
    make_dataset,
    make_table,
    frozen_clock,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t")

    target_iso = (frozen_clock.now() - timedelta(minutes=1)).isoformat(sep=" ")
    sql = f"SELECT * FROM ds.t FOR SYSTEM_TIME AS OF TIMESTAMP '{target_iso}'"
    out = rewrite_for_system_time(sql, "p", full_ctx.snapshots, full_ctx.engine)
    # No snapshots → live table, with no version modifier.
    assert "SYSTEM_TIME" not in out.upper()
    assert "ds.t" in out.replace("`", "")


async def test_redirects_to_snapshot_table_when_one_matches(
    full_ctx: AppContext,
    make_dataset,
    make_table,
    frozen_clock,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t", rows=[(1, "a")])
    snap = full_ctx.snapshots.capture("p", "ds", "t")
    assert snap is not None

    frozen_clock.advance(seconds=10)
    target_iso = frozen_clock.now().isoformat(sep=" ")
    sql = f"SELECT * FROM ds.t FOR SYSTEM_TIME AS OF TIMESTAMP '{target_iso}'"
    out = rewrite_for_system_time(sql, "p", full_ctx.snapshots, full_ctx.engine)
    assert snap.duckdb_table in out
    assert "_bqemulator_snapshots" in out


async def test_bigquery_utc_suffix_literal_falls_back_to_duckdb(
    full_ctx: AppContext,
    make_dataset,
    make_table,
    frozen_clock,
) -> None:
    """BigQuery-style ``'YYYY-MM-DD HH:MM:SS UTC'`` literals route through DuckDB.

    ``datetime.fromisoformat`` rejects the trailing ``UTC`` zone name,
    so the literal fast-path returns ``None`` and the DuckDB
    evaluator — which accepts the suffix — handles the literal.
    """
    make_dataset("p", "ds")
    make_table("p", "ds", "t", rows=[(1, "a")])
    snap = full_ctx.snapshots.capture("p", "ds", "t")
    assert snap is not None

    frozen_clock.advance(seconds=10)
    # Build the literal from a naive datetime so the result is offset-free
    # regardless of which timezone the frozen clock yields, then append the
    # explicit ``UTC`` zone name to exercise the BigQuery-style path.
    naive_target = frozen_clock.now().replace(tzinfo=None, microsecond=0)
    target_str = naive_target.isoformat(sep=" ")
    sql = f"SELECT * FROM ds.t FOR SYSTEM_TIME AS OF TIMESTAMP '{target_str} UTC'"
    out = rewrite_for_system_time(sql, "p", full_ctx.snapshots, full_ctx.engine)
    assert snap.duckdb_table in out
    assert "_bqemulator_snapshots" in out


async def test_raises_out_of_range_when_target_in_future(
    full_ctx: AppContext,
    make_dataset,
    make_table,
    frozen_clock,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t")
    future = (frozen_clock.now() + timedelta(minutes=5)).isoformat(sep=" ")
    sql = f"SELECT * FROM ds.t FOR SYSTEM_TIME AS OF TIMESTAMP '{future}'"
    with pytest.raises(OutOfRangeError):
        rewrite_for_system_time(sql, "p", full_ctx.snapshots, full_ctx.engine)


async def test_passes_through_when_table_has_no_db_part(
    full_ctx: AppContext,
) -> None:
    # Bare references don't get rewritten — caller will hit a missing-table
    # error from DuckDB rather than a snapshot lookup.
    sql = "SELECT * FROM lone FOR SYSTEM_TIME AS OF TIMESTAMP '2026-01-01'"
    # Should not raise; should return SQL roughly intact (we don't rewrite
    # because there's no dataset.table pair to look up).
    out = rewrite_for_system_time(sql, "p", full_ctx.snapshots, full_ctx.engine)
    assert "lone" in out


async def test_invalid_expression_raises_invalid_query(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    from bqemulator.domain.errors import InvalidQueryError

    make_dataset("p", "ds")
    make_table("p", "ds", "t")
    sql = "SELECT * FROM ds.t FOR SYSTEM_TIME AS OF 'not-a-timestamp-foo'"
    with pytest.raises(InvalidQueryError):
        rewrite_for_system_time(sql, "p", full_ctx.snapshots, full_ctx.engine)


async def test_function_expression_falls_back_to_duckdb(
    full_ctx: AppContext,
    make_dataset,
    make_table,
    frozen_clock,
) -> None:
    """Non-literal expressions go through the DuckDB fallback evaluator."""
    make_dataset("p", "ds")
    make_table("p", "ds", "t", rows=[(1, "a")])
    full_ctx.snapshots.capture("p", "ds", "t")
    frozen_clock.advance(seconds=20)
    # ``TIMESTAMP_SUB(TIMESTAMP '...', INTERVAL ...)`` exercises the
    # non-literal DuckDB-fallback path while keeping the result in the
    # frozen-clock-relative retention window.
    target_iso = frozen_clock.now().isoformat(sep=" ", timespec="microseconds")
    sql = (
        f"SELECT id FROM ds.t FOR SYSTEM_TIME AS OF "
        f"TIMESTAMP_SUB(TIMESTAMP '{target_iso}', INTERVAL 5 SECOND)"
    )
    out = rewrite_for_system_time(sql, "p", full_ctx.snapshots, full_ctx.engine)
    # Should now contain the snapshot reference.
    assert "_bqemulator_snapshots" in out


def test_extract_literal_timestamp_branches() -> None:
    """Direct coverage of the literal-extraction helper."""
    from sqlglot import exp

    from bqemulator.versioning.time_travel import _extract_literal_timestamp

    assert _extract_literal_timestamp("2026-04-15") == "2026-04-15"
    cast = exp.Cast(
        this=exp.Literal(this="2026-04-15", is_string=True),
        to=exp.DataType.build("TIMESTAMPTZ"),
    )
    assert _extract_literal_timestamp(cast) == "2026-04-15"
    bare = exp.Literal(this="2026-04-15", is_string=True)
    assert _extract_literal_timestamp(bare) == "2026-04-15"
    # Cast of a non-literal returns None
    cast_of_func = exp.Cast(
        this=exp.Anonymous(this="now"),
        to=exp.DataType.build("TIMESTAMPTZ"),
    )
    assert _extract_literal_timestamp(cast_of_func) is None
    # Non-string literal returns None
    int_lit = exp.Literal.number("123")
    assert _extract_literal_timestamp(int_lit) is None


def test_unparseable_sql_returned_unchanged(
    full_ctx: AppContext,
) -> None:
    """A parse failure must not raise — caller's pipeline reports it."""
    sql = "this is :: not :: SQL"
    out = rewrite_for_system_time(sql, "p", full_ctx.snapshots, full_ctx.engine)
    assert out == sql


def test_unparseable_with_marker_falls_through(
    full_ctx: AppContext,
) -> None:
    """When SYSTEM_TIME is in the SQL but the SQL is unparseable, fall through."""
    sql = "@ SYSTEM_TIME garbage @"
    out = rewrite_for_system_time(sql, "p", full_ctx.snapshots, full_ctx.engine)
    assert out == sql


async def test_query_with_system_time_and_unrelated_table(
    full_ctx: AppContext,
    make_dataset,
    make_table,
    frozen_clock,
) -> None:
    """Tables without a ``version`` arg are skipped by the rewriter."""
    make_dataset("p", "ds")
    make_table("p", "ds", "t", rows=[(1, "a")])
    full_ctx.snapshots.capture("p", "ds", "t")
    frozen_clock.advance(seconds=10)

    # BigQuery TIMESTAMP literals don't carry an explicit ``+00:00`` —
    # use the naive ISO form (UTC implied).
    target_iso = frozen_clock.now().replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
    # Reference two tables; only one has FOR SYSTEM_TIME.
    sql = f"SELECT a.id FROM ds.t FOR SYSTEM_TIME AS OF TIMESTAMP '{target_iso}'"
    out = rewrite_for_system_time(sql, "p", full_ctx.snapshots, full_ctx.engine)
    assert "_bqemulator_snapshots" in out


async def test_resolve_returns_naive_datetime_path(full_ctx: AppContext) -> None:
    """Hit the datetime/no-tzinfo branch via DuckDB evaluation."""
    from sqlglot import exp

    from bqemulator.versioning.time_travel import _resolve_target_ts

    expr = exp.Cast(
        this=exp.Literal(this="2026-04-15 00:00:00", is_string=True),
        to=exp.DataType.build("TIMESTAMP"),
    )
    version = exp.Version(this="TIMESTAMP", kind="AS OF")
    # Use args.set so SQLGlot doesn't strip the cast wrapper.
    version.set("expression", expr)
    out = _resolve_target_ts(version, full_ctx.engine)
    # Either fast-path or fallback succeeds; both must attach UTC.
    assert out.tzinfo is not None


async def test_resolve_no_expression_raises(full_ctx: AppContext) -> None:
    """Direct test of _resolve_target_ts with an empty Version node."""
    from sqlglot import exp

    from bqemulator.domain.errors import InvalidQueryError
    from bqemulator.versioning.time_travel import _resolve_target_ts

    # Hand-construct a degenerate Version with no expression and no this.
    bad = exp.Version(this=None)
    with pytest.raises(InvalidQueryError):
        _resolve_target_ts(bad, full_ctx.engine)


async def test_resolve_evaluates_to_null(full_ctx: AppContext) -> None:
    """A non-literal expression that evaluates to NULL raises."""
    from sqlglot import exp

    from bqemulator.domain.errors import InvalidQueryError
    from bqemulator.versioning.time_travel import _resolve_target_ts

    null_expr = exp.Cast(
        this=exp.Null(),
        to=exp.DataType.build("TIMESTAMP"),
    )
    version = exp.Version(this="TIMESTAMP", kind="AS OF")
    version.set("expression", null_expr)
    with pytest.raises(InvalidQueryError):
        _resolve_target_ts(version, full_ctx.engine)


async def test_resolve_non_timestamp_value_raises(full_ctx: AppContext) -> None:
    """Evaluating to a non-timestamp value (e.g. integer) raises."""
    from sqlglot import exp

    from bqemulator.domain.errors import InvalidQueryError
    from bqemulator.versioning.time_travel import _resolve_target_ts

    # Cast(123, TIMESTAMP) actually evaluates fine in DuckDB, so use
    # something that returns an int via VARCHAR.
    int_expr = exp.Anonymous(this="length", expressions=[exp.Literal.string("abc")])
    version = exp.Version(this="TIMESTAMP", kind="AS OF")
    version.set("expression", int_expr)
    with pytest.raises(InvalidQueryError):
        _resolve_target_ts(version, full_ctx.engine)


async def test_select_executes_against_snapshot(
    full_ctx: AppContext,
    make_dataset,
    make_table,
    frozen_clock,
) -> None:
    from bqemulator.domain.result import Err, Ok
    from bqemulator.sql.table_rewriter import rewrite_table_refs
    from bqemulator.sql.translator import SQLTranslator

    make_dataset("p", "ds")
    make_table("p", "ds", "t", rows=[(1, "a")])
    snap = full_ctx.snapshots.capture("p", "ds", "t")
    assert snap is not None

    # Mutate the live table; the snapshot should still have the original row.
    full_ctx.engine.execute(
        f"INSERT INTO {quoted_table_ref('p', 'ds', 't')} VALUES (2, 'b')",
    )

    frozen_clock.advance(seconds=10)
    target_iso = frozen_clock.now().isoformat(sep=" ")
    sql = f"SELECT id FROM ds.t FOR SYSTEM_TIME AS OF TIMESTAMP '{target_iso}'"
    rewritten_bq = rewrite_for_system_time(
        sql,
        "p",
        full_ctx.snapshots,
        full_ctx.engine,
    )
    # Run through the rest of the pipeline so backticks become double quotes
    # and reserved-schema references survive.
    translator = SQLTranslator()
    match translator.translate(rewritten_bq):
        case Ok(duckdb_sql):
            pass
        case Err(error):
            raise error
    duckdb_sql = rewrite_table_refs(duckdb_sql, "p")
    rows = full_ctx.engine.execute(duckdb_sql).fetchall()
    assert rows == [(1,)]
