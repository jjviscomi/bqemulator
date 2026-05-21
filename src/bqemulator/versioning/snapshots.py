"""Snapshot capture, lookup, and garbage collection.

See ADR 0009 (snapshot-layer architecture) and ADR 0016 (POST-change
capture + ``snapshot_time ≤ target`` lookup).

Every successful DML or DDL that modifies a user table captures a
snapshot by copying the current rows into a uniquely-named table in
the ``_bqemulator_snapshots`` DuckDB schema. The catalog keeps the
``(snapshot_id, source project/dataset/table, snapshot_time, kind,
expires_at)`` metadata; the lookup logic reads it to resolve
``FOR SYSTEM_TIME AS OF`` queries.

A periodic GC task drops ``AUTO`` snapshots past their retention.
``USER`` snapshots (the backing table of ``CREATE SNAPSHOT TABLE``)
never expire automatically.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
import uuid

from bqemulator.catalog.migrations.m002_versioning import SNAPSHOTS_SCHEMA
from bqemulator.catalog.models import SnapshotMeta
from bqemulator.domain.errors import OutOfRangeError, ValidationError
from bqemulator.domain.events import TableDataChanged
from bqemulator.observability.logging_ import get_logger
from bqemulator.storage.sql_identifiers import quoted_table_ref

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.catalog.repository import CatalogRepository
    from bqemulator.domain.clock import Clock
    from bqemulator.domain.events import EventBus
    from bqemulator.storage.engine import DuckDBEngine

_log = get_logger(__name__)

# Snapshot id grammar: ``s_<20-digit nanos>_<8-hex>`` — sortable and
# SQL-safe. ``_SQL_SAFE_ID_RE`` in storage.sql_identifiers already
# whitelists this form.
_ID_HEX_LEN = 8


class SnapshotManager:
    """Capture and resolve time-travel snapshots."""

    def __init__(
        self,
        engine: DuckDBEngine,
        catalog: CatalogRepository,
        clock: Clock,
        events: EventBus,
        *,
        retention_days: int,
    ) -> None:
        self._engine = engine
        self._catalog = catalog
        self._clock = clock
        self._events = events
        self._retention_days = retention_days

    # -- Capture ---------------------------------------------------------

    def capture(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        *,
        kind: str = "AUTO",
    ) -> SnapshotMeta | None:
        """Capture a snapshot of ``project.dataset.table``.

        Called under the caller's write lock — this function does not
        acquire the engine write lock itself.

        Returns the :class:`SnapshotMeta` on success. Returns ``None``
        if the source table does not exist (e.g., the caller tried to
        snapshot a table that was just dropped).
        """
        source = self._catalog.get_table(project_id, dataset_id, table_id)
        if source is None:
            return None

        now = self._clock.now()
        snapshot_id = _new_snapshot_id(now)

        src_ref = quoted_table_ref(project_id, dataset_id, table_id)
        snap_ref = f'"{SNAPSHOTS_SCHEMA}"."{snapshot_id}"'

        self._engine.execute(
            f"CREATE TABLE {snap_ref} AS SELECT * FROM {src_ref}",
        )

        expires_at = None if kind == "USER" else now + timedelta(days=self._retention_days)
        meta = SnapshotMeta(
            snapshot_id=snapshot_id,
            project_id=project_id,
            dataset_id=dataset_id,
            table_id=table_id,
            snapshot_time=now,
            kind=kind,  # type: ignore[arg-type]
            duckdb_schema=SNAPSHOTS_SCHEMA,
            duckdb_table=snapshot_id,
            expires_at=expires_at,
        )
        self._catalog.create_snapshot(meta)
        return meta

    def record_change(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> SnapshotMeta | None:
        """Capture an AUTO snapshot and publish ``TableDataChanged``.

        Central entry point invoked from every write path after a
        successful commit. The snapshot covers the *new* state of the
        table (POST-change semantics per ADR 0016).
        """
        meta = self.capture(project_id, dataset_id, table_id, kind="AUTO")
        self._events.publish(TableDataChanged(project_id, dataset_id, table_id))
        return meta

    # -- Lookup ----------------------------------------------------------

    def resolve_time_travel(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        target: datetime,
    ) -> SnapshotMeta | None:
        """Return the snapshot that satisfies ``FOR SYSTEM_TIME AS OF target``.

        Per ADR 0016:

        * ``target`` must be in ``[now - retention, now]``. Outside that
          window we raise :class:`OutOfRangeError`.
        * Return the snapshot with the *largest* ``snapshot_time <=
          target``. If no such snapshot exists:

            * If the table has no snapshots at all, return ``None`` —
              the caller should read the live table.
            * Otherwise raise :class:`OutOfRangeError` (target is before
              the table's first observable state).
        """
        now = self._clock.now()
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        if target > now:
            raise OutOfRangeError(
                f"FOR SYSTEM_TIME AS OF is in the future: {target.isoformat()}",
            )
        retention_cutoff = now - timedelta(days=self._retention_days)
        if target < retention_cutoff:
            raise OutOfRangeError(
                "FOR SYSTEM_TIME AS OF is before the retention window "
                f"(retention = {self._retention_days} days)",
            )

        snaps = self._catalog.list_snapshots_for_table(
            project_id,
            dataset_id,
            table_id,
        )
        if not snaps:
            return None

        # largest snap with snapshot_time <= target
        eligible = [s for s in snaps if s.snapshot_time <= target]
        if not eligible:
            raise OutOfRangeError(
                "FOR SYSTEM_TIME AS OF is before the table's first "
                "captured state; no snapshot is available",
            )
        return max(eligible, key=lambda s: s.snapshot_time)

    # -- GC --------------------------------------------------------------

    def purge_expired(self) -> int:
        """Drop AUTO snapshots whose ``expires_at`` is in the past.

        Returns the number of snapshots removed.
        """
        now = self._clock.now()
        removed = 0
        for snap in self._catalog.list_all_snapshots():
            if snap.kind != "AUTO":
                continue
            if snap.expires_at is None or snap.expires_at > now:
                continue
            self.drop_snapshot(snap)
            removed += 1
        if removed:
            _log.info(
                "snapshot.gc.purged",
                count=removed,
                retention_days=self._retention_days,
            )
        return removed

    def drop_snapshot(self, snap: SnapshotMeta) -> None:
        """Drop the physical snapshot table and its catalog row."""
        # _SQL_SAFE_ID_RE validates both parts — paranoia at the boundary.
        if '"' in snap.duckdb_schema or '"' in snap.duckdb_table:
            raise ValidationError("Invalid snapshot identifier")
        with contextlib.suppress(Exception):
            self._engine.execute(
                f'DROP TABLE IF EXISTS "{snap.duckdb_schema}"."{snap.duckdb_table}"',
            )
        self._catalog.delete_snapshot(snap.snapshot_id, not_found_ok=True)

    def drop_snapshots_for_table(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        *,
        include_user: bool = True,
    ) -> int:
        """Drop every snapshot for a source table.

        Called on table deletion to avoid orphan snapshot rows.
        """
        removed = 0
        for snap in self._catalog.list_snapshots_for_table(
            project_id,
            dataset_id,
            table_id,
        ):
            if not include_user and snap.kind == "USER":
                continue
            self.drop_snapshot(snap)
            removed += 1
        return removed

    # -- Lifecycle -------------------------------------------------------

    async def run_gc_loop(self, *, interval_seconds: float) -> None:
        """Periodic GC task. Suitable for ``asyncio.create_task``."""
        while True:
            try:
                self.purge_expired()
            except Exception as exc:  # noqa: BLE001 — GC must never die
                _log.warning("snapshot.gc.error", error=str(exc))
            await asyncio.sleep(interval_seconds)


def _new_snapshot_id(now: datetime) -> str:
    """Return a new snapshot identifier.

    ``s_<nanos>_<hex>`` — nanos ensures ordering when listing, hex
    defeats collision when multiple snapshots share a millisecond.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    nanos = int(now.timestamp() * 1_000_000_000)
    suffix = uuid.uuid4().hex[:_ID_HEX_LEN]
    return f"s_{nanos:020d}_{suffix}"


__all__ = ["SnapshotManager"]
