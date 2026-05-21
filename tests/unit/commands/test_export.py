"""Unit tests for ``bqemulator.commands.export``.

These exercise the full export pipeline against a real (ephemeral)
persistent DuckDB file so the COPY-to-Parquet path is hit, not mocked.
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
from bqemulator.commands.export import (
    ExportSummary,
    _dump_model,
    _export_table_rows,
    run_export,
)
from bqemulator.config import PersistenceMode, Settings
from bqemulator.storage.engine import DuckDBEngine

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 13, tzinfo=UTC)


def _populate_catalog(data_dir: Path, *, with_rows: bool = True) -> None:
    """Seed a fresh persistent catalog at ``data_dir`` with two datasets."""

    async def _impl() -> None:
        engine = DuckDBEngine(
            Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=data_dir),
        )
        await engine.start()
        try:
            catalog = DuckDBCatalogRepository(engine)
            catalog.ensure_ready()
            ds = DatasetMeta(
                project_id="proj-a",
                dataset_id="ds1",
                location="US",
                creation_time=_NOW,
                last_modified_time=_NOW,
                etag="e1",
            )
            catalog.create_dataset(ds)
            engine.execute('CREATE SCHEMA IF NOT EXISTS "proj-a__ds1"')
            engine.execute('CREATE TABLE "proj-a__ds1"."t1" ("id" BIGINT, "name" VARCHAR)')
            if with_rows:
                engine.execute(
                    "INSERT INTO \"proj-a__ds1\".\"t1\" VALUES (1, 'a'), (2, 'b')",
                )
            tbl = TableMeta(
                project_id="proj-a",
                dataset_id="ds1",
                table_id="t1",
                schema=TableSchema(  # type: ignore[call-arg]
                    fields=(
                        TableFieldSchema(name="id", type="INT64"),
                        TableFieldSchema(name="name", type="STRING"),
                    ),
                ),
                creation_time=_NOW,
                last_modified_time=_NOW,
                etag="t1e",
            )
            catalog.create_table(tbl)
            # Empty table to cover the zero-rows branch.
            engine.execute(
                'CREATE TABLE "proj-a__ds1"."empty_tbl" ("k" BIGINT)',
            )
            catalog.create_table(
                TableMeta(
                    project_id="proj-a",
                    dataset_id="ds1",
                    table_id="empty_tbl",
                    schema=TableSchema(  # type: ignore[call-arg]
                        fields=(TableFieldSchema(name="k", type="INT64"),),
                    ),
                    creation_time=_NOW,
                    last_modified_time=_NOW,
                    etag="empty",
                ),
            )
            # A view (non-physical) to cover the skip-data branch.
            catalog.create_table(
                TableMeta(
                    project_id="proj-a",
                    dataset_id="ds1",
                    table_id="v1",
                    table_type="VIEW",
                    view_query="SELECT 1",
                    creation_time=_NOW,
                    last_modified_time=_NOW,
                    etag="v1e",
                ),
            )
            catalog.create_routine(
                RoutineMeta(
                    project_id="proj-a",
                    dataset_id="ds1",
                    routine_id="add",
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


def test_export_writes_manifest_and_dataset_files(tmp_path: Path) -> None:
    _populate_catalog(tmp_path / "data")
    out = tmp_path / "out"

    summary = run_export(data_dir=tmp_path / "data", output_dir=out)

    assert summary.datasets == 1
    assert summary.tables == 3  # t1, empty_tbl, v1
    assert summary.routines == 1
    assert summary.rows_written == 2
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["manifestVersion"] == 1
    assert manifest["counts"]["datasets"] == 1
    ds_file = out / "projects" / "proj-a" / "datasets" / "ds1" / "dataset.json"
    assert ds_file.exists()
    table_file = out / "projects" / "proj-a" / "datasets" / "ds1" / "tables" / "t1.json"
    assert table_file.exists()
    parquet = out / "projects" / "proj-a" / "datasets" / "ds1" / "tables" / "t1.parquet"
    assert parquet.exists() and parquet.stat().st_size > 0
    empty_parquet = (
        out / "projects" / "proj-a" / "datasets" / "ds1" / "tables" / "empty_tbl.parquet"
    )
    assert empty_parquet.exists() and empty_parquet.stat().st_size == 0
    # Views must NOT have a parquet file written.
    view_parquet = out / "projects" / "proj-a" / "datasets" / "ds1" / "tables" / "v1.parquet"
    assert not view_parquet.exists()
    routine = out / "projects" / "proj-a" / "datasets" / "ds1" / "routines" / "add.json"
    assert routine.exists()


def test_export_refuses_nonempty_output(tmp_path: Path) -> None:
    _populate_catalog(tmp_path / "data", with_rows=False)
    out = tmp_path / "out"
    out.mkdir()
    (out / "existing.txt").write_text("x")
    with pytest.raises(FileExistsError):
        run_export(data_dir=tmp_path / "data", output_dir=out)


def test_export_errors_when_database_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_export(data_dir=tmp_path / "missing", output_dir=tmp_path / "out")


def test_export_skips_table_missing_in_duckdb(tmp_path: Path) -> None:
    """Catalog has a table but DuckDB doesn't (e.g. imported via 'import')."""
    data_dir = tmp_path / "data"
    _populate_catalog(data_dir, with_rows=False)

    async def _add_orphan() -> None:
        engine = DuckDBEngine(
            Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=data_dir),
        )
        await engine.start()
        try:
            catalog = DuckDBCatalogRepository(engine)
            catalog.ensure_ready()
            catalog.create_table(
                TableMeta(
                    project_id="proj-a",
                    dataset_id="ds1",
                    table_id="orphan",
                    creation_time=_NOW,
                    last_modified_time=_NOW,
                    etag="orph",
                ),
            )
        finally:
            await engine.stop()

    asyncio.run(_add_orphan())

    out = tmp_path / "out"
    summary = run_export(data_dir=data_dir, output_dir=out)
    assert summary.tables == 4  # t1, empty_tbl, v1, orphan
    # No parquet should exist for the orphan
    orphan_pq = out / "projects" / "proj-a" / "datasets" / "ds1" / "tables" / "orphan.parquet"
    assert not orphan_pq.exists()


