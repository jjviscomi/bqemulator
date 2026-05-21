"""Unit tests for the G4 INFORMATION_SCHEMA rewriter expansion.

Covers ``SCHEMATA``, ``TABLES``, ``COLUMNS``, ``TABLE_OPTIONS``,
``VIEWS``, and ``PARTITIONS``. Each expander is exercised against an
in-memory catalog (no DuckDB engine wired); the partitions test path
asserts the empty-VALUES fallback because partition discovery
requires a live engine.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import (
    Clustering,
    DatasetMeta,
    RangePartitioning,
    TableFieldSchema,
    TableMeta,
    TableSchema,
    TimePartitioning,
)
from bqemulator.sql.rewriter.information_schema import (
    expand_information_schema,
    expand_information_schema_columns,
    expand_information_schema_partitions,
    expand_information_schema_schemata,
    expand_information_schema_table_options,
    expand_information_schema_tables,
    expand_information_schema_views,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)


def _empty_catalog() -> MemoryCatalogRepository:
    return MemoryCatalogRepository()


def _catalog_with_dataset(dataset_id: str = "ds") -> MemoryCatalogRepository:
    cat = MemoryCatalogRepository()
    cat.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id=dataset_id,
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
            location="US",
        ),
    )
    return cat


def _add_table(
    cat: MemoryCatalogRepository,
    *,
    table_id: str = "t",
    dataset_id: str = "ds",
    table_type: str = "TABLE",
    description: str | None = None,
    schema: TableSchema | None = None,
    time_partitioning: TimePartitioning | None = None,
    range_partitioning: RangePartitioning | None = None,
    clustering: Clustering | None = None,
    view_query: str | None = None,
    labels: dict[str, str] | None = None,
    friendly_name: str | None = None,
    expiration_time: datetime | None = None,
) -> TableMeta:
    table = TableMeta(
        project_id="p",
        dataset_id=dataset_id,
        table_id=table_id,
        table_type=table_type,  # type: ignore[arg-type]
        schema=schema or TableSchema(),
        description=description,
        labels=labels or {},
        friendly_name=friendly_name,
        time_partitioning=time_partitioning,
        range_partitioning=range_partitioning,
        clustering=clustering,
        expiration_time=expiration_time,
        creation_time=NOW,
        last_modified_time=NOW,
        view_query=view_query,
        etag="e",
    )
    cat.create_table(table)
    return table


# ---------------------------------------------------------------------------
# SCHEMATA
# ---------------------------------------------------------------------------


class TestSchemata:
    def test_no_reference_unchanged(self) -> None:
        cat = _empty_catalog()
        sql = "SELECT 1"
        assert expand_information_schema_schemata(sql, "p", cat) == sql

    def test_lists_datasets(self) -> None:
        cat = _catalog_with_dataset("ds_alpha")
        cat.create_dataset(
            DatasetMeta(
                project_id="p",
                dataset_id="ds_beta",
                creation_time=NOW,
                last_modified_time=NOW,
                etag="e",
                location="US",
            ),
        )
        sql = "SELECT schema_name FROM INFORMATION_SCHEMA.SCHEMATA"
        out = expand_information_schema_schemata(sql, "p", cat)
        assert "VALUES" in out
        assert "'ds_alpha'" in out
        assert "'ds_beta'" in out

    def test_project_qualified(self) -> None:
        cat = _catalog_with_dataset("ds_alpha")
        sql = "SELECT * FROM `p`.region_us.INFORMATION_SCHEMA.SCHEMATA"
        out = expand_information_schema_schemata(sql, "p", cat)
        assert "'ds_alpha'" in out

    def test_empty_project(self) -> None:
        cat = _empty_catalog()
        sql = "SELECT * FROM INFORMATION_SCHEMA.SCHEMATA"
        out = expand_information_schema_schemata(sql, "p", cat)
        assert "WHERE FALSE" in out


# ---------------------------------------------------------------------------
# TABLES
# ---------------------------------------------------------------------------


class TestTables:
    def test_lists_tables(self) -> None:
        cat = _catalog_with_dataset()
        _add_table(cat, table_id="orders")
        sql = "SELECT table_name FROM ds.INFORMATION_SCHEMA.TABLES"
        out = expand_information_schema_tables(sql, "p", cat)
        assert "'orders'" in out
        assert "'BASE TABLE'" in out
        assert "'YES'" in out  # is_insertable_into

    def test_view_table_type(self) -> None:
        cat = _catalog_with_dataset()
        _add_table(cat, table_id="v1", table_type="VIEW", view_query="SELECT 1")
        sql = "SELECT table_name, table_type FROM ds.INFORMATION_SCHEMA.TABLES"
        out = expand_information_schema_tables(sql, "p", cat)
        assert "'v1'" in out
        assert "'VIEW'" in out
        # is_insertable_into should be NO for views
        assert "'NO'" in out

    def test_project_qualified(self) -> None:
        cat = _catalog_with_dataset()
        _add_table(cat, table_id="x")
        sql = "SELECT * FROM `p`.ds.INFORMATION_SCHEMA.TABLES"
        out = expand_information_schema_tables(sql, "p", cat)
        assert "'x'" in out

    def test_empty_dataset_emits_empty_values(self) -> None:
        cat = _catalog_with_dataset()
        sql = "SELECT * FROM ds.INFORMATION_SCHEMA.TABLES"
        out = expand_information_schema_tables(sql, "p", cat)
        assert "WHERE FALSE" in out

    def test_bare_emits_empty_values(self) -> None:
        cat = _catalog_with_dataset()
        _add_table(cat, table_id="t")
        sql = "SELECT * FROM INFORMATION_SCHEMA.TABLES"
        out = expand_information_schema_tables(sql, "p", cat)
        assert "WHERE FALSE" in out


# ---------------------------------------------------------------------------
# COLUMNS
# ---------------------------------------------------------------------------


class TestColumns:
    def test_lists_columns_with_ordinals(self) -> None:
        cat = _catalog_with_dataset()
        schema = TableSchema(
            fields=(
                TableFieldSchema(name="id", type="INT64", mode="REQUIRED"),
                TableFieldSchema(name="name", type="STRING", mode="NULLABLE"),
                TableFieldSchema(name="tags", type="STRING", mode="REPEATED"),
            ),
        )
        _add_table(cat, table_id="t", schema=schema)
        sql = "SELECT * FROM ds.INFORMATION_SCHEMA.COLUMNS"
        out = expand_information_schema_columns(sql, "p", cat)
        assert "'id'" in out
        assert "'INT64'" in out
        assert "'NO'" in out  # is_nullable for REQUIRED
        assert "'name'" in out
        assert "'tags'" in out
        assert "ARRAY<STRING>" in out

    def test_struct_data_type(self) -> None:
        cat = _catalog_with_dataset()
        schema = TableSchema(
            fields=(
                TableFieldSchema(
                    name="addr",
                    type="RECORD",
                    mode="NULLABLE",
                    fields=(
                        TableFieldSchema(name="city", type="STRING"),
                        TableFieldSchema(name="zip", type="INT64"),
                    ),
                ),
            ),
        )
        _add_table(cat, table_id="t", schema=schema)
        sql = "SELECT * FROM ds.INFORMATION_SCHEMA.COLUMNS"
        out = expand_information_schema_columns(sql, "p", cat)
        assert "STRUCT<city STRING, zip INT64>" in out

    def test_partitioning_column_flagged(self) -> None:
        cat = _catalog_with_dataset()
        schema = TableSchema(
            fields=(TableFieldSchema(name="dt", type="DATE"),),
        )
        _add_table(
            cat,
            table_id="t",
            schema=schema,
            time_partitioning=TimePartitioning(type="DAY", field="dt"),
        )
        sql = "SELECT is_partitioning_column FROM ds.INFORMATION_SCHEMA.COLUMNS"
        out = expand_information_schema_columns(sql, "p", cat)
        # The partitioning column should be flagged YES — verified by
        # the row position of 'YES' in the is_partitioning_column slot
        # (14th column in the COLUMNS schema).
        assert "'dt', 1, 'YES'" in out
        # is_partitioning_column slot — 'YES' surrounded by the
        # is_system_defined='NO' to its left and clustering_ordinal_position to right.
        assert "'NO', 'YES', NULL" in out


# ---------------------------------------------------------------------------
# TABLE_OPTIONS
# ---------------------------------------------------------------------------


class TestTableOptions:
    def test_description_option(self) -> None:
        cat = _catalog_with_dataset()
        _add_table(cat, description="orders fact table")
        sql = "SELECT option_name, option_value FROM ds.INFORMATION_SCHEMA.TABLE_OPTIONS"
        out = expand_information_schema_table_options(sql, "p", cat)
        assert "'description'" in out
        assert "orders fact table" in out

    def test_labels_option_renders_as_array(self) -> None:
        cat = _catalog_with_dataset()
        _add_table(cat, labels={"team": "data", "env": "dev"})
        sql = "SELECT * FROM ds.INFORMATION_SCHEMA.TABLE_OPTIONS"
        out = expand_information_schema_table_options(sql, "p", cat)
        assert "'labels'" in out
        assert "ARRAY<STRUCT<STRING, STRING>>" in out
        # Sorted alphabetically by key
        assert '("env", "dev"), ("team", "data")' in out

    def test_require_partition_filter(self) -> None:
        cat = _catalog_with_dataset()
        _add_table(
            cat,
            time_partitioning=TimePartitioning(
                type="DAY",
                field="dt",
                require_partition_filter=True,
            ),
        )
        sql = "SELECT * FROM ds.INFORMATION_SCHEMA.TABLE_OPTIONS"
        out = expand_information_schema_table_options(sql, "p", cat)
        assert "'require_partition_filter'" in out
        assert "'BOOL'" in out

    def test_no_options_emits_empty(self) -> None:
        cat = _catalog_with_dataset()
        _add_table(cat)  # no options set
        sql = "SELECT * FROM ds.INFORMATION_SCHEMA.TABLE_OPTIONS"
        out = expand_information_schema_table_options(sql, "p", cat)
        assert "WHERE FALSE" in out


# ---------------------------------------------------------------------------
# VIEWS
# ---------------------------------------------------------------------------


class TestViews:
    def test_lists_views(self) -> None:
        cat = _catalog_with_dataset()
        _add_table(
            cat,
            table_id="v1",
            table_type="VIEW",
            view_query="SELECT id FROM orders",
        )
        _add_table(cat, table_id="raw_table")  # TABLE not VIEW — should be excluded
        sql = "SELECT table_name FROM ds.INFORMATION_SCHEMA.VIEWS"
        out = expand_information_schema_views(sql, "p", cat)
        assert "'v1'" in out
        assert "'raw_table'" not in out

    def test_view_definition_preserved(self) -> None:
        cat = _catalog_with_dataset()
        _add_table(
            cat,
            table_id="v",
            table_type="VIEW",
            view_query="SELECT 1 AS x",
        )
        sql = "SELECT view_definition FROM ds.INFORMATION_SCHEMA.VIEWS"
        out = expand_information_schema_views(sql, "p", cat)
        assert "'SELECT 1 AS x'" in out

    def test_empty_dataset(self) -> None:
        cat = _catalog_with_dataset()
        sql = "SELECT * FROM ds.INFORMATION_SCHEMA.VIEWS"
        out = expand_information_schema_views(sql, "p", cat)
        assert "WHERE FALSE" in out


# ---------------------------------------------------------------------------
# PARTITIONS
# ---------------------------------------------------------------------------


class TestPartitions:
    def test_no_engine_returns_empty(self) -> None:
        """Unit-test catalog has no engine → no partitions discoverable."""
        cat = _catalog_with_dataset()
        _add_table(
            cat,
            time_partitioning=TimePartitioning(type="DAY", field="dt"),
        )
        sql = "SELECT * FROM ds.INFORMATION_SCHEMA.PARTITIONS"
        out = expand_information_schema_partitions(sql, "p", cat)
        assert "WHERE FALSE" in out

    def test_empty_dataset(self) -> None:
        cat = _catalog_with_dataset()
        sql = "SELECT * FROM ds.INFORMATION_SCHEMA.PARTITIONS"
        out = expand_information_schema_partitions(sql, "p", cat)
        assert "WHERE FALSE" in out

    def test_bare_form_emits_empty(self) -> None:
        cat = _empty_catalog()
        sql = "SELECT * FROM INFORMATION_SCHEMA.PARTITIONS"
        out = expand_information_schema_partitions(sql, "p", cat)
        assert "WHERE FALSE" in out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class TestOrchestrator:
    def test_chains_all_views(self) -> None:
        cat = _catalog_with_dataset()
        _add_table(cat, table_id="t")
        # Query touching SCHEMATA + TABLES — orchestrator should
        # rewrite both in one pass.
        sql = (
            "WITH s AS (SELECT * FROM INFORMATION_SCHEMA.SCHEMATA), "
            "t AS (SELECT * FROM ds.INFORMATION_SCHEMA.TABLES) "
            "SELECT * FROM s CROSS JOIN t"
        )
        out = expand_information_schema(sql, "p", cat)
        assert "'ds'" in out
        assert "'t'" in out
        # No raw INFORMATION_SCHEMA references should remain
        assert "INFORMATION_SCHEMA.SCHEMATA" not in out
        assert "INFORMATION_SCHEMA.TABLES" not in out

    def test_range_partitioned_column_flagged(self) -> None:
        cat = _catalog_with_dataset()
        schema = TableSchema(
            fields=(TableFieldSchema(name="bucket", type="INT64"),),
        )
        _add_table(
            cat,
            table_id="t",
            schema=schema,
            range_partitioning=RangePartitioning(
                field="bucket",
                start=0,
                end=1000,
                interval=100,
            ),
        )
        sql = "SELECT is_partitioning_column FROM ds.INFORMATION_SCHEMA.COLUMNS"
        out = expand_information_schema_columns(sql, "p", cat)
        assert "'bucket', 1, 'YES'" in out

    def test_clustering_ordinal_position(self) -> None:
        cat = _catalog_with_dataset()
        schema = TableSchema(
            fields=(
                TableFieldSchema(name="a", type="STRING"),
                TableFieldSchema(name="b", type="STRING"),
                TableFieldSchema(name="c", type="STRING"),
            ),
        )
        _add_table(
            cat,
            table_id="t",
            schema=schema,
            clustering=Clustering(fields=("b", "c")),
        )
        sql = "SELECT column_name, clustering_ordinal_position FROM ds.INFORMATION_SCHEMA.COLUMNS"
        out = expand_information_schema_columns(sql, "p", cat)
        # 'a' has no cluster position; 'b' is 1; 'c' is 2
        assert "'a', 1, " in out  # ordinal_position=1 for 'a'
        assert "'b', 2, " in out  # ordinal_position=2 for 'b'
        assert ", 'NO', 1," in out  # is_partitioning_column NO, clust pos 1
        assert ", 'NO', 2," in out  # clust pos 2 for 'c'
