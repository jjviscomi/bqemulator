"""Restore a backup directory back into a persistent ``data_dir``.

``bqemulator restore`` is the inverse of :mod:`bqemulator.commands.backup`.
It removes any existing ``bqemulator.duckdb`` under ``data_dir`` (so the
restore is a clean replace, not a merge) and runs DuckDB's
``IMPORT DATABASE FROM '<dir>'`` to materialise the catalog and table
rows in a fresh database file.

Like :mod:`bqemulator.commands.backup`, restore talks to DuckDB
directly (no engine startup) so the ``CREATE SCHEMA`` statements
replayed by ``IMPORT DATABASE`` don't collide with the engine's own
``CREATE SCHEMA IF NOT EXISTS`` for the catalog/snapshot schemas.
Restore runs **offline** — no other process should be holding a write
lock on the destination ``bqemulator.duckdb``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.observability.logging_ import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

_log = get_logger(__name__)


def run_restore(*, data_dir: Path, input_dir: Path, force: bool = False) -> RestoreSummary:
    """Restore ``input_dir`` (a backup) into ``data_dir``.

    Args:
        data_dir: Destination ``data_dir``. Created if absent.
        input_dir: A directory produced by ``bqemulator backup``.
        force: When True, overwrite an existing ``bqemulator.duckdb``
            in ``data_dir``. When False, refuse if the destination file
            already exists.

    Returns:
        A :class:`RestoreSummary` carrying the resolved destination.

    Raises:
        FileNotFoundError: When ``input_dir`` is not a backup directory.
        FileExistsError: When ``data_dir/bqemulator.duckdb`` exists and
            ``force`` is False.
    """
    # DuckDB's EXPORT DATABASE writes ``schema.sql`` at the top of the
    # directory; we use its presence as the canonical backup marker.
    if not (input_dir / "schema.sql").exists():
        raise FileNotFoundError(f"Not a bqemulator backup directory: {input_dir}")

    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "bqemulator.duckdb"
    if db_path.exists():
        if not force:
            raise FileExistsError(
                f"Destination already exists (pass --force to overwrite): {db_path}",
            )
        db_path.unlink()
        # DuckDB also writes a side-car WAL on shutdown; remove it too so
        # the restored database isn't recovered into an inconsistent state.
        wal = db_path.with_suffix(".duckdb.wal")
        if wal.exists():
            wal.unlink()

    import duckdb

    conn = duckdb.connect(str(db_path), read_only=False)
    try:
        # Load spatial in case the backup contains GEOGRAPHY columns; the
        # IMPORT will need it to deserialize ``GEOMETRY`` Parquet values.
        try:
            conn.execute("INSTALL spatial")
            conn.execute("LOAD spatial")
        except Exception as exc:  # noqa: BLE001
            _log.warning("restore.spatial_skipped", error=str(exc))

        quoted_source = str(input_dir).replace("'", "''")
        conn.execute(f"IMPORT DATABASE '{quoted_source}'")
        _log.info("restore.done", data_dir=str(data_dir), input=str(input_dir))
        return RestoreSummary(data_dir=data_dir)
    finally:
        conn.close()


class RestoreSummary:
    """Outcome of a restore run."""

    def __init__(self, *, data_dir: Path) -> None:
        self.data_dir = data_dir


__all__ = ["RestoreSummary", "run_restore"]
