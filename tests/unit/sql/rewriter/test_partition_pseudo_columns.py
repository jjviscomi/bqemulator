"""Unit tests for the ``_PARTITIONDATE`` / ``_PARTITIONTIME`` rewriter."""

from __future__ import annotations

import pytest

from bqemulator.sql.rewriter.partition_pseudo_columns import (
    rewrite_partition_pseudo_columns,
)

pytestmark = pytest.mark.unit


class TestRewritePartitionPseudoColumns:
    """``_PARTITIONDATE`` → ``CURRENT_DATE()``, ``_PARTITIONTIME`` → ``CURRENT_TIMESTAMP()``."""

    def test_partitiondate_in_where(self) -> None:
        out = rewrite_partition_pseudo_columns(
            "SELECT * FROM t WHERE _PARTITIONDATE > DATE '1900-01-01'",
        )
        assert out == "SELECT * FROM t WHERE CURRENT_DATE() > DATE '1900-01-01'"

    def test_partitiontime_in_where(self) -> None:
        out = rewrite_partition_pseudo_columns(
            "SELECT * FROM t WHERE _PARTITIONTIME < TIMESTAMP '2000-01-01 00:00:00 UTC'",
        )
        assert (
            out == "SELECT * FROM t WHERE CURRENT_TIMESTAMP() < TIMESTAMP '2000-01-01 00:00:00 UTC'"
        )

    def test_case_insensitive(self) -> None:
        out = rewrite_partition_pseudo_columns(
            "SELECT _partitiondate FROM t",
        )
        assert out == "SELECT CURRENT_DATE() FROM t"

    def test_between_form(self) -> None:
        out = rewrite_partition_pseudo_columns(
            "SELECT * FROM t WHERE _PARTITIONDATE BETWEEN DATE '1900-01-01' AND DATE '2999-12-31'",
        )
        assert "CURRENT_DATE()" in out
        assert "_PARTITIONDATE" not in out

    def test_no_match_passes_through(self) -> None:
        sql = "SELECT id FROM t WHERE id > 1"
        assert rewrite_partition_pseudo_columns(sql) == sql

    def test_word_boundary_avoids_partial_match(self) -> None:
        """Identifiers that merely *contain* the pseudo-column name aren't rewritten."""
        sql = "SELECT my_partitiondate_column FROM t"
        # ``\b_PARTITIONDATE\b`` doesn't match this because the prefix
        # ``my_`` is part of the same identifier (no word boundary
        # before ``_PARTITIONDATE``).
        assert rewrite_partition_pseudo_columns(sql) == sql

    def test_both_pseudo_columns(self) -> None:
        out = rewrite_partition_pseudo_columns(
            "SELECT _PARTITIONDATE, _PARTITIONTIME FROM t",
        )
        assert out == "SELECT CURRENT_DATE(), CURRENT_TIMESTAMP() FROM t"
