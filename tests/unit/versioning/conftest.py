"""Shared fixtures for the Phase 7 versioning unit tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, TableMeta
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.events import EventBus
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.storage.sql_identifiers import quoted_schema, quoted_table_ref
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager


@pytest_asyncio.fixture
async def full_ctx(
    ephemeral_settings: Settings,
    frozen_clock: FrozenClock,
) -> AsyncIterator[AppContext]:
    """An :class:`AppContext` wired with every Phase 7 dependency."""
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    catalog = MemoryCatalogRepository()
    events = EventBus()
    udf_registry = UDFRegistry(ephemeral_settings)
    snapshots = SnapshotManager(
        engine=engine,
        catalog=catalog,
        clock=frozen_clock,
        events=events,
        retention_days=ephemeral_settings.time_travel_retention_days,
    )
    row_access = RowAccessPolicyManager(catalog=catalog, clock=frozen_clock)
    ctx = AppContext(
        settings=ephemeral_settings,
        clock=frozen_clock,
        engine=engine,
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=events,
        udf_registry=udf_registry,
        snapshots=snapshots,
        row_access=row_access,
    )
    try:
        yield ctx
    finally:
        await engine.stop()


@pytest.fixture
def make_dataset(full_ctx: AppContext, frozen_clock: FrozenClock):
    """Factory: create a dataset entry in the catalog AND its DuckDB schema."""

    def _create(project: str = "p", ds: str = "ds") -> None:
        now = frozen_clock.now()
        full_ctx.catalog.create_dataset(
            DatasetMeta(
                project_id=project,
                dataset_id=ds,
                creation_time=now,
                last_modified_time=now,
                etag=generate_etag(project, ds, str(now)),
            ),
        )
        full_ctx.engine.execute(
            f"CREATE SCHEMA IF NOT EXISTS {quoted_schema(project, ds)}",
        )

    return _create


@pytest.fixture
def make_table(full_ctx: AppContext, frozen_clock: FrozenClock):
    """Factory: create a TableMeta + physical DuckDB table.

    Returns the new ``TableMeta``.
    """

    def _create(
        project: str = "p",
        ds: str = "ds",
        table: str = "t",
        *,
        schema_sql: str = "id INT64, name VARCHAR",
        rows: list[tuple] | None = None,
    ) -> TableMeta:
        now = frozen_clock.now()
        meta = TableMeta(
            project_id=project,
            dataset_id=ds,
            table_id=table,
            table_type="TABLE",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag(project, ds, table, str(now)),
        )
        full_ctx.catalog.create_table(meta)
        full_ctx.engine.execute(
            f"CREATE SCHEMA IF NOT EXISTS {quoted_schema(project, ds)}",
        )
        full_ctx.engine.execute(
            f"CREATE TABLE {quoted_table_ref(project, ds, table)} ({schema_sql})",
        )
        if rows:
            placeholders = ",".join(["(" + ",".join(["?"] * len(rows[0])) + ")"] * len(rows))
            params: list[object] = []
            for row in rows:
                params.extend(row)
            full_ctx.engine.execute(
                f"INSERT INTO {quoted_table_ref(project, ds, table)} VALUES {placeholders}",
                params,
            )
            updated = full_ctx.catalog.get_table(project, ds, table)
            assert updated is not None
            full_ctx.catalog.update_table(updated.model_copy(update={"num_rows": len(rows)}))
        return meta

    return _create


__all__ = ["full_ctx", "make_dataset", "make_table"]
