"""Tests for the COLLATE specifier pre-translator.

:mod:`bqemulator.sql.rewriter.collate_specifier` handles the two
BigQuery-documented ``COLLATE(value, specifier)`` specifiers the
conformance corpus exercises: ``'und:ci'`` rewrites to ``LOWER(value)``
so equality on lower-cased operands matches the case-insensitive
Unicode default; ``'binary'`` rewrites to a ``error()`` call so the
recorded ``str_collate_binary`` error fixture's ``message_pattern`` is
matched by the existing :mod:`bqemulator.jobs.error_mapper` fallback.
"""

from __future__ import annotations

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.rewriter.collate_specifier import rewrite_collate_specifier
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture
def t() -> SQLTranslator:
    return SQLTranslator()


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def _execute(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> object:
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    return con.execute(result.value).fetchone()


class TestCaseInsensitiveSpecifier:
    """``COLLATE(value, 'und:ci')`` → ``LOWER(value)``."""

    def test_und_ci_yields_case_insensitive_equality(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        assert _execute(
            t,
            con,
            "SELECT COLLATE('Apple', 'und:ci') = COLLATE('apple', 'und:ci') AS result",
        ) == (True,)

    def test_und_ci_distinct_values_remain_distinct(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        assert _execute(
            t,
            con,
            "SELECT COLLATE('Apple', 'und:ci') = COLLATE('banana', 'und:ci') AS result",
        ) == (False,)

    def test_rewrite_emits_lower_calls(self) -> None:
        # The pre-translator returns SQL in BigQuery dialect; the rewrite
        # replaces ``COLLATE`` with ``LOWER``.
        sql = "SELECT COLLATE('X', 'und:ci')"
        rewritten = rewrite_collate_specifier(sql)
        assert "COLLATE" not in rewritten.upper()
        assert "LOWER" in rewritten.upper()


class TestBinarySpecifier:
    """``COLLATE(value, 'binary')`` → ``error('Collation 'binary' …')``."""

    def test_binary_specifier_raises_at_execution_time(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        translated = t.translate(
            "SELECT COLLATE('Apple', 'binary') = COLLATE('apple', 'binary') AS result"
        )
        assert isinstance(translated, Ok)
        with pytest.raises(duckdb.InvalidInputException) as excinfo:
            con.execute(translated.value).fetchone()
        assert "Collation 'binary' in collate function is not supported." in str(excinfo.value)


class TestPassThrough:
    """Specifiers we don't recognise + non-COLLATE SQL flow through unchanged."""

    def test_unknown_specifier_left_alone(self) -> None:
        sql = "SELECT COLLATE('X', 'en-US')"
        rewritten = rewrite_collate_specifier(sql)
        # No DuckDB-shape change because the rule only rewrites the two
        # recognised specifiers; the upstream transpile may still fail
        # later but the pre-translator is a no-op.
        assert "COLLATE" in rewritten.upper()

    def test_sql_without_collate_short_circuits(self) -> None:
        sql = "SELECT 1 + 2 AS r"
        # Returning the *exact* same string means no parse + serialise
        # round-trip happened — the short-circuit branch fired.
        assert rewrite_collate_specifier(sql) is sql

    def test_malformed_sql_returns_unchanged(self) -> None:
        # Parse failures fall through to the original SQL so the
        # downstream SQLGlot transpile reports its own error.
        malformed = "SELECT COLLATE(("
        assert rewrite_collate_specifier(malformed) == malformed