def test_export_summary_as_dict_has_camel_case_keys() -> None:
    s = ExportSummary()
    s.datasets = 1
    s.tables = 2
    s.routines = 3
    s.rows_written = 4
    assert s.as_dict() == {
        "datasets": 1,
        "tables": 2,
        "routines": 3,
        "rowsWritten": 4,
    }


def test_dump_model_is_deterministic() -> None:
    """JSON dump must be sorted-keys + indented for stable diffs."""
    ds = DatasetMeta(
        project_id="p",
        dataset_id="d",
        creation_time=_NOW,
        last_modified_time=_NOW,
        etag="e",
    )
    first = _dump_model(ds)
    second = _dump_model(ds)
    assert first == second
    # Keys should be sorted: 'creation_time' before 'project_id' is the
    # natural sort order with sort_keys=True.
    assert first.index('"creation_time"') < first.index('"project_id"')


def test_export_table_rows_returns_zero_for_missing_table(tmp_path: Path) -> None:
    """Direct unit on the helper — used by export when DuckDB lacks the row table."""

    async def _impl() -> int:
        engine = DuckDBEngine(
            Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=tmp_path),
        )
        await engine.start()
        try:
            ghost = TableMeta(
                project_id="px",
                dataset_id="dx",
                table_id="ghost",
                creation_time=_NOW,
                last_modified_time=_NOW,
                etag="g",
            )
            return _export_table_rows(engine, tmp_path / "out", ghost)
        finally:
            await engine.stop()

    assert asyncio.run(_impl()) == 0
