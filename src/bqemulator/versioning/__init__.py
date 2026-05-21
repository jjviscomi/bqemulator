"""Phase 7 — versioning: snapshots, time travel, clones, materialized views.

Module layout:

* :mod:`bqemulator.versioning.snapshots` — capture snapshots on every
  committed DML/DDL; lookup by target timestamp; periodic GC.
* :mod:`bqemulator.versioning.time_travel` — ``FOR SYSTEM_TIME AS OF``
  rewriter that redirects reads to the correct snapshot table.
* :mod:`bqemulator.versioning.clone` — ``CREATE TABLE … CLONE`` handler.
* :mod:`bqemulator.versioning.snapshot_table` — ``CREATE SNAPSHOT TABLE``
  handler (USER-kind snapshots that survive retention GC).
* :mod:`bqemulator.versioning.materialized_views` — MV registry with
  event-driven staleness and lazy recompute.
* :mod:`bqemulator.versioning.ddl` — regex-based detector that routes
  Phase 7 DDL statements to the right manager without going through
  the SQL translator.

See ADRs `0009`, `0016`, and `0017` for the locked decisions.
"""

from __future__ import annotations

from bqemulator.versioning.clone import CloneManager
from bqemulator.versioning.ddl import VersioningDDLRouter, is_versioning_ddl
from bqemulator.versioning.materialized_views import MaterializedViewManager
from bqemulator.versioning.snapshot_table import SnapshotTableManager
from bqemulator.versioning.snapshots import SnapshotManager
from bqemulator.versioning.time_travel import rewrite_for_system_time

__all__ = [
    "CloneManager",
    "MaterializedViewManager",
    "SnapshotManager",
    "SnapshotTableManager",
    "VersioningDDLRouter",
    "is_versioning_ddl",
    "rewrite_for_system_time",
]
