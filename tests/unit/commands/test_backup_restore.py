"""Unit tests for ``bqemulator.commands.backup`` and
``bqemulator.commands.restore`` — exercise the full DuckDB EXPORT/IMPORT
DATABASE round-trip.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
from bqemulator.catalog.models import (
    DatasetMeta,
    TableFieldSchema,
    TableMeta,
    TableSchema,
)
from bqemulator.commands.backup import run_backup
from bqemulator.commands.restore import run_restore
from bqemulator.config import PersistenceMode, Settings
from bqemulator.storage.engine import DuckDBEngine

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 13, tzinfo=UTC)


def _populate(data_dir: Path) -> None:
    """Build a small persistent catalog with rows for round-tripping."""

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
            engine.execute(
                'CREATE TABLE "p__d"."t" ("k" BIGINT, "v" VARCHAR)',
            )
            engine.execute("INSERT INTO \"p__d\".\"t\" VALUES (1,'one'),(2,'two')")
            catalog.create_table(
                TableMeta(
                    project_id="p",
                    dataset_id="d",
                    table_id="t",
                    schema=TableSchema(  # type: ignore[call-arg]
                        fields=(
                            TableFieldSchema(name="k", type="INT64"),
                            TableFieldSchema(name="v", type="STRING"),
                        ),
                    ),
                    creation_time=_NOW,
                    last_modified_time=_NOW,
                    etag="te",
                ),
            )
        finally:
            await engine.stop()

    asyncio.run(_impl())


def test_backup_restore_round_trips_catalog_and_rows(tmp_path: Path) -> None:
    src = tmp_path / "src"
    backup = tmp_path / "backup"
    dest = tmp_path / "dest"
    _populate(src)

    run_backup(data_dir=src, output_dir=backup)
    # DuckDB writes a schema.sql at the top — the restore command uses
    # that as the canonical "is this a backup" marker.
    assert (backup / "schema.sql").exists()

    run_restore(data_dir=dest, input_dir=backup)

    async def _verify() -> None:
        engine = DuckDBEngine(
            Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=dest),
        )
        await engine.start()
        try:
            catalog = DuckDBCatalogRepository(engine)
            catalog.ensure_ready()
            assert catalog.get_dataset("p", "d") is not None
            assert catalog.get_table("p", "d", "t") is not None
            rows = engine.execute('SELECT * FROM "p__d"."t" ORDER BY k').fetchall()
            assert rows == [(1, "one"), (2, "two")]
        finally:
            await engine.stop()

    asyncio.run(_verify())


def test_backup_errors_when_data_dir_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_backup(
            data_dir=tmp_path / "missing",
            output_dir=tmp_path / "out",
        )


def test_backup_errors_when_output_is_nonempty(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "out"
    _populate(src)
    out.mkdir()
    (out / "existing.txt").write_text("x")
    with pytest.raises(FileExistsError):
        run_backup(data_dir=src, output_dir=out)


def test_restore_errors_on_non_backup_directory(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus"
    bogus.mkdir()
    (bogus / "manifest.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="backup directory"):
        run_restore(data_dir=tmp_path / "dest", input_dir=bogus)


def test_restore_refuses_overwrite_without_force(tmp_path: Path) -> None:
    src = tmp_path / "src"
    backup = tmp_path / "backup"
    dest = tmp_path / "dest"
    _populate(src)
    run_backup(data_dir=src, output_dir=backup)
    run_restore(data_dir=dest, input_dir=backup)
    # The DuckDB file now exists at dest/bqemulator.duckdb. A second
    # restore without --force should refuse.
    with pytest.raises(FileExistsError):
        run_restore(data_dir=dest, input_dir=backup)


def test_restore_with_force_overwrites(tmp_path: Path) -> None:
    src = tmp_path / "src"
    backup = tmp_path / "backup"
    dest = tmp_path / "dest"
    _populate(src)
    run_backup(data_dir=src, output_dir=backup)
    run_restore(data_dir=dest, input_dir=backup)
    # Touch a side-car WAL to confirm restore --force cleans it up.
    wal = (dest / "bqemulator.duckdb").with_suffix(".duckdb.wal")
    wal.write_bytes(b"x")
    run_restore(data_dir=dest, input_dir=backup, force=True)
    assert not wal.exists()
