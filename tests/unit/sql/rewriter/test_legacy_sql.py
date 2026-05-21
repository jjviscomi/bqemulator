"""Unit tests for the legacy-SQL → standard-SQL pre-translator."""

from __future__ import annotations

import pytest

from bqemulator.sql.rewriter.legacy_sql import rewrite_legacy_to_standard

pytestmark = pytest.mark.unit


class TestLegacyTypeCastRewrite:
    """``INTEGER(x)`` / ``FLOAT(x)`` / etc. → ``CAST(x AS …)``."""

    def test_integer_cast(self) -> None:
        assert rewrite_legacy_to_standard("SELECT INTEGER(1)") == "SELECT CAST(1 AS INT64)"

    def test_float_cast(self) -> None:
        assert rewrite_legacy_to_standard("SELECT FLOAT(1)") == "SELECT CAST(1 AS FLOAT64)"

    def test_string_cast(self) -> None:
        assert rewrite_legacy_to_standard("SELECT STRING(1)") == "SELECT CAST(1 AS STRING)"

    def test_boolean_cast(self) -> None:
        assert rewrite_legacy_to_standard("SELECT BOOLEAN(1)") == "SELECT CAST(1 AS BOOL)"

    def test_bytes_cast(self) -> None:
        assert rewrite_legacy_to_standard("SELECT BYTES('a')") == "SELECT CAST('a' AS BYTES)"

    def test_nested_call_inside_legacy_cast(self) -> None:
        out = rewrite_legacy_to_standard("SELECT INTEGER(ABS(-1))")
        assert out == "SELECT CAST(ABS(-1) AS INT64)"

    def test_case_insensitive(self) -> None:
        assert rewrite_legacy_to_standard("SELECT integer(1)") == "SELECT CAST(1 AS INT64)"

    def test_no_match_passes_through(self) -> None:
        sql = "SELECT 1 AS n FROM `proj.dataset.tbl`"
        assert rewrite_legacy_to_standard(sql) == sql


class TestLegacyTableRefRewrite:
    """``[project:dataset.table]`` → ```project.dataset.table```."""

    def test_table_ref(self) -> None:
        out = rewrite_legacy_to_standard("SELECT * FROM [proj:ds.tbl]")
        assert out == "SELECT * FROM `proj.ds.tbl`"

    def test_hyphenated_project(self) -> None:
        out = rewrite_legacy_to_standard("SELECT * FROM [my-proj:ds.tbl]")
        assert out == "SELECT * FROM `my-proj.ds.tbl`"
