"""Capture the persistent DuckDB database to a portable directory.

``bqemulator backup`` opens the persistent DuckDB file under
``data_dir`` and issues DuckDB's ``EXPORT DATABASE TO '<dir>'`` to write
a portable archive (schema SQL + per-table Parquet) into the target
directory.

The backup is intended to run **offline** (no other process holding the
DuckDB file lock). Real BigQuery's online backup is implicit and slot-
free; the emulator's offline scope keeps the implementation a few dozen
lines and avoids divergence on lock semantics. The companion
``bqemulator restore`` reads the same directory back via DuckDB's
``IMPORT DATABASE FROM '<dir>'``.

This module talks to DuckDB directly via :func:`duckdb.connect` rather
than going through :class:`~bqemulator.storage.engine.DuckDBEngine`,
because the engine's startup creates the catalog schemas — which
collides with the ``CREATE SCHEMA`` statements that ``IMPORT DATABASE``
later replays on restore. Backup only needs the bare DuckDB
connection (plus the spatial extension so any GEOGRAPHY columns can be
serialised), not the engine's full readiness hooks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.observability.logging_ import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

_log = get_logger(__name__)


def run_backup(*, data_dir: Path, output_dir: Path) -> BackupSummary:
    """Export the persistent DuckDB database to ``output_dir``.

    Args:
        data_dir: The ``data_dir`` whose ``bqemulator.duckdb`` file
            will be exported.
        output_dir: Target directory for the export. Must not exist or
            must be empty.

    Returns:
        A :class:`BackupSummary` carrying the resolved output path.

    Raises:
        FileNotFoundError: When the source DuckDB file is missing.
        FileExistsError: When ``output_dir`` is a non-empty directory.
    """
    db_path = data_dir / "bqemulator.duckdb"
    if not db_path.exists():
        raise FileNotFoundError(f"No DuckDB database at {db_path}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Backup directory {output_dir} is not empty")
    output_dir.mkdir(parents=True, exist_ok=True)

    import duckdb

    conn = duckdb.connect(str(db_path), read_only=False)
    try:
        # Load the spatial extension so any GEOGRAPHY columns serialize
        # correctly; ignore failures because plenty of databases will not
        # use GEOGRAPHY and offline environments may lack the extension.
        try:
            conn.execute("INSTALL spatial")
            conn.execute("LOAD spatial")
        except Exception as exc:  # noqa: BLE001
            _log.warning("backup.spatial_skipped", error=str(exc))

        # SQL-injection defense: the target path is a CLI argument; we
        # double single-quotes so the literal stays inside the SQL string
        # boundary. DuckDB's EXPORT DATABASE accepts string literals
        # only — there is no parameter binding for the path.
        quoted_target = str(output_dir).replace("'", "''")
        conn.execute(f"EXPORT DATABASE '{quoted_target}' (FORMAT PARQUET)")
        _log.info("backup.done", data_dir=str(data_dir), output=str(output_dir))
        return BackupSummary(output_dir=output_dir)
    finally:
        conn.close()


class BackupSummary:
    """Outcome of a backup run."""

    def __init__(self, *, output_dir: Path) -> None:
        self.output_dir = output_dir


__all__ = ["BackupSummary", "run_backup"]
