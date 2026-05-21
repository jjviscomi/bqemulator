"""Forward-only migration rollback semantics — Phase 10 audit gap #9.

The catalog migration runner (:mod:`bqemulator.catalog.migrations`) is
forward-only: there is no ``down()``. ADR 0006 locked this in — once a
migration is applied, the catalog never goes back. That decision puts
the integrity bar on two contracts:

1. **Atomic recording.** A migration's ``up()`` runs, and *only if it
   succeeds* does ``_record_applied`` write the version row. If a
   migration raises mid-flight, the version row is *not* written and
   the next startup re-runs that migration from scratch.

2. **Idempotent forward replay.** Every catalog migration uses
   ``CREATE TABLE IF NOT EXISTS`` so a partial application that
   created some tables can be re-applied without raising on the
   already-created ones. Combined with (1), this means a crash mid-
   migration recovers automatically on the next startup.

These tests verify both contracts at the unit-test tier. The chaos
storage tier extends them with subprocess-level crashes and a
"required column missing" scenario (e.g. an operator manually dropped
``metadata_json`` from the ``datasets`` table). The unit tests below
make the contracts unambiguous without needing a subprocess.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bqemulator.catalog.migrations import (
    _current_version,
    _ensure_version_table,
    run_migrations,
)
from bqemulator.config import Settings
from bqemulator.storage.engine import CATALOG_SCHEMA, DuckDBEngine

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_exists(engine: DuckDBEngine, name: str) -> bool:
    row = engine.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [CATALOG_SCHEMA, name],
    ).fetchone()
    return bool(row and row[0] > 0)


def _drop_column(engine: DuckDBEngine, table: str, column: str) -> None:
    engine.execute(f'ALTER TABLE "{CATALOG_SCHEMA}"."{table}" DROP COLUMN "{column}"')


# ---------------------------------------------------------------------------
# Atomic recording — version row is written only on full success.
# ---------------------------------------------------------------------------


class _FakeMigration:
    """A migration that raises on apply (used to model a partial apply)."""

    VERSION = 9999
    DESCRIPTION = "Synthetic failing migration for the rollback test"

    def __init__(self, *, raise_on_up: bool = True) -> None:
        self._raise = raise_on_up
        self.applied = False

    def up(self, engine: DuckDBEngine) -> None:
        # Create one table, then raise — models the "succeeded partway"
        # crash mode where some forward state landed before the failure.
        engine.execute(
            f'CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."rollback_test_t1" (id BIGINT)'
        )
        if self._raise:
            raise RuntimeError("simulated migration failure mid-apply")
        engine.execute(
            f'CREATE TABLE IF NOT EXISTS "{CATALOG_SCHEMA}"."rollback_test_t2" (id BIGINT)'
        )
        self.applied = True


@pytest.fixture
async def started_engine(
    ephemeral_settings: Settings,
) -> Iterator[DuckDBEngine]:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    try:
        yield engine
    finally:
        await engine.stop()


@pytest.mark.asyncio
class TestPartialApplyAtomicity:
    """A failing migration must NOT leave a version row behind."""

    async def test_failed_migration_does_not_record_version(
        self,
        started_engine: DuckDBEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The version row is only written on full success.

        We monkeypatch ``_discover_migrations`` so the runner sees a
        single failing migration; we then assert the runner re-raises
        the underlying exception (the runner does NOT swallow it) and
        no version row landed.
        """
        fake = _FakeMigration(raise_on_up=True)

        monkeypatch.setattr(
            "bqemulator.catalog.migrations._discover_migrations",
            lambda: [fake],
        )

        with pytest.raises(RuntimeError, match="simulated migration failure"):
            run_migrations(started_engine)

        # Version table exists (created by _ensure_version_table) but is
        # empty — no migration completed.
        assert _table_exists(started_engine, "_schema_version")
        version = _current_version(started_engine)
        assert version == 0
        # The partially-applied table from the failing migration is
        # left in place — that's exactly what forward-only with
        # CREATE TABLE IF NOT EXISTS expects. The next attempt will
        # find it already present and skip the create silently.
        assert _table_exists(started_engine, "rollback_test_t1")
        assert not _table_exists(started_engine, "rollback_test_t2")

    async def test_partial_apply_recovers_on_replay(
        self,
        started_engine: DuckDBEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A subsequent successful run finishes the partial apply.

        Models the operator-recovery flow: migration 9999 failed at
        startup, operator fixed the underlying issue (e.g. a permission
        problem), restarted, and the next run completes the migration.
        Because the migration uses ``CREATE TABLE IF NOT EXISTS``, the
        already-created table doesn't block the retry.
        """
        first = _FakeMigration(raise_on_up=True)
        monkeypatch.setattr(
            "bqemulator.catalog.migrations._discover_migrations",
            lambda: [first],
        )
        with pytest.raises(RuntimeError):
            run_migrations(started_engine)

        assert _current_version(started_engine) == 0

        # Second run: operator fixed the issue, migration now succeeds.
        recovered = _FakeMigration(raise_on_up=False)
        monkeypatch.setattr(
            "bqemulator.catalog.migrations._discover_migrations",
            lambda: [recovered],
        )
        run_migrations(started_engine)

        assert recovered.applied
        # Both tables are now present and the version row landed.
        assert _table_exists(started_engine, "rollback_test_t1")
        assert _table_exists(started_engine, "rollback_test_t2")
        assert _current_version(started_engine) == 9999


# ---------------------------------------------------------------------------
# Required-column missing — operator drop / migration skew.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCatalogColumnDriftDetection:
    """When the catalog drifts from the migration's expected shape.

    The chaos storage tier exercises this scenario via subprocess. The
    unit-tier check below confirms that the *next* operation against
    the missing column raises a clear DuckDB ``CatalogException`` (or
    similar) — i.e. we don't silently swallow the drift. An operator
    sees the broken column referenced in the error and can act.
    """

    async def test_missing_required_column_surfaces_on_next_query(
        self,
        started_engine: DuckDBEngine,
    ) -> None:
        run_migrations(started_engine)
        # Sanity: column is present before we drop it.
        before = started_engine.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? AND column_name = ?",
            [CATALOG_SCHEMA, "datasets", "metadata_json"],
        ).fetchone()
        assert before is not None

        _drop_column(started_engine, "datasets", "metadata_json")

        # Any read of metadata_json now raises a documented DuckDB
        # exception — not a silent NULL, not a Python AttributeError.
        # The operator sees the column name and the table identity in
        # the error message and can pivot to either restoring from a
        # backup or running ``bqemulator import`` against a real BQ
        # project to rebuild the schema (per ADR 0020).
        with pytest.raises(Exception) as excinfo:
            started_engine.execute(
                f'SELECT metadata_json FROM "{CATALOG_SCHEMA}"."datasets"',
            ).fetchall()
        assert "metadata_json" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Version-table bootstrap — _ensure_version_table is idempotent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestVersionTableBootstrap:
    """The version table itself must be safe to create repeatedly."""

    async def test_ensure_version_table_is_idempotent(
        self,
        started_engine: DuckDBEngine,
    ) -> None:
        _ensure_version_table(started_engine)
        _ensure_version_table(started_engine)
        _ensure_version_table(started_engine)
        # No raise; the table exists and is queryable.
        assert _current_version(started_engine) == 0
