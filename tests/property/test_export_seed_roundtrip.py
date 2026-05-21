"""Property-based tests for the Phase 10 export → seed round-trip.

Hypothesis generates random rows and schemas; for each example we
populate a fresh persistent catalog, export it, seed it into a second
data_dir, then confirm the table contents and catalog identity match.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import string
from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
from bqemulator.catalog.models import (
    DatasetMeta,
    TableFieldSchema,
    TableMeta,
    TableSchema,
)
from bqemulator.commands.export import run_export
from bqemulator.commands.seed import run_seed
from bqemulator.config import PersistenceMode, Settings
from bqemulator.storage.engine import DuckDBEngine

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path


pytestmark = pytest.mark.property

_NOW = datetime(2026, 5, 14, tzinfo=UTC)


def _safe_id() -> st.SearchStrategy[str]:
    """Ids that pass our SQL identifier whitelist."""
    return st.text(
        alphabet=string.ascii_lowercase + string.digits + "_",
        min_size=1,
        max_size=20,
    ).filter(lambda s: not s[0].isdigit())


@given(
    rows=st.lists(
        st.tuples(st.integers(min_value=-1_000_000, max_value=1_000_000)),
        min_size=0,
        max_size=20,
    ),
)
@settings(max_examples=20, deadline=None)
def test_round_trip_preserves_row_data(
    tmp_path_factory: pytest.TempPathFactory,
    rows: list[tuple[int]],
) -> None:
    """Random row sets must survive export → seed unchanged."""
    src = tmp_path_factory.mktemp("src")
    out = tmp_path_factory.mktemp("out")
    dest = tmp_path_factory.mktemp("dest")
    _populate(src, rows=rows)
    run_export(data_dir=src, output_dir=out)
    summary = run_seed(data_dir=dest, input_dir=out)

    assert summary.rows_loaded == len(rows)

    async def _verify() -> None:
        engine = DuckDBEngine(
            Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=dest),
        )
        await engine.start()
        try:
            got = engine.execute(
                'SELECT id FROM "p__d"."t" ORDER BY id',
            ).fetchall()
            assert sorted(int(r[0]) for r in got) == sorted(int(r[0]) for r in rows)
        finally:
            await engine.stop()

    asyncio.run(_verify())


@given(
    n_routines=st.integers(min_value=0, max_value=5),
)
@settings(max_examples=10, deadline=None)
def test_round_trip_preserves_routine_count(
    tmp_path_factory: pytest.TempPathFactory,
    n_routines: int,
) -> None:
    """Routine count must survive export → seed."""
    src = tmp_path_factory.mktemp("src")
    out = tmp_path_factory.mktemp("out")
    dest = tmp_path_factory.mktemp("dest")
    _populate(src, rows=[], routine_ids=[f"r{i}" for i in range(n_routines)])
    run_export(data_dir=src, output_dir=out)
    summary = run_seed(data_dir=dest, input_dir=out)
    assert summary.routines == n_routines


def _populate(
    data_dir: Path,
    *,
    rows: list[tuple[int]],
    routine_ids: list[str] | None = None,
) -> None:
    """Build a persistent catalog at ``data_dir`` for the property test."""

    async def _impl() -> None:
        engine = DuckDBEngine(
            Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=data_dir),
        )
        await engine.start()
        try:
            catalog = DuckDBCatalogRepository(engine)
            catalog.ensure_ready()
            catalog.create_dataset(
                DatasetMeta(
                    project_id="p",
                    dataset_id="d",
                    creation_time=_NOW,
                    last_modified_time=_NOW,
                    etag="e",
                ),
            )
            engine.execute('CREATE SCHEMA IF NOT EXISTS "p__d"')
            engine.execute('CREATE TABLE "p__d"."t" ("id" BIGINT)')
            if rows:
                # Use parameterized inserts via DuckDB's executemany.
                engine.connection.executemany(
                    'INSERT INTO "p__d"."t" VALUES (?)',
                    [(int(r[0]),) for r in rows],
                )
            catalog.create_table(
                TableMeta(
                    project_id="p",
                    dataset_id="d",
                    table_id="t",
                    schema=TableSchema(  # type: ignore[call-arg]
                        fields=(TableFieldSchema(name="id", type="INT64"),),
                    ),
                    creation_time=_NOW,
                    last_modified_time=_NOW,
                    etag="te",
                ),
            )
            if routine_ids:
                from bqemulator.catalog.models import RoutineMeta

                for rid in routine_ids:
                    catalog.create_routine(
                        RoutineMeta(
                            project_id="p",
                            dataset_id="d",
                            routine_id=rid,
                            routine_type="SCALAR_FUNCTION",
                            definition_body="SELECT 1",
                            creation_time=_NOW,
                            last_modified_time=_NOW,
                            etag="re",
                        ),
                    )
        finally:
            await engine.stop()

    asyncio.run(_impl())
