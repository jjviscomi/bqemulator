"""Canonical Arrow schemas for the reference Avro fixtures.

Each entry pairs a fixture name with the pyarrow schema the BigQuery
→ Avro mapping should produce. Used by:

* :func:`tests.integration.test_storage_read_avro.test_avro_schema_converter_against_reference_file`
  — runs every committed ``.avro`` file's embedded schema through
  :func:`fastavro.parse_schema` and asserts equality with the
  emulator's converted output.
* :file:`scripts/generate_avro_fixtures.py` — drives the regeneration
  pipeline (``make generate-avro-fixtures``) so the committed files
  stay in lock-step with the schema converter.

If a new conformance fixture lands, append its entry here and
re-run the generator.
"""

from __future__ import annotations

import pyarrow as pa

REFERENCE_SCHEMAS: dict[str, pa.Schema] = {
    "read_session_avro_basic": pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("value", pa.string(), nullable=False),
            pa.field("category", pa.string(), nullable=False),
        ]
    ),
    "read_session_avro_multi_stream": pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("payload", pa.string(), nullable=False),
        ]
    ),
    "read_session_avro_all_types": pa.schema(
        [
            pa.field("i", pa.int64(), nullable=False),
            pa.field("f", pa.float64(), nullable=False),
            pa.field("s", pa.string(), nullable=False),
            pa.field("b", pa.bool_(), nullable=False),
            pa.field("n", pa.decimal128(38, 9), nullable=False),
            pa.field("d", pa.date32(), nullable=False),
            pa.field("ts", pa.timestamp("us", tz="UTC"), nullable=False),
        ]
    ),
    "read_session_avro_nested_struct": pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field(
                "point",
                pa.struct(
                    [
                        pa.field("x", pa.int64(), nullable=False),
                        pa.field("y", pa.int64(), nullable=False),
                    ]
                ),
                nullable=False,
            ),
            pa.field("tags", pa.list_(pa.string()), nullable=False),
        ]
    ),
    "read_session_avro_with_projection": pa.schema(
        [
            # The projection drops every column except `a` and `c`.
            pa.field("a", pa.int64(), nullable=False),
            pa.field("c", pa.string(), nullable=False),
        ]
    ),
    "read_session_avro_split_read_stream": pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("kind", pa.string(), nullable=False),
        ]
    ),
}


__all__ = ["REFERENCE_SCHEMAS"]
