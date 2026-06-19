"""Property tests: autodetect schema inference (DuckDB type to BigQuery field).

``duckdb_type_to_bq_field`` is the core of load autodetect: it turns the
types DuckDB's ``read_csv_auto`` / ``read_json_auto`` infer (and that
``DESCRIBE`` reports) into BigQuery REST schema fields. Its surface is
combinatorial: scalars, arrays (``T[]`` / ``LIST(T)``), structs, and
arbitrary nesting of the three, each of which must map onto BigQuery's
legacy wire type names (``INTEGER`` / ``FLOAT`` / ``BOOLEAN`` / ``RECORD``)
and the correct ``NULLABLE`` / ``REPEATED`` mode.

The strategy below builds a structural model of an inferable column and
renders it two ways: to the DuckDB type string the converter consumes, and
to the BigQuery field the converter must produce. The headline invariant is
that the converter's output equals the independently rendered expectation,
so column names, ordering, nesting depth, the RECORD / REPEATED structure,
and the legacy wire type names all round-trip together.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from bqemulator.domain.errors import ValidationError
from bqemulator.storage.type_map import duckdb_type_to_bq_field

pytestmark = pytest.mark.property


@dataclass(frozen=True)
class _Scalar:
    """A scalar DuckDB type and the BigQuery legacy wire name it maps to."""

    duckdb: str
    wire: str


@dataclass(frozen=True)
class _Struct:
    """A DuckDB ``STRUCT`` modelled as ordered, named child nodes."""

    fields: tuple[tuple[str, _Node], ...]


@dataclass(frozen=True)
class _Node:
    """A column or field: a scalar or struct, optionally wrapped in one array."""

    base: _Scalar | _Struct
    repeated: bool


# DuckDB scalar -> BigQuery legacy wire name. The first three are the ones
# the bug hinged on: standard-SQL ``INT64`` / ``FLOAT64`` / ``BOOL`` must
# surface as ``INTEGER`` / ``FLOAT`` / ``BOOLEAN`` like real BigQuery. The
# two ``DECIMAL`` entries also exercise a comma inside a parameterised type
# when it appears as a struct field, stressing the struct parser's depth
# tracking.
_SCALARS: tuple[_Scalar, ...] = (
    _Scalar("BIGINT", "INTEGER"),
    _Scalar("DOUBLE", "FLOAT"),
    _Scalar("BOOLEAN", "BOOLEAN"),
    _Scalar("VARCHAR", "STRING"),
    _Scalar("BLOB", "BYTES"),
    _Scalar("DATE", "DATE"),
    _Scalar("TIME", "TIME"),
    _Scalar("TIMESTAMP", "DATETIME"),
    _Scalar("TIMESTAMPTZ", "TIMESTAMP"),
    _Scalar("JSON", "JSON"),
    _Scalar("DECIMAL(38, 9)", "NUMERIC"),
    _Scalar("DECIMAL(76, 38)", "BIGNUMERIC"),
)

# Identifiers safe as struct field / column names: no whitespace, commas, or
# brackets, so the struct parser splits them cleanly.
_IDENT = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,7}", fullmatch=True)


def _nodes() -> st.SearchStrategy[_Node]:
    """Strategy for an inferable column: scalar or nested struct, maybe arrayed."""
    leaves = st.builds(_Node, base=st.sampled_from(_SCALARS), repeated=st.booleans())

    def _extend(children: st.SearchStrategy[_Node]) -> st.SearchStrategy[_Node]:
        structs = st.lists(
            st.tuples(_IDENT, children),
            min_size=1,
            max_size=4,
            unique_by=lambda item: item[0],
        ).map(lambda fields: _Struct(tuple(fields)))
        return st.builds(_Node, base=structs, repeated=st.booleans())

    return st.recursive(leaves, _extend, max_leaves=8)


def _to_duckdb(node: _Node) -> str:
    """Render *node* as the DuckDB type string ``DESCRIBE`` would report."""
    base = node.base
    if isinstance(base, _Scalar):
        rendered = base.duckdb
    else:
        inner = ", ".join(f"{name} {_to_duckdb(child)}" for name, child in base.fields)
        rendered = f"STRUCT({inner})"
    return f"{rendered}[]" if node.repeated else rendered


def _to_bq_field(name: str, node: _Node) -> dict[str, Any]:
    """Render *node* as the BigQuery REST field the converter must produce."""
    mode = "REPEATED" if node.repeated else "NULLABLE"
    base = node.base
    if isinstance(base, _Scalar):
        return {"name": name, "type": base.wire, "mode": mode}
    return {
        "name": name,
        "type": "RECORD",
        "mode": mode,
        "fields": [_to_bq_field(child_name, child) for child_name, child in base.fields],
    }


def _walk(fields: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Yield every field in *fields*, descending into RECORD sub-fields."""
    for field in fields:
        yield field
        yield from _walk(field.get("fields", []))


@settings(max_examples=200, deadline=None)
@given(name=_IDENT, node=_nodes())
def test_inferred_field_matches_structural_model(name: str, node: _Node) -> None:
    """The converter reproduces the field rendered independently from the model.

    A single equality pins column name, ordering, nesting depth, the
    RECORD / REPEATED structure, and the legacy wire type names at once.
    """
    assert duckdb_type_to_bq_field(name, _to_duckdb(node)) == _to_bq_field(name, node)


@settings(max_examples=100, deadline=None)
@given(
    columns=st.lists(
        st.tuples(_IDENT, _nodes()),
        min_size=1,
        max_size=6,
        unique_by=lambda column: column[0],
    ),
)
def test_describe_rows_preserve_count_names_and_order(
    columns: list[tuple[str, _Node]],
) -> None:
    """A whole ``DESCRIBE`` result maps column-for-column, preserving order.

    This mirrors what ``_infer_autodetect_schema`` does with the rows of a
    ``DESCRIBE``: convert each ``(name, duckdb_type)`` pair independently.
    """
    inferred = [duckdb_type_to_bq_field(name, _to_duckdb(node)) for name, node in columns]

    assert len(inferred) == len(columns)
    assert [field["name"] for field in inferred] == [name for name, _ in columns]
    assert inferred == [_to_bq_field(name, node) for name, node in columns]


@settings(max_examples=200, deadline=None)
@given(name=_IDENT, node=_nodes())
def test_inferred_fields_use_legacy_wire_names_and_valid_modes(
    name: str,
    node: _Node,
) -> None:
    """Output never leaks a standard-SQL name and always has a coherent shape."""
    root = duckdb_type_to_bq_field(name, _to_duckdb(node))
    for field in _walk([root]):
        assert field["type"] not in {"INT64", "FLOAT64", "BOOL", "STRUCT"}
        assert field["mode"] in {"NULLABLE", "REPEATED"}
        if field["type"] == "RECORD":
            assert field["fields"]  # a RECORD always carries its sub-fields
        else:
            assert "fields" not in field  # scalars carry no sub-fields


@given(
    scalar=st.sampled_from(_SCALARS),
    template=st.sampled_from(("{t}[][]", "LIST({t}[])", "STRUCT(nested {t}[][])")),
)
def test_nested_array_is_rejected(scalar: _Scalar, template: str) -> None:
    """BigQuery has no ARRAY of ARRAY, so any nested array must raise."""
    with pytest.raises(ValidationError):
        duckdb_type_to_bq_field("col", template.format(t=scalar.duckdb))
