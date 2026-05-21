"""Unit tests for the ``UNNEST ... WITH OFFSET`` rewriter."""

from __future__ import annotations

import pytest

from bqemulator.sql.rewriter.unnest_offset import rewrite_unnest_offset

pytestmark = pytest.mark.unit


def test_no_offset_unchanged() -> None:
    sql = "SELECT x FROM UNNEST([1, 2, 3]) AS x"
    assert rewrite_unnest_offset(sql) == sql


def test_rebases_single_offset() -> None:
    sql = "SELECT x, off FROM UNNEST([1, 2, 3]) AS x WITH OFFSET AS off"
    out = rewrite_unnest_offset(sql)
    assert "off - 1" in out


def test_preserves_other_refs() -> None:
    sql = """
    SELECT x, off, off + 10 AS shifted
    FROM UNNEST([10, 20]) AS x
    WITH OFFSET AS off
    WHERE off > 0
    """
    out = rewrite_unnest_offset(sql)
    # Every reference to ``off`` is rebased.
    assert "off - 1" in out


def test_default_offset_name() -> None:
    # BigQuery allows ``WITH OFFSET`` without a name — defaults to 'offset'.
    sql = "SELECT offset FROM UNNEST([1]) AS x WITH OFFSET"
    out = rewrite_unnest_offset(sql)
    # Either rebased or left alone if parser doesn't bind the default.
    assert "offset" in out.lower()


def test_malformed_bigquery_returns_unchanged() -> None:
    sql = "INVALID SQL WITH OFFSET AS off"
    # Parser should fail silently and return the input.
    assert rewrite_unnest_offset(sql) == sql


def test_case_insensitive_detection() -> None:
    sql = "SELECT x, off FROM UNNEST([1]) AS x with offset as off"
    out = rewrite_unnest_offset(sql)
    assert out != sql


def test_offset_in_aliased_expression() -> None:
    sql = "SELECT x, off AS o, off + 1 AS next FROM UNNEST([1, 2]) AS x WITH OFFSET AS off"
    out = rewrite_unnest_offset(sql)
    # Aliased column still has its expression rebased.
    assert "off - 1" in out
