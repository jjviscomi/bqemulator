"""Load an export directory back into a local emulator catalog.

``bqemulator seed`` is the inverse of
:mod:`bqemulator.commands.export`. It reads the directory layout
written by :func:`bqemulator.commands.export.run_export`, recreates each
dataset / table / routine in the local persistent catalog at
``data_dir``, and bulk-loads each table's rows from its Parquet file via
DuckDB's ``COPY FROM '<file>' (FORMAT PARQUET)``.

Seed is idempotent in the catalog (existing entities are replaced) but
NOT in row data (rows are appended into newly-created or pre-existing
tables). Use a fresh ``data_dir`` if you need a clean reload.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
from bqemulator.catalog.models import DatasetMeta, RoutineMeta, TableMeta
from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.errors import DomainError
from bqemulator.observability.logging_ import get_logger
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.storage.sql_identifiers import quoted_schema, quoted_table_ref
from bqemulator.storage.type_map import bq_schema_to_duckdb_columns

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

_log = get_logger(__name__)


def run_seed(*, data_dir: Path, input_dir: Path) -> SeedSummary:
    """Load ``input_dir`` (an export) into the local catalog at ``data_dir``.

    Args:
        data_dir: Persistent ``data_dir`` for the local catalog. Created
            if absent.
        input_dir: Directory produced by an earlier ``bqemulator export``.

    Returns:
        Counts of seeded entities.

    Raises:
        FileNotFoundError: When ``input_dir/manifest.json`` is missing.
        ValueError: When the manifest version is incompatible.
    """
    return asyncio.run(
        _run_seed_async(data_dir=data_dir, input_dir=input_dir),
    )


async def _run_seed_async(*, data_dir: Path, input_dir: Path) -> SeedSummary:
    """Async implementation of :func:`run_seed`."""
    manifest_path = input_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Not an export directory (no manifest.json): {input_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_version = manifest.get("manifestVersion")
    if manifest_version != 1:
        raise ValueError(f"Unsupported export manifest version: {manifest_version}")

    settings = Settings(
        persistence_mode=PersistenceMode.PERSISTENT,
        data_dir=data_dir,
    )
    engine = DuckDBEngine(settings)
    await engine.start()
    try:
        catalog = DuckDBCatalogRepository(engine)
        catalog.ensure_ready()
        summary = SeedSummary()

        projects_dir = input_dir / "projects"
        if not projects_dir.is_dir():
            return summary

        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            datasets_dir = project_dir / "datasets"
            if not datasets_dir.is_dir():
                continue
            for dataset_dir in sorted(datasets_dir.iterdir()):
                if not dataset_dir.is_dir():
                    continue
                _seed_dataset(engine, catalog, dataset_dir, summary)

        _log.info(
            "seed.done",
            input=str(input_dir),
            datasets=summary.datasets,
            tables=summary.tables,
            routines=summary.routines,
            rows=summary.rows_loaded,
        )
        return summary
    finally:
        await engine.stop()


def _seed_dataset(
    engine: DuckDBEngine,
    catalog: DuckDBCatalogRepository,
    dataset_dir: Path,
    summary: SeedSummary,
) -> None:
    """Seed a single dataset (schema + tables + routines)."""
    ds_path = dataset_dir / "dataset.json"
    if not ds_path.exists():
        return
    ds_meta = DatasetMeta.model_validate_json(ds_path.read_text(encoding="utf-8"))
    schema = quoted_schema(ds_meta.project_id, ds_meta.dataset_id)
    engine.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    if catalog.get_dataset(ds_meta.project_id, ds_meta.dataset_id) is None:
        catalog.create_dataset(ds_meta)
    else:
        catalog.update_dataset(ds_meta)
    summary.datasets += 1

    tables_dir = dataset_dir / "tables"
    if tables_dir.is_dir():
        for tbl_file in sorted(tables_dir.glob("*.json")):
            rows = _seed_table(engine, catalog, tbl_file)
            summary.tables += 1
            summary.rows_loaded += rows

    routines_dir = dataset_dir / "routines"
    if routines_dir.is_dir():
        for rtn_file in sorted(routines_dir.glob("*.json")):
            _seed_routine(catalog, rtn_file)
            summary.routines += 1


def _seed_table(
    engine: DuckDBEngine,
    catalog: DuckDBCatalogRepository,
    tbl_file: Path,
) -> int:
    """Recreate a single table (CREATE TABLE + COPY FROM <parquet>)."""
    table = TableMeta.model_validate_json(tbl_file.read_text(encoding="utf-8"))
    target_ref = quoted_table_ref(table.project_id, table.dataset_id, table.table_id)

    if table.table_type in ("TABLE", "CLONE"):
        # Create the underlying DuckDB table. Mirror routes/tables.py so
        # the seed produces the same physical shape a REST POST would.
        if table.schema_.fields:
            fields_raw = [
                json.loads(f.model_dump_json(by_alias=True)) for f in table.schema_.fields
            ]
            duckdb_cols = bq_schema_to_duckdb_columns(fields_raw)
            col_defs = ", ".join(f'"{name}" {dtype}' for name, dtype in duckdb_cols)
            engine.execute(f"CREATE TABLE IF NOT EXISTS {target_ref} ({col_defs})")
        else:
            engine.execute(f"CREATE TABLE IF NOT EXISTS {target_ref} (__placeholder INTEGER)")

    if catalog.get_table(table.project_id, table.dataset_id, table.table_id) is None:
        catalog.create_table(table)
    else:
        catalog.update_table(table)

    rows_loaded = 0
    parquet = tbl_file.with_suffix(".parquet")
    if parquet.exists() and parquet.stat().st_size > 0 and table.table_type in ("TABLE", "CLONE"):
        quoted_source = str(parquet).replace("'", "''")
        engine.execute(
            f"COPY {target_ref} FROM '{quoted_source}' (FORMAT PARQUET)",
        )
        count = engine.execute(f"SELECT COUNT(*) FROM {target_ref}").fetchone()
        if count is not None:
            rows_loaded = int(count[0])
    return rows_loaded


def _seed_routine(catalog: DuckDBCatalogRepository, rtn_file: Path) -> None:
    """Recreate a single routine (catalog only — registration happens at start)."""
    routine = RoutineMeta.model_validate_json(rtn_file.read_text(encoding="utf-8"))
    try:
        catalog.create_routine(routine)
    except DomainError:
        catalog.update_routine(routine)


class SeedSummary:
    """Counts of seeded entities for CLI-level reporting."""

    def __init__(self) -> None:
        self.datasets = 0
        self.tables = 0
        self.routines = 0
        self.rows_loaded = 0

    def as_dict(self) -> dict[str, Any]:
        """Return counts as a plain dict (JSON-friendly)."""
        return {
            "datasets": self.datasets,
            "tables": self.tables,
            "routines": self.routines,
            "rowsLoaded": self.rows_loaded,
        }


__all__ = ["SeedSummary", "run_seed"]
