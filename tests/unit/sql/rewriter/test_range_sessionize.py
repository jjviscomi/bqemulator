"""Unit tests for the ``RANGE_SESSIONIZE`` pre-translator.

The rewriter operates at the SQL-text level — SQLGlot's BigQuery
parser rejects the ``TABLE <ref>`` keyword in TVF arguments, so the
rewrite must run before SQLGlot transpile. These tests pin both the
text-level behaviour of the rewrite (idempotency, argument parsing,
mode dispatch) and end-to-end execution against a live DuckDB
connection (correctness of the gaps-and-islands sessionisation
pattern for every documented sessionize-option).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.rewriter.range_sessionize import (
    _MODE_TO_OP,
    rewrite_range_sessionize,
)
from bqemulator.sql.translator import SQLTranslator


@pytest.fixture(scope="module")
def translator() -> SQLTranslator:
    """Module-scoped translator — the post-translate rule registry is shared."""
    return SQLTranslator()


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    """A fresh in-memory DuckDB connection per test."""
    return duckdb.connect(":memory:")


def _exec_bq(
    translator: SQLTranslator, conn: duckdb.DuckDBPyConnection, bq_sql: str
) -> list[tuple[Any, ...]]:
    """Translate ``bq_sql`` BigQuery → DuckDB and execute against ``conn``."""
    result = translator.translate(bq_sql)
    assert isinstance(result, Ok), f"translate failed: {result}"
    return conn.execute(result.value).fetchall()


class TestRewriteShape:
    """Pin the text-level rewrite output for each input shape."""

    def test_no_call_passes_through_unchanged(self) -> None:
        sql = "SELECT 1"
        assert rewrite_range_sessionize(sql) == sql

    def test_three_arg_call_defaults_to_meets_op(self) -> None:
        sql = "SELECT * FROM RANGE_SESSIONIZE(TABLE my_table, 'duration', ['user_id'])"
        out = rewrite_range_sessionize(sql)
        # MEETS uses strict ``>`` so touching ranges share a session.
        assert " > _bqemu_max_prior_end " in out
        assert ">=" not in out
        assert "_bqemu_session_id" in out
        assert "session_range" in out

    def test_four_arg_overlaps_uses_strict_gte(self) -> None:
        sql = "SELECT * FROM RANGE_SESSIONIZE(TABLE my_table, 'duration', ['user_id'], 'OVERLAPS')"
        out = rewrite_range_sessionize(sql)
        # OVERLAPS uses ``>=`` so touching ranges form separate sessions.
        assert " >= _bqemu_max_prior_end " in out

    def test_four_arg_overlaps_or_meets_raises_like_bigquery(self) -> None:
        # P2.a closure-bug follow-up (2026-05-18): empirical recording
        # against real BigQuery showed BQ rejects ``OVERLAPS_OR_MEETS``
        # as an invalid RANGE_SESSIONIZE_MODE (the docstring's claim
        # that it was an alias for ``MEETS`` was wrong). The closure
        # was updated to omit ``OVERLAPS_OR_MEETS`` from
        # ``_MODE_TO_OP`` so the unknown-mode branch raises
        # ``InvalidQueryError`` matching BigQuery's wire-format
        # ``Could not cast literal "OVERLAPS_OR_MEETS" to type
        # RANGE_SESSIONIZE_MODE`` error. See conformance fixture
        # ``specialized_types/range_sessionize_overlaps_or_meets_alias``.
        from bqemulator.domain.errors import InvalidQueryError

        sql = (
            "SELECT * FROM RANGE_SESSIONIZE("
            "TABLE my_table, 'duration', ['user_id'], 'OVERLAPS_OR_MEETS')"
        )
        with pytest.raises(InvalidQueryError) as exc_info:
            rewrite_range_sessionize(sql)
        assert 'Could not cast literal "OVERLAPS_OR_MEETS"' in str(exc_info.value)
        assert "RANGE_SESSIONIZE_MODE" in str(exc_info.value)

    def test_multi_partition_columns_emit_in_partition_clause(self) -> None:
        sql = "SELECT * FROM RANGE_SESSIONIZE(TABLE my_table, 'active', ['user_id', 'region'])"
        out = rewrite_range_sessionize(sql)
        # Both partition columns surface as backticked identifiers.
        assert "`user_id`" in out
        assert "`region`" in out

    def test_backticked_table_reference_preserved(self) -> None:
        sql = "SELECT * FROM RANGE_SESSIONIZE(TABLE `proj.ds.events`, 'duration', ['user_id'])"
        out = rewrite_range_sessionize(sql)
        assert "`proj.ds.events`" in out

    def test_unknown_mode_raises_invalid_query(self) -> None:
        # P3.a / ADR 0022 §3: an unrecognised mode literal raises
        # ``InvalidQueryError`` matching BigQuery's documented
        # ``Could not cast literal "X" to type RANGE_SESSIONIZE_MODE``
        # message. Previous behaviour (silently default to MEETS)
        # masked typos and diverged from BigQuery.
        from bqemulator.domain.errors import InvalidQueryError

        sql = "SELECT * FROM RANGE_SESSIONIZE(TABLE my_table, 'duration', ['user_id'], 'BOGUS')"
        with pytest.raises(InvalidQueryError) as exc_info:
            rewrite_range_sessionize(sql)
        assert "Could not cast literal" in str(exc_info.value)
        assert "RANGE_SESSIONIZE_MODE" in str(exc_info.value)

    def test_mode_to_op_only_carries_meets_and_overlaps(self) -> None:
        # P2.a closure-bug follow-up (2026-05-18): ``_MODE_TO_OP``
        # carries exactly two entries — ``MEETS`` (strict ``>``,
        # touching ranges stay in the same session) and ``OVERLAPS``
        # (non-strict ``>=``, touching ranges form separate
        # sessions). ``OVERLAPS_OR_MEETS`` was removed after the
        # 2026-05-18 fixture recording proved real BigQuery rejects
        # it (not an alias as the original docstring claimed).
        assert set(_MODE_TO_OP) == {"MEETS", "OVERLAPS"}
        assert _MODE_TO_OP["MEETS"] == ">"
        assert _MODE_TO_OP["OVERLAPS"] == ">="


class TestEndToEnd:
    """Translate + execute against DuckDB and assert real session ranges."""

    @staticmethod
    def _setup_events_meets(conn: duckdb.DuckDBPyConnection) -> None:
        """Three rows for alice — two meet, one is isolated."""
        conn.execute(
            'CREATE TABLE events (user_id VARCHAR, duration STRUCT("start" DATE, "end" DATE))'
        )
        conn.execute(
            "INSERT INTO events VALUES "
            "('alice', {'start': DATE '2024-01-01', 'end': DATE '2024-01-03'}), "
            "('alice', {'start': DATE '2024-01-03', 'end': DATE '2024-01-05'}), "
            "('alice', {'start': DATE '2024-01-10', 'end': DATE '2024-01-12'})"
        )

    def test_meets_default_joins_touching_ranges(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        self._setup_events_meets(conn)
        rows = _exec_bq(
            translator,
            conn,
            "SELECT user_id, duration, session_range "
            "FROM RANGE_SESSIONIZE(TABLE events, 'duration', ['user_id']) "
            "ORDER BY user_id, duration",
        )
        # Touching ranges (end == start of next) share a session under
        # the default MEETS mode; the isolated range stands alone.
        assert len(rows) == 3
        first_session = {"start": dt.date(2024, 1, 1), "end": dt.date(2024, 1, 5)}
        assert rows[0][2] == first_session
        assert rows[1][2] == first_session
        assert rows[2][2] == {"start": dt.date(2024, 1, 10), "end": dt.date(2024, 1, 12)}

    def test_overlaps_splits_touching_ranges(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        conn.execute(
            'CREATE TABLE windows (emp_id VARCHAR, duration STRUCT("start" DATE, "end" DATE))'
        )
        conn.execute(
            "INSERT INTO windows VALUES "
            "('e1', {'start': DATE '2024-01-01', 'end': DATE '2024-01-03'}), "
            "('e1', {'start': DATE '2024-01-03', 'end': DATE '2024-01-05'}), "
            "('e1', {'start': DATE '2024-01-04', 'end': DATE '2024-01-07'}), "
            "('e1', {'start': DATE '2024-01-20', 'end': DATE '2024-01-22'})"
        )
        rows = _exec_bq(
            translator,
            conn,
            "SELECT emp_id, duration, session_range "
            "FROM RANGE_SESSIONIZE(TABLE windows, 'duration', ['emp_id'], 'OVERLAPS') "
            "ORDER BY emp_id, duration",
        )
        # Under OVERLAPS the touching pair ``[01, 03)`` / ``[03, 05)``
        # splits into separate sessions; the strict overlap pair
        # ``[03, 05)`` / ``[04, 07)`` shares one.
        assert rows[0][2] == {"start": dt.date(2024, 1, 1), "end": dt.date(2024, 1, 3)}
        merged = {"start": dt.date(2024, 1, 3), "end": dt.date(2024, 1, 7)}
        assert rows[1][2] == merged
        assert rows[2][2] == merged
        assert rows[3][2] == {"start": dt.date(2024, 1, 20), "end": dt.date(2024, 1, 22)}

    def test_partition_columns_isolate_sessions(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        # Two partition columns; sessions cannot span across either.
        conn.execute(
            "CREATE TABLE sessions ("
            'user_id VARCHAR, region VARCHAR, active STRUCT("start" DATE, "end" DATE))'
        )
        conn.execute(
            "INSERT INTO sessions VALUES "
            "('alice', 'NORTH', {'start': DATE '2024-01-01', 'end': DATE '2024-01-03'}), "
            "('alice', 'NORTH', {'start': DATE '2024-01-03', 'end': DATE '2024-01-05'}), "
            "('alice', 'SOUTH', {'start': DATE '2024-01-04', 'end': DATE '2024-01-06'})"
        )
        rows = _exec_bq(
            translator,
            conn,
            "SELECT user_id, region, active, session_range "
            "FROM RANGE_SESSIONIZE(TABLE sessions, 'active', ['user_id', 'region']) "
            "ORDER BY user_id, region, active",
        )
        # NORTH's two rows merge; SOUTH's lone row stays alone — the
        # SOUTH range overlaps NORTH's chronologically but the
        # partition prevents cross-region sessionisation.
        north_session = {"start": dt.date(2024, 1, 1), "end": dt.date(2024, 1, 5)}
        assert rows[0][3] == north_session
        assert rows[1][3] == north_session
        assert rows[2][3] == {"start": dt.date(2024, 1, 4), "end": dt.date(2024, 1, 6)}

    def test_datetime_element_type_round_trips(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        # DATETIME-typed RANGE elements stay DATETIME through the
        # window's MIN/MAX — DuckDB's TIMESTAMP-naive type matches
        # BigQuery's DATETIME on the wire.
        conn.execute(
            'CREATE TABLE log (host VARCHAR, span STRUCT("start" TIMESTAMP, "end" TIMESTAMP))'
        )
        conn.execute(
            "INSERT INTO log VALUES "
            "('h1', {'start': TIMESTAMP '2024-01-01 00:00:00', "
            "       'end':   TIMESTAMP '2024-01-01 00:05:00'}), "
            "('h1', {'start': TIMESTAMP '2024-01-01 00:05:00', "
            "       'end':   TIMESTAMP '2024-01-01 00:10:00'})"
        )
        rows = _exec_bq(
            translator,
            conn,
            "SELECT host, span, session_range "
            "FROM RANGE_SESSIONIZE(TABLE log, 'span', ['host']) "
            "ORDER BY host, span",
        )
        merged_span = {
            "start": dt.datetime(2024, 1, 1, 0, 0, 0),  # noqa: DTZ001 — naive matches DuckDB TIMESTAMP
            "end": dt.datetime(2024, 1, 1, 0, 10, 0),  # noqa: DTZ001 — naive matches DuckDB TIMESTAMP
        }
        assert rows[0][2] == merged_span
        assert rows[1][2] == merged_span

    def test_null_range_bridges_partition_to_one_session(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        # P2.a closure-bug follow-up (2026-05-18): when any NULL
        # range is present in a partition, BigQuery's RANGE_SESSIONIZE
        # collapses every non-NULL row in that partition into a single
        # session spanning ``[min(start), max(end)]`` of all non-NULL
        # ranges; the NULL rows return ``session_range = NULL``.
        # The recorded
        # ``specialized_types/range_sessionize_null_range``
        # conformance fixture pins this contract against real BQ.
        conn.execute(
            'CREATE TABLE null_events (user_id VARCHAR, duration STRUCT("start" DATE, "end" DATE))'
        )
        conn.execute(
            "INSERT INTO null_events VALUES "
            "('alice', {'start': DATE '2024-01-01', 'end': DATE '2024-01-03'}), "
            "('alice', NULL), "
            "('alice', {'start': DATE '2024-01-05', 'end': DATE '2024-01-07'})"
        )
        rows = _exec_bq(
            translator,
            conn,
            "SELECT user_id, duration, session_range "
            "FROM RANGE_SESSIONIZE(TABLE null_events, 'duration', ['user_id']) "
            "ORDER BY user_id, duration NULLS FIRST",
        )
        # Ordering: NULL row first, then the two non-NULL rows by start.
        assert len(rows) == 3
        # NULL row → session_range NULL.
        assert rows[0][1] is None
        assert rows[0][2] is None
        # Both non-NULL rows → bridged single session spanning the
        # outer ``[min(start), max(end)]`` even though they are
        # otherwise separated by a 2-day gap that would normally end
        # the session under MEETS semantics.
        bridged = {"start": dt.date(2024, 1, 1), "end": dt.date(2024, 1, 7)}
        assert rows[1][2] == bridged
        assert rows[2][2] == bridged

    def test_no_null_range_preserves_meets_gap_split(
        self, translator: SQLTranslator, conn: duckdb.DuckDBPyConnection
    ) -> None:
        # Regression guard: the NULL-bridge logic must not change the
        # behaviour for partitions without NULL ranges. Two non-NULL
        # rows separated by a 2-day gap under default MEETS produce
        # two separate sessions, exactly as before the NULL-bridge
        # plumbing landed.
        conn.execute(
            'CREATE TABLE gap_events (user_id VARCHAR, duration STRUCT("start" DATE, "end" DATE))'
        )
        conn.execute(
            "INSERT INTO gap_events VALUES "
            "('bob', {'start': DATE '2024-01-01', 'end': DATE '2024-01-03'}), "
            "('bob', {'start': DATE '2024-01-05', 'end': DATE '2024-01-07'})"
        )
        rows = _exec_bq(
            translator,
            conn,
            "SELECT user_id, duration, session_range "
            "FROM RANGE_SESSIONIZE(TABLE gap_events, 'duration', ['user_id']) "
            "ORDER BY user_id, duration",
        )
        assert len(rows) == 2
        assert rows[0][2] == {"start": dt.date(2024, 1, 1), "end": dt.date(2024, 1, 3)}
        assert rows[1][2] == {"start": dt.date(2024, 1, 5), "end": dt.date(2024, 1, 7)}
