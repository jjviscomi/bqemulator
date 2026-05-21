"""Tests for query parameter binding."""

from __future__ import annotations

import pytest

from bqemulator.sql.parameters import bind_parameters

pytestmark = pytest.mark.unit


class TestPositionalParameters:
    def test_no_params(self) -> None:
        sql, vals = bind_parameters("SELECT 1", None)
        assert sql == "SELECT 1"
        assert vals == []

    def test_single_positional(self) -> None:
        params = [
            {
                "parameterType": {"type": "INT64"},
                "parameterValue": {"value": "42"},
            },
        ]
        sql, vals = bind_parameters("SELECT * FROM t WHERE id = ?", params)
        assert "?" in sql  # DuckDB uses ? for positional
        assert vals == [42]

    def test_multiple_positional(self) -> None:
        params = [
            {"parameterType": {"type": "STRING"}, "parameterValue": {"value": "Alice"}},
            {"parameterType": {"type": "INT64"}, "parameterValue": {"value": "30"}},
        ]
        sql, vals = bind_parameters("SELECT * FROM t WHERE name = ? AND age > ?", params)
        assert "?" in sql
        assert vals == ["Alice", 30]


class TestNamedParameters:
    def test_single_named(self) -> None:
        params = [
            {
                "name": "min_amount",
                "parameterType": {"type": "FLOAT64"},
                "parameterValue": {"value": "9.99"},
            },
        ]
        sql, vals = bind_parameters("SELECT * FROM t WHERE amount > @min_amount", params)
        assert "?" in sql
        assert "@min_amount" not in sql
        assert vals == [9.99]

    def test_multiple_named(self) -> None:
        params = [
            {
                "name": "category",
                "parameterType": {"type": "STRING"},
                "parameterValue": {"value": "books"},
            },
            {
                "name": "limit",
                "parameterType": {"type": "INT64"},
                "parameterValue": {"value": "10"},
            },
        ]
        sql, vals = bind_parameters(
            "SELECT * FROM t WHERE cat = @category LIMIT @limit",
            params,
        )
        assert "@category" not in sql
        assert "@limit" not in sql
        assert "?" in sql
        assert len(vals) == 2


class TestTypeCoercion:
    def test_bool_true(self) -> None:
        params = [
            {"parameterType": {"type": "BOOL"}, "parameterValue": {"value": "true"}},
        ]
        _, vals = bind_parameters("SELECT ?", params)
        assert vals == [True]

    def test_bool_false(self) -> None:
        params = [
            {"parameterType": {"type": "BOOL"}, "parameterValue": {"value": "false"}},
        ]
        _, vals = bind_parameters("SELECT ?", params)
        assert vals == [False]

    def test_numeric(self) -> None:
        from decimal import Decimal

        params = [
            {"parameterType": {"type": "NUMERIC"}, "parameterValue": {"value": "12.50"}},
        ]
        _, vals = bind_parameters("SELECT ?", params)
        assert vals == [Decimal("12.50")]

    def test_null_value(self) -> None:
        params = [
            {"parameterType": {"type": "INT64"}, "parameterValue": {"value": None}},
        ]
        _, vals = bind_parameters("SELECT ?", params)
        assert vals == [None]


class TestArrayParameters:
    def test_array_of_ints(self) -> None:
        params = [
            {
                "parameterType": {
                    "type": "ARRAY",
                    "arrayType": {"type": "INT64"},
                },
                "parameterValue": {
                    "arrayValues": [
                        {"value": "1"},
                        {"value": "2"},
                        {"value": "3"},
                    ],
                },
            },
        ]
        _, vals = bind_parameters("SELECT ?", params)
        assert vals == [[1, 2, 3]]


class TestStructParameters:
    def test_simple_struct(self) -> None:
        params = [
            {
                "parameterType": {
                    "type": "STRUCT",
                    "structTypes": [
                        {"name": "x", "type": {"type": "INT64"}},
                        {"name": "y", "type": {"type": "STRING"}},
                    ],
                },
                "parameterValue": {
                    "structValues": {
                        "x": {"value": "42"},
                        "y": {"value": "hello"},
                    },
                },
            },
        ]
        _, vals = bind_parameters("SELECT ?", params)
        assert vals == [{"x": 42, "y": "hello"}]


