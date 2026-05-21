"""Unit tests for ``bqemulator.commands.seed``.

Exercises the full export → seed round-trip in process to confirm
catalog metadata and row data survive an export-then-seed cycle.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
from bqemulator.catalog.models import (
    DatasetMeta,
    RoutineMeta,
    TableFieldSchema,
    TableMeta,
    TableSchema,
)
from bqemulator.commands.export import run_export
from bqemulator.commands.seed import SeedSummary, run_seed
from bqemulator.config import PersistenceMode, Settings
from bqemulator.storage.engine import DuckDBEngine

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 13, tzinfo=UTC)


def _seed_source_catalog(data_dir: Path) -> None:
    """Build a persistent catalog with one dataset, one table, one routine."""

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
                    project_id="src",
                    dataset_id="ds",
                    creation_time=_NOW,
                    last_modified_time=_NOW,
                    etag="e1",
                ),
            )
            engine.execute('CREATE SCHEMA IF NOT EXISTS "src__ds"')
            engine.execute(
                'CREATE TABLE "src__ds"."t" ("id" BIGINT, "label" VARCHAR)',
            )
            engine.execute("INSERT INTO \"src__ds\".\"t\" VALUES (1,'a'),(2,'b'),(3,'c')")
            catalog.create_table(
                TableMeta(
                    project_id="src",
                    dataset_id="ds",
                    table_id="t",
                    schema=TableSchema(  # type: ignore[call-arg]
                        fields=(
                            TableFieldSchema(name="id", type="INT64"),
                            TableFieldSchema(name="label", type="STRING"),
                        ),
                    ),
                    creation_time=_NOW,
                    last_modified_time=_NOW,
                    etag="te",
                ),
            )
            catalog.create_routine(
                RoutineMeta(
                    project_id="src",
                    dataset_id="ds",
                    routine_id="echo",
                    routine_type="SCALAR_FUNCTION",
                    definition_body="SELECT x",
                    creation_time=_NOW,
                    last_modified_time=_NOW,
                    etag="re",
                ),
            )
        finally:
            await engine.stop()

    asyncio.run(_impl())


def test_seed_round_trips_dataset_table_routine_and_rows(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "out"
    dest = tmp_path / "dest"
    _seed_source_catalog(src)
    run_export(data_dir=src, output_dir=out)
    summary = run_seed(data_dir=dest, input_dir=out)

    assert summary.datasets == 1
    assert summary.tables == 1
    assert summary.routines == 1
    assert summary.rows_loaded == 3

    # Verify the destination catalog matches.
    async def _verify() -> None:
        engine = DuckDBEngine(
            Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=dest),
        )
        await engine.start()
        try:
            catalog = DuckDBCatalogRepository(engine)
            catalog.ensure_ready()
            assert catalog.get_dataset("src", "ds") is not None
            tbl = catalog.get_table("src", "ds", "t")
            assert tbl is not None
            assert tuple(f.name for f in tbl.schema_.fields) == ("id", "label")
            count = engine.execute('SELECT COUNT(*) FROM "src__ds"."t"').fetchone()
            assert count is not None and int(count[0]) == 3
            rtn = catalog.get_routine("src", "ds", "echo")
            assert rtn is not None
        finally:
            await engine.stop()

    asyncio.run(_verify())


def test_seed_errors_without_manifest(tmp_path: Path) -> None:
    not_an_export = tmp_path / "x"
    not_an_export.mkdir()
    with pytest.raises(FileNotFoundError, match="manifest"):
        run_seed(data_dir=tmp_path / "dest", input_dir=not_an_export)


def test_seed_errors_on_incompatible_manifest_version(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "manifest.json").write_text(
        json.dumps({"manifestVersion": 2}),
    )
    with pytest.raises(ValueError, match="manifest version"):
        run_seed(data_dir=tmp_path / "dest", input_dir=in_dir)


def test_seed_handles_empty_export(tmp_path: Path) -> None:
    """Seed should not fail when the export had no projects/datasets."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "manifest.json").write_text(
        json.dumps({"manifestVersion": 1, "counts": {}}),
    )
    summary = run_seed(data_dir=tmp_path / "dest", input_dir=in_dir)
    assert summary.datasets == 0
    assert summary.tables == 0
    assert summary.routines == 0
    assert summary.rows_loaded == 0


def test_seed_summary_as_dict_uses_camel_case() -> None:
    s = SeedSummary()
    s.datasets = 1
    s.tables = 2
    s.routines = 3
    s.rows_loaded = 4
    assert s.as_dict() == {
        "datasets": 1,
        "tables": 2,
        "routines": 3,
        "rowsLoaded": 4,
    }


def test_seed_is_idempotent_for_catalog_entries(tmp_path: Path) -> None:
    """Re-seeding the same export must not fail (update, not create)."""
    src = tmp_path / "src"
    out = tmp_path / "out"
    dest = tmp_path / "dest"
    _seed_source_catalog(src)
    run_export(data_dir=src, output_dir=out)
    run_seed(data_dir=dest, input_dir=out)
    # Second run must succeed (would raise AlreadyExists if we used create-only).
    summary = run_seed(data_dir=dest, input_dir=out)
    assert summary.datasets == 1
    assert summary.tables == 1
    assert summary.routines == 1
