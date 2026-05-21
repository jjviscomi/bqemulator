"""Unit tests for INFORMATION_SCHEMA.MATERIALIZED_VIEWS rewriting."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import (
    DatasetMeta,
    MaterializedViewMeta,
    TableMeta,
)
from bqemulator.sql.rewriter.information_schema import (
    expand_information_schema,
    expand_information_schema_materialized_views,
)

pytestmark = pytest.mark.unit


def _seed(catalog: MemoryCatalogRepository) -> None:
    now = datetime(2026, 4, 15, tzinfo=UTC)
    catalog.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="ds",
            creation_time=now,
            last_modified_time=now,
            etag="x",
        ),
    )
    catalog.create_table(
        TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="orders",
            creation_time=now,
            last_modified_time=now,
            etag="x",
        ),
    )
    catalog.upsert_materialized_view(
        MaterializedViewMeta(
            project_id="p",
            dataset_id="ds",
            table_id="totals",
            view_query="SELECT SUM(amount) FROM ds.orders",
            base_tables=(("p", "ds", "orders"),),
            last_refresh_time=now,
            is_stale=False,
        ),
    )


def test_expand_dataset_qualified() -> None:
    catalog = MemoryCatalogRepository()
    _seed(catalog)
    sql = "SELECT * FROM ds.INFORMATION_SCHEMA.MATERIALIZED_VIEWS"
    out = expand_information_schema(sql, "p", catalog)
    assert "VALUES" in out
    assert "totals" in out
    assert "INFORMATION_SCHEMA.MATERIALIZED_VIEWS" not in out.upper().replace(" ", "")


def test_expand_project_qualified_returns_specific_dataset_results() -> None:
    catalog = MemoryCatalogRepository()
    _seed(catalog)
    sql = "SELECT * FROM `p`.ds.INFORMATION_SCHEMA.MATERIALIZED_VIEWS"
    out = expand_information_schema(sql, "default", catalog)
    assert "totals" in out


def test_expand_bare_returns_empty() -> None:
    catalog = MemoryCatalogRepository()
    _seed(catalog)
    sql = "SELECT * FROM INFORMATION_SCHEMA.MATERIALIZED_VIEWS"
    out = expand_information_schema(sql, "p", catalog)
    assert "WHERE FALSE" in out


def test_expand_returns_empty_subquery_when_no_mvs_exist() -> None:
    catalog = MemoryCatalogRepository()
    sql = "SELECT * FROM ds.INFORMATION_SCHEMA.MATERIALIZED_VIEWS"
    out = expand_information_schema_materialized_views(sql, "p", catalog)
    assert "WHERE FALSE" in out


def test_expand_handles_stale_flag() -> None:
    catalog = MemoryCatalogRepository()
    _seed(catalog)
    # Mark stale
    mv = catalog.get_materialized_view("p", "ds", "totals")
    assert mv is not None
    catalog.upsert_materialized_view(mv.model_copy(update={"is_stale": True}))

    sql = "SELECT * FROM ds.INFORMATION_SCHEMA.MATERIALIZED_VIEWS"
    out = expand_information_schema_materialized_views(sql, "p", catalog)
    assert "TRUE" in out  # is_stale flag


def test_short_circuits_when_no_information_schema() -> None:
    catalog = MemoryCatalogRepository()
    _seed(catalog)
    sql = "SELECT 1"
    assert expand_information_schema_materialized_views(sql, "p", catalog) == sql
