"""Property tests: BigQuery type round-trips.

Invariant: for every scalar BigQuery type, bq_to_duckdb(t) followed by
duckdb_to_bq(result) returns the original type name.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
import pytest

from bqemulator.storage.type_map import bq_to_duckdb, duckdb_to_bq

pytestmark = pytest.mark.property

_SCALAR_BQ_TYPES = [
    "INT64",
    "FLOAT64",
    "NUMERIC",
    "BIGNUMERIC",
    "BOOL",
    "STRING",
    "BYTES",
    "DATE",
    "TIME",
    "DATETIME",
    "TIMESTAMP",
    "JSON",
]


@given(bq_type=st.sampled_from(_SCALAR_BQ_TYPES))
def test_scalar_bq_round_trips(bq_type: str) -> None:
    duckdb_type = bq_to_duckdb(bq_type)
    assert duckdb_to_bq(duckdb_type) == bq_type


@given(
    element_type=st.sampled_from(_SCALAR_BQ_TYPES),
)
def test_array_round_trip(element_type: str) -> None:
    bq_array = f"ARRAY<{element_type}>"
    duckdb_type = bq_to_duckdb(bq_array)
    assert duckdb_to_bq(duckdb_type) == bq_array


@given(
    name1=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz",
        min_size=1,
        max_size=10,
    ),
    type1=st.sampled_from(_SCALAR_BQ_TYPES),
    name2=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz",
        min_size=1,
        max_size=10,
    ),
    type2=st.sampled_from(_SCALAR_BQ_TYPES),
)
def test_struct_round_trip(name1: str, type1: str, name2: str, type2: str) -> None:
    # Struct field names must differ to form a valid struct.
    if name1 == name2:
        return
    bq_struct = f"STRUCT<{name1} {type1}, {name2} {type2}>"
    duckdb_type = bq_to_duckdb(bq_struct)
    result = duckdb_to_bq(duckdb_type)
    assert result == bq_struct
