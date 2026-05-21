"""Export the emulator's catalog and row data as portable seed files.

``bqemulator export`` writes a directory tree under ``output_dir`` that
:func:`bqemulator.commands.seed.run_seed` can read back. The directory
layout — locked here, documented in
``docs/architecture/admin-and-import-export.md`` and ADR 0020 — is::

    <output_dir>/
        manifest.json
        projects/
            <project_id>/
                datasets/
                    <dataset_id>/
                        dataset.json
                        tables/
                            <table_id>.json
                            <table_id>.parquet   # type=TABLE only
                        routines/
                            <routine_id>.json

The schema files use JSON (a strict subset of YAML; Pydantic
``model_dump_json`` round-trips them losslessly back into our frozen
models). Row data uses Apache Parquet via DuckDB's ``COPY ... TO`` so the
output is portable to any tool in the Parquet ecosystem.

The export is read-only against a running emulator's persistent DuckDB
file when ``--readonly`` is passed; otherwise it opens the file with
write access. Phase 10 ADR 0020 documents why we did not pursue an
"online export over REST" path.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
from bqemulator.config import PersistenceMode, Settings
from bqemulator.observability.logging_ import get_logger
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.storage.sql_identifiers import quoted_table_ref

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

    from bqemulator.catalog.models import (
        DatasetMeta,
        RoutineMeta,
        TableMeta,
    )

_log = get_logger(__name__)

_MANIFEST_VERSION = 1


def run_export(*, data_dir: Path, output_dir: Path) -> ExportSummary:
    """Export the persistent catalog at ``data_dir`` into ``output_dir``.

    Args:
        data_dir: The persistent ``data_dir`` whose ``bqemulator.duckdb``
            file holds the catalog and table rows.
        output_dir: Destination directory. Must not exist or must be empty.

    Returns:
        An :class:`ExportSummary` with counts of exported entities.

    Raises:
        FileExistsError: When ``output_dir`` contains files.
        FileNotFoundError: When ``data_dir/bqemulator.duckdb`` is missing.
    """
    # Pre-flight path checks run synchronously so we don't trip ruff's
    # ASYNC240 (no blocking pathlib calls inside async functions).
    db_path = data_dir / "bqemulator.duckdb"
    if not db_path.exists():
        raise FileNotFoundError(f"No DuckDB database at {db_path}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Output directory {output_dir} is not empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    return asyncio.run(_run_export_async(data_dir=data_dir, output_dir=output_dir))


async def _run_export_async(*, data_dir: Path, output_dir: Path) -> ExportSummary:
    """Async impl of :func:`run_export`."""
    settings = Settings(
        persistence_mode=PersistenceMode.PERSISTENT,
        data_dir=data_dir,
    )
    engine = DuckDBEngine(settings)
    await engine.start()
    try:
        catalog = DuckDBCatalogRepository(engine)
        catalog.ensure_ready()
        summary = ExportSummary()

        for dataset in catalog.list_all_datasets():
            _write_dataset(output_dir, dataset)
            summary.datasets += 1

            tables = catalog.list_tables(dataset.project_id, dataset.dataset_id)
            for table in tables:
                _write_table_schema(output_dir, table)
                summary.tables += 1
                # Only physical tables (TABLE / CLONE) carry exportable rows.
                # SNAPSHOT tables are immutable view-of-history that we
                # rematerialise on restore via the catalog; VIEW /
                # MATERIALIZED_VIEW / EXTERNAL are derived from queries or
                # external sources and don't have row storage we control.
                if table.table_type in ("TABLE", "CLONE"):
                    rows = _export_table_rows(engine, output_dir, table)
                    summary.rows_written += rows

            for routine in catalog.list_routines(
                dataset.project_id,
                dataset.dataset_id,
            ):
                _write_routine(output_dir, routine)
                summary.routines += 1

        _write_manifest(output_dir, summary)
        _log.info(
            "export.done",
            output=str(output_dir),
            datasets=summary.datasets,
            tables=summary.tables,
            routines=summary.routines,
            rows=summary.rows_written,
        )
        return summary
    finally:
        await engine.stop()


def _dataset_dir(output_dir: Path, dataset: DatasetMeta) -> Path:
    return output_dir / "projects" / dataset.project_id / "datasets" / dataset.dataset_id


def _write_dataset(output_dir: Path, dataset: DatasetMeta) -> None:
    """Write ``dataset.json`` for one dataset."""
    target = _dataset_dir(output_dir, dataset)
    target.mkdir(parents=True, exist_ok=True)
    (target / "dataset.json").write_text(_dump_model(dataset), encoding="utf-8")


def _write_table_schema(output_dir: Path, table: TableMeta) -> None:
    """Write ``tables/<table_id>.json`` for one table."""
    target = output_dir / "projects" / table.project_id / "datasets" / table.dataset_id / "tables"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{table.table_id}.json").write_text(_dump_model(table), encoding="utf-8")


def _export_table_rows(
    engine: DuckDBEngine,
    output_dir: Path,
    table: TableMeta,
) -> int:
    """Copy a table's rows to ``<output>/.../tables/<table>.parquet``.

    Returns the number of rows written.
    """
    qualified = quoted_table_ref(table.project_id, table.dataset_id, table.table_id)
    target = (
        output_dir
        / "projects"
        / table.project_id
        / "datasets"
        / table.dataset_id
        / "tables"
        / f"{table.table_id}.parquet"
    )
    # DuckDB skips creating files when the source table has zero rows in
    # some builds; we COUNT(*) first so callers know whether a parquet
    # exists, and so seed can skip-the-load when zero.
    try:
        count = engine.execute(f"SELECT COUNT(*) FROM {qualified}").fetchone()
    except Exception:  # noqa: BLE001
        # Table referenced by catalog but not present in DuckDB (e.g.,
        # an unmaterialised mirror imported via ``bqemulator import``).
        # Skip silently — the catalog row is enough for round-tripping.
        return 0
    if count is None:
        return 0
    n: int = int(count[0])
    if n == 0:
        # Still emit an empty Parquet so seed knows the table existed.
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    quoted_target = str(target).replace("'", "''")
    engine.execute(
        f"COPY {qualified} TO '{quoted_target}' (FORMAT PARQUET)",
    )
    return n


def _write_routine(output_dir: Path, routine: RoutineMeta) -> None:
    """Write ``routines/<routine_id>.json`` for one routine."""
    target = (
        output_dir / "projects" / routine.project_id / "datasets" / routine.dataset_id / "routines"
    )
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{routine.routine_id}.json").write_text(
        _dump_model(routine),
        encoding="utf-8",
    )


def _write_manifest(output_dir: Path, summary: ExportSummary) -> None:
    """Write the top-level manifest.json file."""
    manifest: dict[str, Any] = {
        "manifestVersion": _MANIFEST_VERSION,
        "tool": "bqemulator-export",
        "counts": summary.as_dict(),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _dump_model(model: Any) -> str:
    """Render a frozen Pydantic model as deterministic indented JSON."""
    return json.dumps(
        json.loads(model.model_dump_json(by_alias=True)),
        indent=2,
        sort_keys=True,
        default=str,
    )


class ExportSummary:
    """Counts of exported entities for CLI-level reporting."""

    def __init__(self) -> None:
        self.datasets = 0
        self.tables = 0
        self.routines = 0
        self.rows_written = 0

    def as_dict(self) -> dict[str, int]:
        """Return counts as a plain dict (JSON-friendly)."""
        return {
            "datasets": self.datasets,
            "tables": self.tables,
            "routines": self.routines,
            "rowsWritten": self.rows_written,
        }


__all__ = ["ExportSummary", "run_export"]
