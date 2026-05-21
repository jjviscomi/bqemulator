"""Catalog schema migrations.

Migrations are numbered, append-only Python modules in this package. Each
module exposes:

* ``VERSION: int`` — the integer version this migration establishes.
* ``DESCRIPTION: str`` — a one-line summary.
* ``def up(engine: DuckDBEngine) -> None`` — runs the migration forward.

A simple registry table, ``_bqemulator_catalog._schema_version``, tracks
which migrations have been applied. :func:`run_migrations` applies any
pending migrations in order.

This is deliberately simpler than Alembic (no downgrade path, no
autogenerate); the catalog is an implementation detail owned entirely by
bqemulator and older versions are not supported once migrated forward.
"""

from __future__ import annotations

from importlib import import_module
import pkgutil
from typing import TYPE_CHECKING, Protocol

from bqemulator.observability.logging_ import get_logger
from bqemulator.storage.engine import CATALOG_SCHEMA

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.storage.engine import DuckDBEngine

_log = get_logger(__name__)

_VERSION_TABLE = f'"{CATALOG_SCHEMA}"."_schema_version"'


class Migration(Protocol):
    """Protocol every migration module implements."""

    VERSION: int
    DESCRIPTION: str

    def up(self, engine: DuckDBEngine) -> None:
        """Run the migration forward against ``engine``."""
        ...


def _discover_migrations() -> list[Migration]:
    """Discover all migration modules in this package, sorted by VERSION."""
    migrations: list[Migration] = []
    for mod_info in pkgutil.iter_modules(__path__):
        name = mod_info.name
        if not name.startswith("m"):
            continue
        module = import_module(f"{__name__}.{name}")
        if not hasattr(module, "VERSION") or not hasattr(module, "up"):
            continue
        migrations.append(module)
    migrations.sort(key=lambda m: m.VERSION)
    return migrations


def _ensure_version_table(engine: DuckDBEngine) -> None:
    engine.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_VERSION_TABLE} (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP WITH TIME ZONE DEFAULT current_timestamp,
            description VARCHAR NOT NULL
        )
        """,
    )


def _current_version(engine: DuckDBEngine) -> int:
    result = engine.execute(
        f"SELECT COALESCE(MAX(version), 0) FROM {_VERSION_TABLE}",
    ).fetchone()
    return int(result[0]) if result is not None else 0


def _record_applied(engine: DuckDBEngine, migration: Migration) -> None:
    engine.execute(
        f"INSERT INTO {_VERSION_TABLE} (version, description) VALUES (?, ?)",
        [migration.VERSION, migration.DESCRIPTION],
    )


def run_migrations(engine: DuckDBEngine) -> None:
    """Apply any pending migrations in order.

    Idempotent — a migration at or below the current version is skipped.
    """
    _ensure_version_table(engine)
    current = _current_version(engine)
    _log.debug("catalog.migrations.start", current_version=current)

    pending = [m for m in _discover_migrations() if current < m.VERSION]
    if not pending:
        _log.debug("catalog.migrations.none_pending")
        return

    for migration in pending:
        _log.info(
            "catalog.migration.apply",
            version=migration.VERSION,
            description=migration.DESCRIPTION,
        )
        migration.up(engine)
        _record_applied(engine, migration)

    _log.info("catalog.migrations.done", applied=len(pending), version=_current_version(engine))


__all__ = ["Migration", "run_migrations"]
