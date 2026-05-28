"""Unit tests for the Arrow ↔ BigQuery REST JSON bridge."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pyarrow as pa
import pytest

from bqemulator.storage.arrow_bridge import (
    _detect_interval_form,
    arrow_table_to_bq_rows,
    arrow_type_to_bq_type_name,
    bq_rows_to_arrow,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Arrow → BQ REST JSON
# ---------------------------------------------------------------------------


class TestArrowToBqRows:
    def test_empty_table(self) -> None:
        table = pa.table({"a": pa.array([], type=pa.int64())})
        assert arrow_table_to_bq_rows(table) == []

    def test_zero_column_table_short_circuits(self) -> None:
        """``pa.table({})`` has 0 columns and 0 rows; slicing it inflates ``num_rows``.

        Regression: the slice quirk used to produce N rows of empty
        cells for DDL responses (CREATE FUNCTION / CREATE SNAPSHOT
        TABLE / …) which crashed the ``bq`` CLI's table-formatter
        ("max() iterable argument is empty"). The short-circuit
        ensures we always return ``[]`` here.
        """
        table = pa.table({})
        assert arrow_table_to_bq_rows(table, offset=0, limit=10000) == []

    def test_offset_without_limit_uses_slice(self) -> None:
        """``offset > 0`` with ``limit=None`` exercises the ``elif`` branch."""
        table = pa.table({"id": pa.array([1, 2, 3], type=pa.int64())})
        rows = arrow_table_to_bq_rows(table, offset=1)
        assert rows == [{"f": [{"v": "2"}]}, {"f": [{"v": "3"}]}]

    def test_zero_offset_no_limit_returns_whole_table(self) -> None:
        """Both arguments at defaults takes the ``else`` slice branch."""
        table = pa.table({"id": pa.array([5, 6], type=pa.int64())})
        rows = arrow_table_to_bq_rows(table)
        assert rows == [{"f": [{"v": "5"}]}, {"f": [{"v": "6"}]}]

    def test_single_int_row(self) -> None:
        table = pa.table({"id": pa.array([42], type=pa.int64())})
        rows = arrow_table_to_bq_rows(table)
        assert rows == [{"f": [{"v": "42"}]}]

    def test_multiple_columns(self) -> None:
        table = pa.table(
            {
                "id": pa.array([1], type=pa.int64()),
                "name": pa.array(["Alice"], type=pa.string()),
            }
        )
        rows = arrow_table_to_bq_rows(table)
        assert rows == [{"f": [{"v": "1"}, {"v": "Alice"}]}]

    def test_null_value(self) -> None:
        table = pa.table({"x": pa.array([None], type=pa.int64())})
        rows = arrow_table_to_bq_rows(table)
        assert rows == [{"f": [{"v": None}]}]

    def test_bool_as_string(self) -> None:
        table = pa.table({"flag": pa.array([True, False], type=pa.bool_())})
        rows = arrow_table_to_bq_rows(table)
        assert rows[0]["f"][0]["v"] == "true"
        assert rows[1]["f"][0]["v"] == "false"

    def test_float(self) -> None:
        table = pa.table({"x": pa.array([1.5], type=pa.float64())})
        rows = arrow_table_to_bq_rows(table)
        assert rows[0]["f"][0]["v"] == "1.5"

    def test_decimal_preserves_precision(self) -> None:
        table = pa.table({"amount": pa.array([Decimal("12.500000000")], type=pa.decimal128(38, 9))})
        rows = arrow_table_to_bq_rows(table)
        val = rows[0]["f"][0]["v"]
        assert "12.5" in val  # precision preserved as string

    def test_date(self) -> None:
        table = pa.table({"d": pa.array([date(2026, 4, 15)], type=pa.date32())})
        rows = arrow_table_to_bq_rows(table)
        assert rows[0]["f"][0]["v"] == "2026-04-15"

    def test_time(self) -> None:
        table = pa.table({"t": pa.array([time(14, 30, 0)], type=pa.time64("us"))})
        rows = arrow_table_to_bq_rows(table)
        assert "14:30:00" in rows[0]["f"][0]["v"]

    def test_timestamp_with_tz(self) -> None:
        # BigQuery REST wire format for TIMESTAMP is microseconds-since-
        # epoch encoded as a string — that is what the official Python
        # client parses via ``_timestamp_from_json``. 2026-04-15 12:00:00
        # UTC ≈ 1.776e15 microseconds since epoch.
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
        table = pa.table({"ts": pa.array([ts], type=pa.timestamp("us", tz="UTC"))})
        rows = arrow_table_to_bq_rows(table)
        val = rows[0]["f"][0]["v"]
        assert val.isdigit()
        # Round-trip back: microseconds → datetime should match.
        assert int(val) == int(ts.timestamp() * 1_000_000)

    def test_timestamp_without_tz(self) -> None:
        dt = datetime(2026, 4, 15, 12, 0, 0)  # noqa: DTZ001 — intentionally naive
        table = pa.table({"dt": pa.array([dt], type=pa.timestamp("us"))})
        rows = arrow_table_to_bq_rows(table)
        val = rows[0]["f"][0]["v"]
        assert "2026-04-15" in val
        assert "UTC" not in val

    def test_string(self) -> None:
        table = pa.table({"s": pa.array(["hello"], type=pa.string())})
        rows = arrow_table_to_bq_rows(table)
        assert rows[0]["f"][0]["v"] == "hello"

    def test_bytes_base64(self) -> None:
        table = pa.table({"b": pa.array([b"\x01\x02\x03"], type=pa.binary())})
        rows = arrow_table_to_bq_rows(table)
        import base64

        assert base64.b64decode(rows[0]["f"][0]["v"]) == b"\x01\x02\x03"

    def test_list_of_ints(self) -> None:
        table = pa.table({"arr": pa.array([[1, 2, 3]], type=pa.list_(pa.int64()))})
        rows = arrow_table_to_bq_rows(table)
        arr = rows[0]["f"][0]["v"]
        assert arr == [{"v": "1"}, {"v": "2"}, {"v": "3"}]

    def test_null_repeated_column_renders_as_empty_array(self) -> None:
        """ADR 0023 §1.A: REPEATED columns with NULL Arrow value
        render as ``[]`` — BigQuery REPEATED columns are never NULL
        on the wire, and the ``google-cloud-bigquery`` row parser
        iterates the value unconditionally (None is not iterable).
        """
        table = pa.table({"arr": pa.array([None], type=pa.list_(pa.int64()))})
        rows = arrow_table_to_bq_rows(table)
        assert rows == [{"f": [{"v": []}]}]

    def test_struct(self) -> None:
        struct_type = pa.struct(
            [
                pa.field("name", pa.string()),
                pa.field("age", pa.int64()),
            ]
        )
        table = pa.table(
            {
                "person": pa.array(
                    [{"name": "Alice", "age": 30}],
                    type=struct_type,
                ),
            }
        )
        rows = arrow_table_to_bq_rows(table)
        struct_val = rows[0]["f"][0]["v"]
        assert struct_val == {"f": [{"v": "Alice"}, {"v": "30"}]}

    def test_offset_and_limit(self) -> None:
        table = pa.table({"x": pa.array([10, 20, 30, 40, 50], type=pa.int64())})
        rows = arrow_table_to_bq_rows(table, offset=1, limit=2)
        assert len(rows) == 2
        assert rows[0]["f"][0]["v"] == "20"
        assert rows[1]["f"][0]["v"] == "30"

    def test_offset_only_no_limit(self) -> None:
        table = pa.table({"x": pa.array([10, 20, 30], type=pa.int64())})
        rows = arrow_table_to_bq_rows(table, offset=1)
        assert len(rows) == 2  # rows 20, 30

    def test_no_offset_no_limit(self) -> None:
        table = pa.table({"x": pa.array([1], type=pa.int64())})
        rows = arrow_table_to_bq_rows(table)
        assert len(rows) == 1

    def test_json_as_string(self) -> None:
        """JSON values render as strings in BQ REST format."""
        table = pa.table({"j": pa.array(['{"key": "val"}'], type=pa.string())})
        rows = arrow_table_to_bq_rows(table)
        assert rows[0]["f"][0]["v"] == '{"key": "val"}'

    def test_nested_array_of_structs(self) -> None:
        struct_type = pa.struct([pa.field("x", pa.int64())])
        arr_type = pa.list_(struct_type)
        table = pa.table(
            {
                "items": pa.array([[{"x": 1}, {"x": 2}]], type=arr_type),
            }
        )
        rows = arrow_table_to_bq_rows(table)
        arr = rows[0]["f"][0]["v"]
        assert len(arr) == 2
        assert arr[0]["v"]["f"][0]["v"] == "1"


# ---------------------------------------------------------------------------
# ADR 0023 §1.G — RANGE wire-format
# ---------------------------------------------------------------------------


class TestArrowToBqRowsRangeWireFormat:
    """A column tagged as RANGE via ``bqemu.duckdb_type`` metadata
    surfaces its cells as the BigQuery ``[start, end)`` string the
    Python client's ``_RANGE_PATTERN`` parses.
    """

    @staticmethod
    def _date_range_table() -> pa.Table:
        struct_type = pa.struct(
            [pa.field("start", pa.date32()), pa.field("end", pa.date32())],
        )
        field = pa.field(
            "r",
            struct_type,
            metadata={b"bqemu.duckdb_type": b'STRUCT("start" DATE, "end" DATE)'},
        )
        schema = pa.schema([field])
        values = [{"start": date(2024, 1, 1), "end": date(2024, 1, 31)}]
        return pa.Table.from_pydict({"r": values}, schema=schema)

    def test_date_range_renders_as_bracket_pair(self) -> None:
        rows = arrow_table_to_bq_rows(self._date_range_table())
        assert rows[0]["f"][0]["v"] == "[2024-01-01, 2024-01-31)"

    def test_unbounded_endpoints_render_as_unbounded(self) -> None:
        struct_type = pa.struct(
            [pa.field("start", pa.date32()), pa.field("end", pa.date32())],
        )
        field = pa.field(
            "r",
            struct_type,
            metadata={b"bqemu.duckdb_type": b'STRUCT("start" DATE, "end" DATE)'},
        )
        schema = pa.schema([field])
        values = [
            {"start": None, "end": date(2024, 1, 31)},
            {"start": date(2024, 1, 1), "end": None},
        ]
        table = pa.Table.from_pydict({"r": values}, schema=schema)
        rows = arrow_table_to_bq_rows(table)
        assert rows[0]["f"][0]["v"] == "[UNBOUNDED, 2024-01-31)"
        assert rows[1]["f"][0]["v"] == "[2024-01-01, UNBOUNDED)"

    def test_datetime_range_uses_iso_t_separator(self) -> None:
        struct_type = pa.struct(
            [
                pa.field("start", pa.timestamp("us")),
                pa.field("end", pa.timestamp("us")),
            ],
        )
        field = pa.field(
            "r",
            struct_type,
            metadata={
                b"bqemu.duckdb_type": b'STRUCT("start" TIMESTAMP, "end" TIMESTAMP)',
            },
        )
        schema = pa.schema([field])
        values = [
            {
                "start": datetime(2024, 1, 1, 0, 0, 0),  # noqa: DTZ001 — naive on purpose
                "end": datetime(2024, 2, 1, 0, 0, 0),  # noqa: DTZ001
            },
        ]
        table = pa.Table.from_pydict({"r": values}, schema=schema)
        rows = arrow_table_to_bq_rows(table)
        # ``T`` separator matches the BigQuery Python client's
        # ``_RFC3339_NO_FRACTION`` strptime pattern.
        assert rows[0]["f"][0]["v"] == "[2024-01-01T00:00:00, 2024-02-01T00:00:00)"

    def test_timestamp_range_uses_microseconds_since_epoch(self) -> None:
        struct_type = pa.struct(
            [
                pa.field("start", pa.timestamp("us", tz="UTC")),
                pa.field("end", pa.timestamp("us", tz="UTC")),
            ],
        )
        field = pa.field(
            "r",
            struct_type,
            metadata={
                b"bqemu.duckdb_type": (
                    b'STRUCT("start" TIMESTAMP WITH TIME ZONE, "end" TIMESTAMP WITH TIME ZONE)'
                ),
            },
        )
        schema = pa.schema([field])
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2024, 2, 1, 0, 0, 0, tzinfo=UTC)
        values = [{"start": start, "end": end}]
        table = pa.Table.from_pydict({"r": values}, schema=schema)
        rows = arrow_table_to_bq_rows(table)
        wire = rows[0]["f"][0]["v"]
        # ``timestamp_to_py`` parses each endpoint via ``int(value)``.
        assert wire.startswith("[")
        inner = wire[1:-1]  # drop ``[`` and ``)``
        start_str, end_str = (s.strip() for s in inner.split(","))
        assert int(start_str) == int(start.timestamp() * 1_000_000)
        assert int(end_str) == int(end.timestamp() * 1_000_000)

    def test_repeated_range_emits_list_of_v_cells(self) -> None:
        struct_type = pa.struct(
            [pa.field("start", pa.date32()), pa.field("end", pa.date32())],
        )
        field = pa.field(
            "ranges",
            pa.list_(struct_type),
            metadata={b"bqemu.duckdb_type": b'STRUCT("start" DATE, "end" DATE)[]'},
        )
        schema = pa.schema([field])
        values = [
            [
                {"start": date(2024, 1, 1), "end": date(2024, 2, 1)},
                {"start": date(2024, 2, 1), "end": date(2024, 3, 1)},
            ],
        ]
        table = pa.Table.from_pydict({"ranges": values}, schema=schema)
        rows = arrow_table_to_bq_rows(table)
        arr = rows[0]["f"][0]["v"]
        assert arr == [
            {"v": "[2024-01-01, 2024-02-01)"},
            {"v": "[2024-02-01, 2024-03-01)"},
        ]

    def test_null_range_cell_renders_as_null(self) -> None:
        struct_type = pa.struct(
            [pa.field("start", pa.date32()), pa.field("end", pa.date32())],
        )
        field = pa.field(
            "r",
            struct_type,
            metadata={b"bqemu.duckdb_type": b'STRUCT("start" DATE, "end" DATE)'},
        )
        schema = pa.schema([field])
        table = pa.Table.from_pydict({"r": [None]}, schema=schema)
        rows = arrow_table_to_bq_rows(table)
        assert rows[0]["f"][0]["v"] is None

    def test_interval_column_uses_canonical_y_m_d_string(self) -> None:
        """INTERVAL columns serialise to ``Y-M D H:M:S`` strings (locked
        in for ADR 0023 §1.G — the wire format must match the BigQuery
        Python client's ``_INTERVAL_PATTERN``).
        """
        # DuckDB-side MonthDayNano values: 14 months → 1 year + 2 months.
        from pyarrow import MonthDayNano

        arr = pa.array([MonthDayNano([14, 0, 0])], type=pa.month_day_nano_interval())
        table = pa.table({"i": arr})
        rows = arrow_table_to_bq_rows(table)
        assert rows[0]["f"][0]["v"] == "1-2 0 0:0:0"


# ---------------------------------------------------------------------------
# BQ REST JSON → Arrow (insertAll path)
# ---------------------------------------------------------------------------


class TestBqRowsToArrow:
    def test_empty_rows(self) -> None:
        schema = pa.schema([pa.field("x", pa.int64())])
        table = bq_rows_to_arrow([], schema)
        assert table.num_rows == 0

    def test_single_row_int_string(self) -> None:
        schema = pa.schema(
            [
                pa.field("id", pa.int64()),
                pa.field("name", pa.string()),
            ]
        )
        rows = [{"json": {"id": "42", "name": "Alice"}}]
        table = bq_rows_to_arrow(rows, schema)
        assert table.num_rows == 1
        assert table.column("id")[0].as_py() == 42
        assert table.column("name")[0].as_py() == "Alice"

    def test_null_handling(self) -> None:
        schema = pa.schema([pa.field("x", pa.int64())])
        rows = [{"json": {"x": None}}]
        table = bq_rows_to_arrow(rows, schema)
        assert table.column("x")[0].as_py() is None

    def test_bool_coercion(self) -> None:
        schema = pa.schema([pa.field("flag", pa.bool_())])
        rows = [
            {"json": {"flag": "true"}},
            {"json": {"flag": "false"}},
            {"json": {"flag": True}},
        ]
        table = bq_rows_to_arrow(rows, schema)
        assert table.column("flag").to_pylist() == [True, False, True]

    def test_float_coercion(self) -> None:
        schema = pa.schema([pa.field("val", pa.float64())])
        rows = [{"json": {"val": "3.14"}}]
        table = bq_rows_to_arrow(rows, schema)
        assert abs(table.column("val")[0].as_py() - 3.14) < 1e-9

    def test_list_coercion(self) -> None:
        schema = pa.schema([pa.field("tags", pa.list_(pa.string()))])
        rows = [{"json": {"tags": ["a", "b", "c"]}}]
        table = bq_rows_to_arrow(rows, schema)
        assert table.column("tags")[0].as_py() == ["a", "b", "c"]

    def test_flat_json_format(self) -> None:
        """Support rows without the 'json' wrapper (both formats accepted)."""
        schema = pa.schema([pa.field("x", pa.int64())])
        rows = [{"x": "1"}]
        table = bq_rows_to_arrow(rows, schema)
        assert table.column("x")[0].as_py() == 1

    def test_timestamp_string_coercion(self) -> None:
        """ISO-8601 timestamp strings are parsed into datetime objects."""
        schema = pa.schema([pa.field("ts", pa.timestamp("us", tz="UTC"))])
        rows = [{"json": {"ts": "2026-04-15T12:00:00Z"}}]
        table = bq_rows_to_arrow(rows, schema)
        val = table.column("ts")[0].as_py()
        assert val is not None
        assert val.year == 2026

    def test_timestamp_utc_suffix_coercion(self) -> None:
        """BigQuery-style '2026-04-15 12:00:00 UTC' format parsed correctly."""
        schema = pa.schema([pa.field("ts", pa.timestamp("us", tz="UTC"))])
        rows = [{"json": {"ts": "2026-04-15 12:00:00 UTC"}}]
        table = bq_rows_to_arrow(rows, schema)
        val = table.column("ts")[0].as_py()
        assert val is not None
        assert val.year == 2026

    def test_date_string_coercion(self) -> None:
        schema = pa.schema([pa.field("d", pa.date32())])
        rows = [{"json": {"d": "2026-04-15"}}]
        table = bq_rows_to_arrow(rows, schema)
        assert table.column("d")[0].as_py() == date(2026, 4, 15)

    def test_time_string_coercion(self) -> None:
        schema = pa.schema([pa.field("t", pa.time64("us"))])
        rows = [{"json": {"t": "14:30:00"}}]
        table = bq_rows_to_arrow(rows, schema)
        val = table.column("t")[0].as_py()
        assert val is not None
        assert val.hour == 14

    def test_decimal_coercion(self) -> None:
        schema = pa.schema([pa.field("amt", pa.decimal128(38, 9))])
        rows = [{"json": {"amt": "12.50"}}]
        table = bq_rows_to_arrow(rows, schema)
        val = table.column("amt")[0].as_py()
        assert val is not None

    def test_binary_base64_coercion(self) -> None:
        import base64

        schema = pa.schema([pa.field("b", pa.binary())])
        encoded = base64.b64encode(b"\x01\x02").decode("ascii")
        rows = [{"json": {"b": encoded}}]
        table = bq_rows_to_arrow(rows, schema)
        assert table.column("b")[0].as_py() == b"\x01\x02"

    def test_binary_passes_through_bytes(self) -> None:
        """Storage Write proto path feeds BYTES fields as raw ``bytes``."""
        schema = pa.schema([pa.field("b", pa.binary())])
        rows = [{"json": {"b": b"\x01\x02\x03"}}]
        table = bq_rows_to_arrow(rows, schema)
        assert table.column("b")[0].as_py() == b"\x01\x02\x03"

    def test_binary_malformed_base64_raises(self) -> None:
        """Bad base64 surfaces as a ``ValueError`` (binascii.Error subclass).

        Matches real BigQuery's ``400 invalid: Could not decode bytes``.
        Pre-refactor behavior silently produced partial bytes.
        """
        import binascii

        schema = pa.schema([pa.field("b", pa.binary())])
        rows = [{"json": {"b": "not-valid-base64!!"}}]
        with pytest.raises((binascii.Error, ValueError)):
            bq_rows_to_arrow(rows, schema)

    def test_binary_rejects_unsupported_type(self) -> None:
        """Anything other than str / bytes / bytearray raises ``TypeError``.

        Pre-refactor behavior went through ``bytes(value)`` which
        tolerated iterables of ints and masked real bugs.
        """
        schema = pa.schema([pa.field("b", pa.binary())])
        rows = [{"json": {"b": 42}}]
        with pytest.raises(TypeError, match="BYTES value must be"):
            bq_rows_to_arrow(rows, schema)

    def test_struct_coercion(self) -> None:
        struct_type = pa.struct(
            [
                pa.field("name", pa.string()),
                pa.field("age", pa.int64()),
            ]
        )
        schema = pa.schema([pa.field("person", struct_type)])
        rows = [{"json": {"person": {"name": "Alice", "age": 30}}}]
        table = bq_rows_to_arrow(rows, schema)
        val = table.column("person")[0].as_py()
        assert val["name"] == "Alice"
        assert val["age"] == 30


# ---------------------------------------------------------------------------
# Phase 9 — GEOGRAPHY / INTERVAL / RANGE
# ---------------------------------------------------------------------------


class TestSpecializedTypesGeography:
    """GEOGRAPHY columns round-trip through WKT and WKB hex respectively."""

    def test_geometry_column_emits_wkt(self) -> None:
        # Point(1, 2) WKB (little-endian).
        wkb = bytes.fromhex("0101000000000000000000F03F0000000000000040")
        field = pa.field(
            "g",
            pa.binary(),
            metadata={
                "ARROW:extension:name": "geoarrow.wkb",
                "ARROW:extension:metadata": "{}",
            },
        )
        table = pa.Table.from_arrays([pa.array([wkb], type=pa.binary())], schema=pa.schema([field]))
        rows = arrow_table_to_bq_rows(table)
        assert rows == [{"f": [{"v": "POINT (1 2)"}]}]

    def test_insertall_wkt_to_hex(self) -> None:
        field = pa.field("g", pa.string(), metadata={"bq_type": "GEOGRAPHY"})
        schema = pa.schema([field])
        rows = [{"json": {"g": "POINT(1 2)"}}]
        table = bq_rows_to_arrow(rows, schema)
        # The coerced value is hex-encoded WKB.
        hex_str = table.column("g")[0].as_py()
        assert isinstance(hex_str, str)
        assert hex_str.upper().startswith("0101")  # Little-endian POINT.

    def test_null_geography(self) -> None:
        field = pa.field(
            "g",
            pa.binary(),
            metadata={
                "ARROW:extension:name": "geoarrow.wkb",
                "ARROW:extension:metadata": "{}",
            },
        )
        table = pa.Table.from_arrays(
            [pa.array([None], type=pa.binary())],
            schema=pa.schema([field]),
        )
        rows = arrow_table_to_bq_rows(table)
        assert rows == [{"f": [{"v": None}]}]


class TestSpecializedTypesInterval:
    """INTERVAL columns emit BigQuery-canonical strings and accept them."""

    def test_emits_bq_canonical_string(self) -> None:
        # 1 month, 2 days, 3 hours (in nanoseconds).
        md_nanos = (1, 2, 3 * 3600 * 1_000_000_000)
        arr = pa.array([md_nanos], type=pa.month_day_nano_interval())
        table = pa.table({"v": arr})
        rows = arrow_table_to_bq_rows(table)
        assert rows == [{"f": [{"v": "0-1 2 3:0:0"}]}]

    def test_null_interval(self) -> None:
        arr = pa.array([None], type=pa.month_day_nano_interval())
        table = pa.table({"v": arr})
        rows = arrow_table_to_bq_rows(table)
        assert rows == [{"f": [{"v": None}]}]

    def test_insertall_parses_bq_string(self) -> None:
        field = pa.field(
            "v",
            pa.month_day_nano_interval(),
            metadata={"bq_type": "INTERVAL"},
        )
        schema = pa.schema([field])
        rows = [{"json": {"v": "1-2 3 4:5:6"}}]
        table = bq_rows_to_arrow(rows, schema)
        val = table.column("v")[0].as_py()
        assert val.months == 14  # 1 year + 2 months
        assert val.days == 3


class TestSpecializedTypesRange:
    """RANGE columns serialize through STRUCT round-trip."""

    def test_range_output(self) -> None:
        struct_type = pa.struct(
            [pa.field("start", pa.date32()), pa.field("end", pa.date32())],
        )
        arr = pa.array(
            [{"start": date(2024, 1, 1), "end": date(2024, 12, 31)}],
            type=struct_type,
        )
        table = pa.table({"r": arr})
        rows = arrow_table_to_bq_rows(table)
        # STRUCT emits BigQuery REST nested ``{f: [{v: start}, {v: end}]}``.
        struct_row = rows[0]["f"][0]["v"]
        assert struct_row["f"][0]["v"] == "2024-01-01"
        assert struct_row["f"][1]["v"] == "2024-12-31"

    def test_range_insertall(self) -> None:
        struct_type = pa.struct(
            [pa.field("start", pa.date32()), pa.field("end", pa.date32())],
        )
        field = pa.field("r", struct_type, metadata={"bq_type": "RANGE"})
        schema = pa.schema([field])
        rows = [{"json": {"r": {"start": "2024-01-01", "end": "2024-12-31"}}}]
        table = bq_rows_to_arrow(rows, schema)
        val = table.column("r")[0].as_py()
        assert val["start"] == date(2024, 1, 1)
        assert val["end"] == date(2024, 12, 31)


class TestDetectIntervalForm:
    """``_detect_interval_form`` selects the ``parse_interval_literal`` form.

    Pins the signature-tuple classification introduced when
    ``_bq_interval_string_to_tuple`` was de-nested below rank C — every
    branch of the original if/elif chain plus the unrecognised fallback,
    and that a leading sign is treated as part of the value (not a
    separator) so signed shorthands classify like their unsigned form.
    """

    @pytest.mark.parametrize(
        ("text", "form"),
        [
            ("1-2 3 4:5:6", "YEAR TO SECOND"),  # Y-M D H:M:S — dash in the day part
            ("3 4:5:6", "DAY TO SECOND"),  # D H:M:S — no dash in the day part
            ("4:5:6", "HOUR TO SECOND"),  # H:M:S — two colons
            ("4:5", "HOUR TO MINUTE"),  # H:M — one colon
            ("1-2", "YEAR TO MONTH"),  # Y-M — dash only
            ("5", "DAY"),  # bare day shorthand
            ("", "DAY"),  # empty → fallback (no split IndexError)
            ("1-2:3", "DAY"),  # dash + colon, no space → unrecognised → DAY
            # Signed shorthands — the leading sign is part of the value,
            # not a separator, so each classifies like its unsigned form.
            ("-4:5", "HOUR TO MINUTE"),
            ("-4:5:6", "HOUR TO SECOND"),
            ("-3", "DAY"),
            ("-1-2", "YEAR TO MONTH"),
            ("-1-2 3 4:5:6", "YEAR TO SECOND"),
        ],
    )
    def test_form_selection(self, text: str, form: str) -> None:
        assert _detect_interval_form(text) == form


class TestArrowTypeToBqTypeName:
    """``arrow_type_to_bq_type_name`` maps Arrow scalar types to BQ names."""

    @pytest.mark.parametrize(
        ("arrow_type", "name"),
        [
            (pa.int64(), "INTEGER"),
            (pa.int32(), "INTEGER"),
            (pa.float64(), "FLOAT"),
            (pa.bool_(), "BOOLEAN"),
            (pa.string(), "STRING"),
            (pa.large_string(), "STRING"),
            (pa.date32(), "DATE"),
            (pa.time64("us"), "TIME"),
            (pa.decimal128(38, 9), "NUMERIC"),
            (pa.binary(), "BYTES"),
            (pa.large_binary(), "BYTES"),  # must map to BYTES, not fall through to STRING
        ],
    )
    def test_scalar_mapping(self, arrow_type: pa.DataType, name: str) -> None:
        assert arrow_type_to_bq_type_name(arrow_type) == name

    def test_timestamp_tz_is_timestamp(self) -> None:
        assert arrow_type_to_bq_type_name(pa.timestamp("us", tz="UTC")) == "TIMESTAMP"

    def test_timestamp_naive_is_datetime(self) -> None:
        assert arrow_type_to_bq_type_name(pa.timestamp("us")) == "DATETIME"
