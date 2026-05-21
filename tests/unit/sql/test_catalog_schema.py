"""Tests for the catalog-derived schema-snapshot helper.

The helper walks a BigQuery-style SQL AST, finds every table
reference, looks each up in the catalog, and emits a SQLGlot-shaped
schema dict ``{table_id: {column_name: duckdb_type}}``. The translator
feeds this into ``annotate_types`` so the ``AvgDecimalRule`` (ADR 0023
§1.B) can decide whether to wrap an ``AVG`` operand in a DECIMAL
cast.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.models import (
    TableFieldSchema,
    TableMeta,
    TableSchema,
)
from bqemulator.sql.catalog_schema import build_catalog_schema

pytestmark = pytest.mark.unit


class _FakeCatalog:
    """Minimal in-memory stand-in for the production catalog."""

    def __init__(self, tables: dict[tuple[str, str, str], TableMeta]) -> None:
        self._tables = tables

    def get_table(self, project_id: str, dataset_id: str, table_id: str) -> TableMeta | None:
        return self._tables.get((project_id, dataset_id, table_id))


def _make_table(
    *,
    project_id: str = "test-project",
    dataset_id: str = "ds",
    table_id: str = "orders",
    fields: tuple[TableFieldSchema, ...] = (),
) -> TableMeta:
    now = datetime.now(UTC)
    return TableMeta(
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id,
        creation_time=now,
        last_modified_time=now,
        etag="x",
        schema=TableSchema(fields=fields),
    )


class TestBuildCatalogSchema:
    def test_three_part_reference(self) -> None:
        meta = _make_table(
            fields=(
                TableFieldSchema(name="order_id", type="INTEGER"),
                TableFieldSchema(name="amount", type="NUMERIC"),
            ),
        )
        cat = _FakeCatalog({("test-project", "ds", "orders"): meta})
        out = build_catalog_schema(
            "SELECT amount FROM `test-project.ds.orders`",
            project_id="test-project",
            catalog=cat,
        )
        assert out == {"orders": {"order_id": "BIGINT", "amount": "DECIMAL(38, 9)"}}

    def test_two_part_reference_uses_default_project(self) -> None:
        meta = _make_table(
            fields=(TableFieldSchema(name="amount", type="NUMERIC"),),
        )
        cat = _FakeCatalog({("test-project", "ds", "orders"): meta})
        out = build_catalog_schema(
            "SELECT amount FROM `ds.orders`",
            project_id="test-project",
            catalog=cat,
        )
        assert "orders" in out

    def test_legacy_wire_format_type_aliases(self) -> None:
        # Catalog rows from ddl_sync.py use the BQ wire-format names
        # ``INTEGER`` / ``FLOAT`` / ``BOOLEAN`` / ``RECORD``. The helper
        # must normalise them.
        meta = _make_table(
            fields=(
                TableFieldSchema(name="i", type="INTEGER"),
                TableFieldSchema(name="f", type="FLOAT"),
                TableFieldSchema(name="b", type="BOOLEAN"),
            ),
        )
        cat = _FakeCatalog({("test-project", "ds", "orders"): meta})
        out = build_catalog_schema(
            "SELECT i, f, b FROM `test-project.ds.orders`",
            project_id="test-project",
            catalog=cat,
        )
        assert out["orders"] == {"i": "BIGINT", "f": "DOUBLE", "b": "BOOLEAN"}

    def test_bare_table_ref_skipped(self) -> None:
        # Without a dataset hint the lookup is ambiguous; the helper
        # skips so the translator falls back to its un-annotated path.
        cat = _FakeCatalog({})
        out = build_catalog_schema(
            "SELECT amount FROM orders",
            project_id="test-project",
            catalog=cat,
        )
        assert out == {}

    def test_missing_table_returns_empty(self) -> None:
        cat = _FakeCatalog({})
        out = build_catalog_schema(
            "SELECT amount FROM `test-project.ds.orders`",
            project_id="test-project",
            catalog=cat,
        )
        assert out == {}

    def test_parse_failure_returns_empty(self) -> None:
        cat = _FakeCatalog({})
        out = build_catalog_schema(
            "THIS IS NOT VALID SQL ;;;",
            project_id="test-project",
            catalog=cat,
        )
        assert out == {}

    def test_unmappable_type_skipped(self) -> None:
        # An unrecognised type drops the column from the dict but
        # keeps the rest of the table's columns.
        meta = _make_table(
            fields=(
                TableFieldSchema(name="a", type="NUMERIC"),
                TableFieldSchema(name="b", type="MYSTERY_TYPE"),
            ),
        )
        cat = _FakeCatalog({("test-project", "ds", "orders"): meta})
        out = build_catalog_schema(
            "SELECT a, b FROM `test-project.ds.orders`",
            project_id="test-project",
            catalog=cat,
        )
        assert out == {"orders": {"a": "DECIMAL(38, 9)"}}
