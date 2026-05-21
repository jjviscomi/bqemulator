"""Unit tests for :mod:`bqemulator.versioning.materialized_views`."""

from __future__ import annotations

import pytest

from bqemulator.api.dependencies import AppContext
from bqemulator.domain.errors import (
    AlreadyExistsError,
    InvalidQueryError,
    NotFoundError,
)
from bqemulator.domain.events import TableDataChanged
from bqemulator.storage.sql_identifiers import quoted_table_ref
from bqemulator.versioning.materialized_views import (
    MaterializedViewManager,
    clear_subscriptions_for_context,
    extract_base_tables,
    hydrate_subscriptions,
)

pytestmark = pytest.mark.unit


def test_extract_base_tables_handles_join() -> None:
    sql = """SELECT o.id, c.name
    FROM ds.orders AS o
    JOIN ds.customers AS c ON c.id = o.customer_id"""
    result = extract_base_tables(sql, project_id="proj")
    # ordering preserves walk order; deduped
    assert ("proj", "ds", "orders") in result
    assert ("proj", "ds", "customers") in result
    assert len(result) == 2


def test_extract_base_tables_uses_project_default_when_unqualified() -> None:
    sql = "SELECT * FROM ds.orders"
    result = extract_base_tables(sql, project_id="default-proj")
    assert result == [("default-proj", "ds", "orders")]


def test_extract_base_tables_uses_explicit_project_qualifier() -> None:
    sql = "SELECT * FROM `other-proj`.ds.orders"
    result = extract_base_tables(sql, project_id="default-proj")
    assert result == [("other-proj", "ds", "orders")]


def test_extract_base_tables_skips_tvf_calls() -> None:
    sql = "SELECT * FROM ds.my_tvf(7)"
    result = extract_base_tables(sql, project_id="p")
    assert result == []


def test_extract_base_tables_raises_on_unparseable_sql() -> None:
    with pytest.raises(InvalidQueryError):
        extract_base_tables("SELECT *** FROM (((", project_id="p")


