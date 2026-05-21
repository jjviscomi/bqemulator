"""Unit tests for the Arrow-to-BigQuery type helper in the jobs route.

The :func:`build_response_schema` contract is the **BigQuery REST**
wire format for query result schemas. REPEATED columns (Arrow list
types) are emitted as ``{type: <element_type>, mode: "REPEATED"}`` —
not ``{type: "RECORD", mode: "NULLABLE"}`` — so the
``google-cloud-bigquery`` row deserialiser can decode them. STRUCT
columns recurse to populate the nested ``fields`` array.

ADR 0023 §1.A documents the wire-format closure these tests lock in.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from bqemulator.jobs.executor import _arrow_type_to_bq_type, build_response_schema

pytestmark = pytest.mark.unit


class TestArrowTypeToBqType:
    @pytest.mark.parametrize(
        ("arrow_type", "expected"),
        [
            (pa.int64(), "INTEGER"),
            (pa.int32(), "INTEGER"),
            # ADR 0023 §1.B (2026-05-16): TINYINT / SMALLINT and their
            # unsigned variants all surface as INTEGER on the wire.
            # DuckDB's ``SIGN(INT)`` returns TINYINT (Arrow ``int8``)
            # and several small-width arithmetic shortcuts emit
            # ``int16`` / ``uint8`` / ``uint16``.
            (pa.int16(), "INTEGER"),
            (pa.int8(), "INTEGER"),
            (pa.uint64(), "INTEGER"),
            (pa.uint32(), "INTEGER"),
            (pa.uint16(), "INTEGER"),
            (pa.uint8(), "INTEGER"),
            (pa.float64(), "FLOAT"),
            (pa.float32(), "FLOAT"),
            (pa.bool_(), "BOOLEAN"),
            (pa.string(), "STRING"),
            (pa.large_string(), "STRING"),
            (pa.timestamp("us", tz="UTC"), "TIMESTAMP"),
            (pa.timestamp("us"), "DATETIME"),
            (pa.date32(), "DATE"),
            (pa.time64("us"), "TIME"),
            (pa.decimal128(38, 9), "NUMERIC"),
            # ADR 0023 §1.B (2026-05-16): scale > 9 marks BIGNUMERIC
            # (BigQuery NUMERIC has fixed scale 9; BIGNUMERIC carries
            # up to scale 38). The ``bqemu_to_bignumeric`` UDF emits
            # DECIMAL(38, 10) precisely so this rule fires.
            (pa.decimal128(38, 10), "BIGNUMERIC"),
            (pa.decimal128(38, 38), "BIGNUMERIC"),
            (pa.binary(), "BYTES"),
            (pa.struct([pa.field("x", pa.int64())]), "RECORD"),
            (pa.duration("us"), "STRING"),  # fallback
            # ADR 0023 §1.G (2026-05-16): DuckDB's INTERVAL surfaces as
            # ``month_day_nano_interval`` in Arrow; map to BigQuery
            # INTERVAL so the schema renderer doesn't fall through to
            # the STRING fallback.
            (pa.month_day_nano_interval(), "INTERVAL"),
            # LIST types unwrap to the element's BigQuery type. The
            # REPEATED mode is carried by build_response_schema, not
            # by this scalar helper.
            (pa.list_(pa.int64()), "INTEGER"),
            (pa.list_(pa.string()), "STRING"),
            (pa.large_list(pa.float64()), "FLOAT"),
            (pa.list_(pa.struct([pa.field("x", pa.int64())])), "RECORD"),
        ],
    )
    def test_type_mapping(self, arrow_type: pa.DataType, expected: str) -> None:
        assert _arrow_type_to_bq_type(arrow_type) == expected

    def test_hugeint_metadata_overrides_to_integer(self) -> None:
        """ADR 0023 §1.B: a column carrying ``bqemu.duckdb_type=HUGEINT``
        in its Arrow metadata surfaces as INTEGER even though the
        underlying Arrow type is ``decimal128(38, 0)`` (which would
        otherwise map to NUMERIC). DuckDB's ``SUM(BIGINT)`` and
        ``COUNT_IF(…)`` aggregates promote to HUGEINT.
        """
        from bqemulator.jobs.executor import _resolve_bq_type

        field = pa.field(
            "x",
            pa.decimal128(38, 0),
            metadata={b"bqemu.duckdb_type": b"HUGEINT"},
        )
        assert _resolve_bq_type(field, field.type) == "INTEGER"

    def test_decimal_scale_above_9_renders_as_bignumeric(self) -> None:
        """ADR 0023 §1.B: DECIMAL with scale > 9 → BIGNUMERIC."""
        from bqemulator.jobs.executor import _resolve_bq_type

        field = pa.field("n", pa.decimal128(38, 10))
        assert _resolve_bq_type(field, field.type) == "BIGNUMERIC"


class TestBuildResponseSchema:
    def test_multi_column(self) -> None:
        schema = pa.schema(
            [
                pa.field("id", pa.int64()),
                pa.field("name", pa.string()),
                pa.field("ts", pa.timestamp("us", tz="UTC")),
            ]
        )
        fields = build_response_schema(schema)
        assert len(fields) == 3
        assert fields[0] == {"name": "id", "type": "INTEGER", "mode": "NULLABLE"}
        assert fields[1] == {"name": "name", "type": "STRING", "mode": "NULLABLE"}
        assert fields[2] == {"name": "ts", "type": "TIMESTAMP", "mode": "NULLABLE"}

    def test_repeated_int_column(self) -> None:
        """REPEATED INTEGER renders as {type=INTEGER, mode=REPEATED}.

        Regression guard for ADR 0023 §1.A — the pre-closure shape
        was {type=RECORD, mode=NULLABLE}, which crashed the
        ``google-cloud-bigquery`` row deserialiser with
        ``TypeError: list indices must be integers or slices, not str``.
        """
        schema = pa.schema([pa.field("arr", pa.list_(pa.int64()))])
        fields = build_response_schema(schema)
        assert fields == [{"name": "arr", "type": "INTEGER", "mode": "REPEATED"}]

    def test_repeated_string_column(self) -> None:
        schema = pa.schema([pa.field("tags", pa.list_(pa.string()))])
        fields = build_response_schema(schema)
        assert fields == [{"name": "tags", "type": "STRING", "mode": "REPEATED"}]

    def test_struct_column_carries_nested_fields(self) -> None:
        """STRUCT columns carry the nested ``fields`` array."""
        struct_type = pa.struct([pa.field("x", pa.int64()), pa.field("y", pa.string())])
        schema = pa.schema([pa.field("s", struct_type)])
        fields = build_response_schema(schema)
        assert fields == [
            {
                "name": "s",
                "type": "RECORD",
                "mode": "NULLABLE",
                "fields": [
                    {"name": "x", "type": "INTEGER", "mode": "NULLABLE"},
                    {"name": "y", "type": "STRING", "mode": "NULLABLE"},
                ],
            },
        ]

    def test_repeated_struct_column(self) -> None:
        """REPEATED STRUCT renders mode=REPEATED with nested fields."""
        struct_type = pa.struct([pa.field("k", pa.string()), pa.field("v", pa.int64())])
        schema = pa.schema([pa.field("items", pa.list_(struct_type))])
        fields = build_response_schema(schema)
        assert fields == [
            {
                "name": "items",
                "type": "RECORD",
                "mode": "REPEATED",
                "fields": [
                    {"name": "k", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "v", "type": "INTEGER", "mode": "NULLABLE"},
                ],
            },
        ]

    def test_struct_of_repeated_column(self) -> None:
        """STRUCT containing a REPEATED field recurses with mode=REPEATED."""
        inner = pa.struct(
            [pa.field("name", pa.string()), pa.field("scores", pa.list_(pa.int64()))],
        )
        schema = pa.schema([pa.field("user", inner)])
        fields = build_response_schema(schema)
        assert fields == [
            {
                "name": "user",
                "type": "RECORD",
                "mode": "NULLABLE",
                "fields": [
                    {"name": "name", "type": "STRING", "mode": "NULLABLE"},
                    {"name": "scores", "type": "INTEGER", "mode": "REPEATED"},
                ],
            },
        ]


class TestRangeSchemaEntry:
    """ADR 0023 §1.G: a RANGE-shaped STRUCT surfaces as ``type=RANGE``."""

    def test_range_date_struct_metadata_renders_as_range(self) -> None:
        struct_type = pa.struct(
            [pa.field("start", pa.date32()), pa.field("end", pa.date32())],
        )
        field = pa.field(
            "r",
            struct_type,
            metadata={b"bqemu.duckdb_type": b'STRUCT("start" DATE, "end" DATE)'},
        )
        schema = pa.schema([field])
        assert build_response_schema(schema) == [
            {
                "name": "r",
                "type": "RANGE",
                "mode": "NULLABLE",
                "rangeElementType": {"type": "DATE"},
            },
        ]

    def test_range_datetime_struct_metadata_renders_as_range_datetime(self) -> None:
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
        assert build_response_schema(schema) == [
            {
                "name": "r",
                "type": "RANGE",
                "mode": "NULLABLE",
                "rangeElementType": {"type": "DATETIME"},
            },
        ]

    def test_repeated_range_renders_as_range_repeated(self) -> None:
        struct_type = pa.struct(
            [pa.field("start", pa.date32()), pa.field("end", pa.date32())],
        )
        field = pa.field(
            "ranges",
            pa.list_(struct_type),
            metadata={b"bqemu.duckdb_type": b'STRUCT("start" DATE, "end" DATE)[]'},
        )
        schema = pa.schema([field])
        assert build_response_schema(schema) == [
            {
                "name": "ranges",
                "type": "RANGE",
                "mode": "REPEATED",
                "rangeElementType": {"type": "DATE"},
            },
        ]

    def test_unrelated_struct_falls_through_to_record(self) -> None:
        """A regular STRUCT (not RANGE-shaped) keeps its RECORD wire type."""
        struct_type = pa.struct(
            [pa.field("x", pa.int64()), pa.field("y", pa.string())],
        )
        field = pa.field(
            "s",
            struct_type,
            metadata={b"bqemu.duckdb_type": b'STRUCT("x" INTEGER, "y" VARCHAR)'},
        )
        schema = pa.schema([field])
        entry = build_response_schema(schema)[0]
        assert entry["type"] == "RECORD"