class TestTemporalTypeCoercion:
    """P2.e: DATE / DATETIME / TIME / TIMESTAMP coerce to typed Python objects.

    DuckDB's prepared-statement interface infers a parameter's column
    type from the bound Python value's class. Passing a string leaks
    VARCHAR through to the BigQuery schema response; passing a typed
    object (``date`` / ``datetime`` / ``time``) preserves the declared
    BigQuery type.
    """

    def test_date_is_python_date(self) -> None:
        from datetime import date

        params = [
            {
                "name": "d",
                "parameterType": {"type": "DATE"},
                "parameterValue": {"value": "2024-01-15"},
            },
        ]
        _, vals = bind_parameters("SELECT @d", params)
        assert vals == [date(2024, 1, 15)]

    def test_timestamp_with_z_suffix_is_python_datetime(self) -> None:
        from datetime import UTC, datetime

        params = [
            {
                "name": "ts",
                "parameterType": {"type": "TIMESTAMP"},
                "parameterValue": {"value": "2024-01-15T12:00:00Z"},
            },
        ]
        _, vals = bind_parameters("SELECT @ts", params)
        assert vals == [datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)]

    def test_timestamp_with_offset_is_python_datetime(self) -> None:
        from datetime import UTC, datetime

        params = [
            {
                "name": "ts",
                "parameterType": {"type": "TIMESTAMP"},
                "parameterValue": {"value": "2024-01-15 12:00:00+00:00"},
            },
        ]
        _, vals = bind_parameters("SELECT @ts", params)
        assert vals == [datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)]

    def test_datetime_naive_python_datetime(self) -> None:
        from datetime import datetime

        params = [
            {
                "name": "dt",
                "parameterType": {"type": "DATETIME"},
                "parameterValue": {"value": "2024-01-15 12:30:45"},
            },
        ]
        _, vals = bind_parameters("SELECT @dt", params)
        assert vals == [datetime(2024, 1, 15, 12, 30, 45)]  # noqa: DTZ001 — DATETIME is naive in BQ

    def test_time_is_python_time(self) -> None:
        from datetime import time

        params = [
            {
                "name": "t",
                "parameterType": {"type": "TIME"},
                "parameterValue": {"value": "12:30:45"},
            },
        ]
        _, vals = bind_parameters("SELECT @t", params)
        assert vals == [time(12, 30, 45)]


class TestNullCastWrap:
    """P2.e: NULL-valued parameters are wrapped in CAST so DuckDB knows the type.

    Without the wrap, DuckDB defaults a bare ``?`` parameter to BIGINT
    regardless of the BQ-declared type, which causes the schema
    renderer to surface INTEGER instead of e.g. STRING. The wrap
    survives intact through to DuckDB and the schema renderer
    surfaces the declared BQ type.
    """

    def test_named_null_string_wrapped_with_varchar_cast(self) -> None:
        params = [
            {
                "name": "s",
                "parameterType": {"type": "STRING"},
                "parameterValue": {"value": None},
            },
        ]
        sql, vals = bind_parameters("SELECT @s AS s", params)
        assert "CAST(? AS VARCHAR)" in sql
        assert vals == [None]

    def test_named_null_date_wrapped_with_date_cast(self) -> None:
        params = [
            {
                "name": "d",
                "parameterType": {"type": "DATE"},
                "parameterValue": {"value": None},
            },
        ]
        sql, _ = bind_parameters("SELECT @d AS d", params)
        assert "CAST(? AS DATE)" in sql

    def test_named_non_null_value_keeps_bare_question_mark(self) -> None:
        params = [
            {
                "name": "n",
                "parameterType": {"type": "INT64"},
                "parameterValue": {"value": "7"},
            },
        ]
        sql, vals = bind_parameters("SELECT @n AS n", params)
        assert "CAST(" not in sql
        assert "?" in sql
        assert vals == [7]

    def test_positional_null_wrapped(self) -> None:
        params = [
            {"parameterType": {"type": "STRING"}, "parameterValue": {"value": "x"}},
            {"parameterType": {"type": "INT64"}, "parameterValue": {"value": None}},
        ]
        sql, vals = bind_parameters("SELECT ? AS a, ? AS b", params)
        # Only the second ``?`` should be wrapped.
        assert sql.count("CAST(? AS BIGINT)") == 1
        assert vals == ["x", None]

    def test_question_mark_inside_string_literal_preserved(self) -> None:
        """``?`` inside a quoted string literal is NOT a parameter marker."""
        params = [
            {"parameterType": {"type": "INT64"}, "parameterValue": {"value": None}},
        ]
        sql, _ = bind_parameters("SELECT 'wat?', ? AS x", params)
        # The literal '?' inside the string is untouched.
        assert "'wat?'" in sql
        # The bare ``?`` is wrapped because the value is NULL.
        assert "CAST(? AS BIGINT)" in sql