async def test_create_mv_materialises_and_records_metadata(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table(
        "p",
        "ds",
        "orders",
        schema_sql="amount INT64, country VARCHAR",
        rows=[(10, "US"), (5, "US"), (20, "CA")],
    )
    manager = MaterializedViewManager(full_ctx)
    mv = await manager.create(
        "p",
        "ds",
        "country_totals",
        "SELECT country, SUM(amount) AS total FROM ds.orders GROUP BY country",
    )
    assert mv.is_stale is False
    assert mv.base_tables == (("p", "ds", "orders"),)

    rows = full_ctx.engine.execute(
        f"SELECT country, total FROM "
        f"{quoted_table_ref('p', 'ds', 'country_totals')} ORDER BY country",
    ).fetchall()
    assert rows == [("CA", 20), ("US", 15)]

    table_meta = full_ctx.catalog.get_table("p", "ds", "country_totals")
    assert table_meta is not None
    assert table_meta.table_type == "MATERIALIZED_VIEW"
    assert table_meta.view_query is not None


async def test_create_mv_rejects_when_destination_table_exists(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "orders")
    make_table("p", "ds", "country_totals")
    manager = MaterializedViewManager(full_ctx)
    with pytest.raises(AlreadyExistsError):
        await manager.create(
            "p",
            "ds",
            "country_totals",
            "SELECT * FROM ds.orders",
        )


async def test_create_mv_requires_base_tables(
    full_ctx: AppContext,
    make_dataset,
) -> None:
    make_dataset("p", "ds")
    manager = MaterializedViewManager(full_ctx)
    # SELECT 1 has no base tables.
    with pytest.raises(InvalidQueryError):
        await manager.create("p", "ds", "v", "SELECT 1 AS x")


async def test_create_mv_requires_base_table_to_exist(
    full_ctx: AppContext,
    make_dataset,
) -> None:
    make_dataset("p", "ds")
    manager = MaterializedViewManager(full_ctx)
    with pytest.raises(NotFoundError):
        await manager.create("p", "ds", "v", "SELECT * FROM ds.missing")


async def test_event_marks_mv_stale_and_refresh_recomputes(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table(
        "p",
        "ds",
        "orders",
        schema_sql="amount INT64",
        rows=[(10,), (5,)],
    )
    manager = MaterializedViewManager(full_ctx)
    await manager.create(
        "p",
        "ds",
        "totals",
        "SELECT SUM(amount) AS total FROM ds.orders",
    )

    # Insert new row + emit event the way real DML does.
    full_ctx.engine.execute(
        f"INSERT INTO {quoted_table_ref('p', 'ds', 'orders')} VALUES (100)",
    )
    full_ctx.events.publish(TableDataChanged("p", "ds", "orders"))

    # Without refresh, the MV still has the stale row count.
    stale = full_ctx.engine.execute(
        f"SELECT total FROM {quoted_table_ref('p', 'ds', 'totals')}",
    ).fetchone()
    assert stale is not None and stale[0] == 15

    mv = full_ctx.catalog.get_materialized_view("p", "ds", "totals")
    assert mv is not None and mv.is_stale is True

    refreshed = await manager.refresh_if_stale("p", "ds", "totals")
    assert refreshed is not None and refreshed.is_stale is False
    fresh = full_ctx.engine.execute(
        f"SELECT total FROM {quoted_table_ref('p', 'ds', 'totals')}",
    ).fetchone()
    assert fresh is not None and fresh[0] == 115


async def test_refresh_force_runs_when_not_stale(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table(
        "p",
        "ds",
        "orders",
        schema_sql="amount INT64",
        rows=[(10,)],
    )
    manager = MaterializedViewManager(full_ctx)
    await manager.create(
        "p",
        "ds",
        "totals",
        "SELECT SUM(amount) AS total FROM ds.orders",
    )

    # No invalidation yet — refresh anyway.
    full_ctx.engine.execute(
        f"INSERT INTO {quoted_table_ref('p', 'ds', 'orders')} VALUES (90)",
    )
    refreshed = await manager.refresh("p", "ds", "totals")
    assert refreshed.is_stale is False
    fresh = full_ctx.engine.execute(
        f"SELECT total FROM {quoted_table_ref('p', 'ds', 'totals')}",
    ).fetchone()
    assert fresh is not None and fresh[0] == 100


async def test_refresh_unknown_mv_raises(full_ctx: AppContext, make_dataset) -> None:
    make_dataset("p", "ds")
    manager = MaterializedViewManager(full_ctx)
    with pytest.raises(NotFoundError):
        await manager.refresh("p", "ds", "nope")


async def test_refresh_if_stale_returns_none_for_non_mv(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t")
    manager = MaterializedViewManager(full_ctx)
    assert await manager.refresh_if_stale("p", "ds", "t") is None


async def test_drop_removes_table_and_mv_meta(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "orders", schema_sql="amount INT64", rows=[(1,)])
    manager = MaterializedViewManager(full_ctx)
    await manager.create("p", "ds", "v", "SELECT SUM(amount) AS s FROM ds.orders")

    await manager.drop("p", "ds", "v")
    assert full_ctx.catalog.get_table("p", "ds", "v") is None
    assert full_ctx.catalog.get_materialized_view("p", "ds", "v") is None


async def test_drop_rejects_non_materialized_view(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t")
    manager = MaterializedViewManager(full_ctx)
    with pytest.raises(NotFoundError):
        await manager.drop("p", "ds", "t")


async def test_hydrate_subscriptions_replays_catalog_state(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table(
        "p",
        "ds",
        "orders",
        schema_sql="amount INT64",
        rows=[(10,)],
    )
    manager = MaterializedViewManager(full_ctx)
    await manager.create(
        "p",
        "ds",
        "totals",
        "SELECT SUM(amount) AS total FROM ds.orders",
    )

    # Simulate restart: clear all subscriptions and rebuild them from the catalog.
    clear_subscriptions_for_context(full_ctx)
    hydrate_subscriptions(full_ctx)

    full_ctx.events.publish(TableDataChanged("p", "ds", "orders"))
    mv = full_ctx.catalog.get_materialized_view("p", "ds", "totals")
    assert mv is not None and mv.is_stale is True


async def test_unrelated_event_does_not_mark_stale(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table(
        "p",
        "ds",
        "orders",
        schema_sql="amount INT64",
        rows=[(1,)],
    )
    manager = MaterializedViewManager(full_ctx)
    await manager.create(
        "p",
        "ds",
        "v",
        "SELECT SUM(amount) AS s FROM ds.orders",
    )

    full_ctx.events.publish(TableDataChanged("p", "ds", "other"))
    mv = full_ctx.catalog.get_materialized_view("p", "ds", "v")
    assert mv is not None and mv.is_stale is False


async def test_create_mv_requires_destination_dataset_to_exist(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    """The MV's parent dataset must exist before creation."""
    make_dataset("p", "src")
    make_table("p", "src", "orders", schema_sql="amount INT64", rows=[(1,)])
    # Don't create the destination dataset.
    manager = MaterializedViewManager(full_ctx)
    with pytest.raises(NotFoundError):
        await manager.create(
            "p",
            "missing_ds",
            "v",
            "SELECT SUM(amount) AS s FROM src.orders",
        )


def test_arrow_to_bq_type_mapping_covers_every_branch() -> None:
    """Direct coverage for the shared Arrow → BigQuery type mapping."""
    import pyarrow as pa

    from bqemulator.storage.arrow_bridge import arrow_type_to_bq_type_name

    # Each branch — an Arrow type per BQ type.
    assert arrow_type_to_bq_type_name(pa.int64()) == "INTEGER"
    assert arrow_type_to_bq_type_name(pa.int32()) == "INTEGER"
    assert arrow_type_to_bq_type_name(pa.float64()) == "FLOAT"
    assert arrow_type_to_bq_type_name(pa.float32()) == "FLOAT"
    assert arrow_type_to_bq_type_name(pa.bool_()) == "BOOLEAN"
    assert arrow_type_to_bq_type_name(pa.string()) == "STRING"
    assert arrow_type_to_bq_type_name(pa.large_string()) == "STRING"
    assert arrow_type_to_bq_type_name(pa.timestamp("us")) == "DATETIME"
    assert arrow_type_to_bq_type_name(pa.timestamp("us", tz="UTC")) == "TIMESTAMP"
    assert arrow_type_to_bq_type_name(pa.date32()) == "DATE"
    assert arrow_type_to_bq_type_name(pa.time64("us")) == "TIME"
    assert arrow_type_to_bq_type_name(pa.decimal128(38, 9)) == "NUMERIC"
    assert arrow_type_to_bq_type_name(pa.binary()) == "BYTES"
    # Unknown type defaults to STRING.
    assert arrow_type_to_bq_type_name(pa.list_(pa.int64())) == "STRING"
