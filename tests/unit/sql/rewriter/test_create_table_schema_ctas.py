"""Tests for the CTAS-with-schema pre-translator.

``CREATE OR REPLACE TABLE x (id INT64, country STRING) AS
SELECT 1 AS id, 'US' AS country`` is accepted by BigQuery but rejected
by DuckDB. The rewriter in
:mod:`bqemulator.sql.rewriter.create_table_schema_ctas` strips the
schema clause and wraps each ``SELECT`` projection in
``CAST(<value> AS <declared-type>) AS <declared-name>`` so the bare
``CREATE TABLE x AS SELECT …`` form DuckDB accepts produces a table
with exactly the user's declared column types.

Discovery: 2026-05-17 during P2.d Phase 8 conformance recording —
see the entry in CHANGELOG.md and
:mod:`bqemulator.sql.rewriter.create_table_schema_ctas` for the
full discovery / design rationale.
"""

from __future__ import annotations

import pytest
import sqlglot

from bqemulator.sql.rewriter.create_table_schema_ctas import (
    rewrite_create_table_schema_ctas,
)

pytestmark = pytest.mark.unit


def _normalise(sql: str) -> str:
    """Collapse whitespace so we can compare emitted SQL without churn."""
    return " ".join(sql.split())


class TestRewriteCreateTableSchemaCtas:
    """Pin the combined → bare-CTAS-with-casts transformation."""

    def test_basic_combined_form_decomposes(self) -> None:
        sql = (
            "CREATE OR REPLACE TABLE `p.ds.t` (id INT64, country STRING) AS "
            "SELECT 1 AS id, 'US' AS country"
        )
        out = rewrite_create_table_schema_ctas(sql)
        assert "CAST(1 AS INT64)" in out
        assert "CAST('US' AS STRING)" in out
        # Schema clause is gone — no ``(id INT64, country STRING)`` remains.
        assert "(id INT64" not in out
        # Bare CTAS shape preserved.
        assert "CREATE OR REPLACE TABLE" in out
        assert " AS SELECT" in out or " AS\nSELECT" in out

    def test_create_table_without_or_replace(self) -> None:
        """``CREATE TABLE`` (without ``OR REPLACE``) is also rewritten."""
        sql = "CREATE TABLE `p.ds.t` (a INT64) AS SELECT 7"
        out = rewrite_create_table_schema_ctas(sql)
        assert _normalise(out).upper().startswith("CREATE TABLE")
        assert "(a INT64)" not in out
        assert "CAST(7 AS INT64)" in out

    def test_numeric_literal_lands_with_declared_type(self) -> None:
        """A ``NUMERIC '100.00'`` literal stays NUMERIC under the cast.

        SQLGlot's BigQuery parser internally lowers a ``NUMERIC '100.00'``
        typed-literal to ``CAST('100.00' AS NUMERIC)``, so the outer
        cast layered on by the rewriter produces a double-cast string
        — harmless because DuckDB's optimiser collapses redundant
        casts, and the final column type is still ``NUMERIC``.
        """
        sql = (
            "CREATE OR REPLACE TABLE `p.ds.t` (amount NUMERIC) AS SELECT NUMERIC '100.00' AS amount"
        )
        out = rewrite_create_table_schema_ctas(sql)
        # The outer cast (to the declared NUMERIC type) is present.
        assert " AS NUMERIC) AS amount" in out
        # And the inner literal text survives.
        assert "'100.00'" in out

    def test_select_int_cast_to_numeric(self) -> None:
        """A bare integer SELECT projection promotes to the declared NUMERIC type."""
        sql = "CREATE OR REPLACE TABLE `p.ds.t` (amount NUMERIC) AS SELECT 100"
        out = rewrite_create_table_schema_ctas(sql)
        assert "CAST(100 AS NUMERIC)" in out

    def test_bare_ctas_passes_through_unchanged(self) -> None:
        """``CREATE TABLE x AS SELECT …`` (no schema clause) is unchanged."""
        sql = "CREATE OR REPLACE TABLE `p.ds.t` AS SELECT 1 AS id, 'US' AS country"
        out = rewrite_create_table_schema_ctas(sql)
        assert out == sql

    def test_bare_create_table_passes_through_unchanged(self) -> None:
        """``CREATE TABLE x (schema)`` (no ``AS SELECT``) is unchanged.

        The rewriter only kicks in when BOTH a schema clause and an
        ``AS SELECT`` are present. Plain DDL is DuckDB-compatible
        as-is.
        """
        sql = "CREATE TABLE `p.ds.t` (id INT64, country STRING)"
        out = rewrite_create_table_schema_ctas(sql)
        assert out == sql

    def test_select_passes_through_unchanged(self) -> None:
        """Pure ``SELECT`` queries are not modified."""
        sql = "SELECT 1 AS id, 'US' AS country"
        out = rewrite_create_table_schema_ctas(sql)
        assert out == sql

    def test_column_count_mismatch_leaves_sql_alone(self) -> None:
        """Schema has 3 columns; SELECT has 2 — leave SQL alone so the
        downstream parser surfaces the error.
        """
        sql = (
            "CREATE OR REPLACE TABLE `p.ds.t` (a INT64, b STRING, c NUMERIC) AS "
            "SELECT 1 AS a, 'x' AS b"
        )
        out = rewrite_create_table_schema_ctas(sql)
        assert out == sql

    def test_unparseable_sql_passes_through(self) -> None:
        """Unparseable SQL falls through so downstream layers surface the error."""
        sql = "this is not valid sql"
        out = rewrite_create_table_schema_ctas(sql)
        assert out == sql

    def test_column_names_preserved_from_schema(self) -> None:
        """Output column names come from the SCHEMA, not from the SELECT projection aliases.

        BigQuery uses the declared column names; if the SELECT
        projection has a different alias, the schema wins.
        """
        sql = (
            "CREATE OR REPLACE TABLE `p.ds.t` (alpha INT64, beta STRING) AS "
            "SELECT 1 AS some_other_name, 'US' AS country"
        )
        out = rewrite_create_table_schema_ctas(sql)
        # The output uses the schema's column names, not the SELECT aliases.
        assert " AS alpha" in out
        assert " AS beta" in out
        # The original SELECT aliases are NOT in the rewritten SQL.
        assert "some_other_name" not in out
        assert "country" not in out

    def test_rewriter_output_is_valid_sqlglot_bigquery(self) -> None:
        """The rewriter emits valid BigQuery SQL that SQLGlot can re-parse."""
        sql = (
            "CREATE OR REPLACE TABLE `p.ds.t` (id INT64, amount NUMERIC) AS "
            "SELECT 1, NUMERIC '100.00'"
        )
        out = rewrite_create_table_schema_ctas(sql)
        # Re-parse must succeed.
        sqlglot.parse_one(out, read="bigquery")

    def test_struct_type_in_schema_preserves_through_cast(self) -> None:
        """A ``STRUCT<…>`` type in the schema lands inside the CAST."""
        sql = (
            "CREATE OR REPLACE TABLE `p.ds.t` (s STRUCT<a INT64, b STRING>) AS "
            "SELECT STRUCT(1 AS a, 'x' AS b)"
        )
        out = rewrite_create_table_schema_ctas(sql)
        assert "STRUCT" in out
        # The CAST wraps the struct value with the declared STRUCT type.
        assert "CAST(" in out
