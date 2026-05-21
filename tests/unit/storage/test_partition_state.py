"""Unit tests for the G4 partition-state derivation helper.

The helper synthesises INFORMATION_SCHEMA.PARTITIONS-shaped rows from
live DuckDB data + the table's catalog-side partitioning config. These
tests wire a real in-memory DuckDB engine through
:class:`DuckDBEngine` so the GROUP-BY logic is exercised end-to-end.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from bqemulator.catalog.models import (
    RangePartitioning,
    TableMeta,
    TimePartitioning,
)
from bqemulator.config import PersistenceMode, Settings
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.storage.partition_state import list_partitions_for_table

pytestmark = pytest.mark.unit

NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def engine() -> DuckDBEngine:
    settings = Settings(persistence_mode=PersistenceMode.EPHEMERAL)
    eng = DuckDBEngine(settings)
    asyncio.run(eng.start())
    return eng


def _make_table(
    table_id: str = "t",
    *,
    time_partitioning: TimePartitioning | None = None,
    range_partitioning: RangePartitioning | None = None,
) -> TableMeta:
    return TableMeta(
        project_id="p",
        dataset_id="ds",
        table_id=table_id,
        time_partitioning=time_partitioning,
        range_partitioning=range_partitioning,
        creation_time=NOW,
        last_modified_time=NOW,
        etag="e",
    )


def test_day_partitioned_table_returns_partition_per_date(engine: DuckDBEngine) -> None:
    engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
    engine.execute('CREATE TABLE "p__ds"."t" (dt DATE, x INT)')
    engine.execute(
        'INSERT INTO "p__ds"."t" VALUES '
        "(DATE '2026-05-20', 1), (DATE '2026-05-20', 2), "
        "(DATE '2026-05-21', 3)",
    )
    table = _make_table(
        time_partitioning=TimePartitioning(type="DAY", field="dt"),
    )
    parts = list_partitions_for_table(engine, table)
    assert len(parts) == 2
    by_id = {p.partition_id: p for p in parts}
    assert by_id["20260520"].total_rows == 2
    assert by_id["20260521"].total_rows == 1


def test_day_partitioned_with_null_partition(engine: DuckDBEngine) -> None:
    engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
    engine.execute('CREATE TABLE "p__ds"."t_null" (dt DATE, x INT)')
    engine.execute(
        'INSERT INTO "p__ds"."t_null" VALUES (DATE \'2026-05-20\', 1), (NULL, 2), (NULL, 3)',
    )
    table = _make_table(
        table_id="t_null",
        time_partitioning=TimePartitioning(type="DAY", field="dt"),
    )
    parts = list_partitions_for_table(engine, table)
    by_id = {p.partition_id: p for p in parts}
    assert by_id["__NULL__"].total_rows == 2
    assert by_id["20260520"].total_rows == 1


def test_unpartitioned_table_returns_single_null_partition(engine: DuckDBEngine) -> None:
    engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
    engine.execute('CREATE TABLE "p__ds"."unp" (x INT)')
    engine.execute('INSERT INTO "p__ds"."unp" VALUES (1), (2), (3)')
    table = _make_table(table_id="unp")
    parts = list_partitions_for_table(engine, table)
    assert len(parts) == 1
    assert parts[0].partition_id == "__NULL__"
    assert parts[0].total_rows == 3


def test_range_partitioned_table(engine: DuckDBEngine) -> None:
    engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
    engine.execute('CREATE TABLE "p__ds"."r" (bucket INT, x INT)')
    engine.execute(
        'INSERT INTO "p__ds"."r" VALUES (5, 1), (10, 2), (15, 3), (105, 4)',
    )
    table = _make_table(
        table_id="r",
        range_partitioning=RangePartitioning(
            field="bucket",
            start=0,
            end=1000,
            interval=100,
        ),
    )
    parts = list_partitions_for_table(engine, table)
    by_id = {p.partition_id: p for p in parts}
    # bucket [0, 100): three rows; bucket [100, 200): one row
    assert by_id["0"].total_rows == 3
    assert by_id["100"].total_rows == 1


def test_missing_table_returns_empty(engine: DuckDBEngine) -> None:
    """A table whose DuckDB storage doesn't exist yet returns zero rows
    (not an exception) — important for the conformance fixture path
    where setup.sql may not have created the storage yet."""
    table = _make_table(table_id="nope")
    parts = list_partitions_for_table(engine, table)
    # Unpartitioned table → 1 partition with 0 rows (count() returns 0
    # when the table is missing thanks to the best-effort catch).
    assert len(parts) == 1
    assert parts[0].total_rows == 0


def test_hour_partitioned_format(engine: DuckDBEngine) -> None:
    engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
    engine.execute('CREATE TABLE "p__ds"."h" (ts TIMESTAMP)')
    engine.execute(
        'INSERT INTO "p__ds"."h" VALUES '
        "(TIMESTAMP '2026-05-21 14:30:00'), (TIMESTAMP '2026-05-21 15:05:00')",
    )
    table = _make_table(
        table_id="h",
        time_partitioning=TimePartitioning(type="HOUR", field="ts"),
    )
    parts = list_partitions_for_table(engine, table)
    by_id = {p.partition_id for p in parts}
    assert by_id == {"2026052114", "2026052115"}
