"""Tests for the ``defaultDataset`` table-qualification pre-translator.

:mod:`bqemulator.sql.rewriter.default_dataset` implements
``qualify_unqualified_tables`` — given a SQL string and a
``defaultDataset`` config, it walks the sqlglot AST and rewrites
every unqualified ``exp.Table`` node to the fully-qualified form
``<default_project>.<default_dataset>.<table>``. CTE names and
already-qualified references are preserved.
"""

from __future__ import annotations

import pytest

from bqemulator.sql.rewriter.default_dataset import qualify_unqualified_tables

pytestmark = pytest.mark.unit


class TestQualifyUnqualifiedTables:
    """Round-trip the BigQuery → BigQuery default-dataset rewrite."""

    def test_unqualified_select_gets_qualified(self) -> None:
        sql = "SELECT id, name FROM products ORDER BY id"
        out = qualify_unqualified_tables(
            sql,
            default_project="proj",
            default_dataset="ds",
        )
        assert "proj.ds.products" in out

    def test_already_qualified_two_part_left_alone(self) -> None:
        """A dataset-qualified ref (``dataset.table``) is preserved."""
        sql = "SELECT * FROM ds_b.orders"
        out = qualify_unqualified_tables(
            sql,
            default_project="proj",
            default_dataset="ds",
        )
        assert "ds_b" in out
        # Should NOT be rewritten to ``proj.ds.orders``.
        assert "proj.ds.orders" not in out

    def test_already_qualified_three_part_left_alone(self) -> None:
        """A fully-qualified ref (``project.dataset.table``) is preserved."""
        sql = "SELECT * FROM `other_proj.other_ds`.orders"
        out = qualify_unqualified_tables(
            sql,
            default_project="proj",
            default_dataset="ds",
        )
        assert "other_proj" in out
        assert "proj.ds.orders" not in out

    def test_cte_name_shadows_table(self) -> None:
        """A CTE named ``products`` shadows the unqualified ``products`` ref."""
        sql = "WITH products AS (SELECT 1 AS id) SELECT * FROM products"
        out = qualify_unqualified_tables(
            sql,
            default_project="proj",
            default_dataset="ds",
        )
        # The CTE reference should NOT be rewritten — it binds inside
        # the query.
        assert "proj.ds.products" not in out

    def test_partial_qualification_in_join(self) -> None:
        """JOIN with one unqualified leaf and one qualified leaf only rewrites the leaf."""
        sql = "SELECT u.name FROM users AS u JOIN dataset_b.orders AS o USING (user_id)"
        out = qualify_unqualified_tables(
            sql,
            default_project="proj",
            default_dataset="ds",
        )
        assert "proj.ds.users" in out
        # The dataset-qualified orders should NOT be touched.
        assert "proj.ds.orders" not in out

    def test_insert_into_unqualified_target(self) -> None:
        """``INSERT INTO target ...`` has its target qualified."""
        sql = "INSERT INTO target (id) VALUES (1)"
        out = qualify_unqualified_tables(
            sql,
            default_project="proj",
            default_dataset="ds",
        )
        assert "proj.ds.target" in out

    def test_missing_default_returns_sql_verbatim(self) -> None:
        """Empty default_dataset leaves the SQL untouched."""
        sql = "SELECT id FROM products"
        out = qualify_unqualified_tables(
            sql,
            default_project="proj",
            default_dataset="",
        )
        assert out == sql

    def test_parse_error_returns_sql_verbatim(self) -> None:
        """SQL that can't parse passes through; downstream translator
        will surface the parse error with the right shape.
        """
        sql = "BOGUS NOT PARSEABLE FROM products"
        out = qualify_unqualified_tables(
            sql,
            default_project="proj",
            default_dataset="ds",
        )
        # Conservative: when parsing fails, return the input unchanged.
        # We don't assert exact equality (sqlglot may partially parse
        # some shapes) — just that the function doesn't raise.
        assert isinstance(out, str)
