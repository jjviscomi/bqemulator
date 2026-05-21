"""Derive ``INFORMATION_SCHEMA.PARTITIONS``-shaped rows from live storage.

BigQuery exposes ``INFORMATION_SCHEMA.PARTITIONS`` as one row per
partition slice of a partitioned table; for unpartitioned tables the
table itself is treated as a single partition with
``partition_id='__NULL__'`` (BigQuery's documented sentinel).

The emulator does not persist a per-partition catalog row — partitions
are an implicit consequence of the underlying DuckDB data plus the
partitioning configuration on :class:`TableMeta`. This module bridges
the gap: given a table and the engine, it enumerates the distinct
partition values by running a ``GROUP BY`` over the partition column
and counts the rows in each bucket.

This is what powers G4's ``INFORMATION_SCHEMA.PARTITIONS`` rewriter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bqemulator.catalog.models import PartitionMeta

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.catalog.models import TableMeta
    from bqemulator.storage.engine import DuckDBEngine


# Map a BigQuery time-partitioning granularity to the strftime format
# string DuckDB uses. BigQuery's documented partition_id format pads
# the date components to fixed widths — DuckDB's strftime matches
# (``%Y`` always emits a 4-digit year for years ≥ 1000 — earlier years
# are out of scope for partitioning).
_GRANULARITY_FORMAT: dict[str, str] = {
    "DAY": "%Y%m%d",
    "HOUR": "%Y%m%d%H",
    "MONTH": "%Y%m",
    "YEAR": "%Y",
}


def list_partitions_for_table(
    engine: DuckDBEngine,
    table: TableMeta,
) -> tuple[PartitionMeta, ...]:
    """Return the distinct partitions for ``table`` based on live data.

    Inspects ``table.time_partitioning`` and ``table.range_partitioning``;
    if either is set, runs a ``GROUP BY`` over the corresponding column
    against the table's physical DuckDB storage (``{project}__{dataset}.
    {table_id}``) to discover the partition keys and row counts.

    Tables that are neither time- nor range-partitioned are returned as
    a single ``__NULL__``-keyed partition matching BigQuery's behaviour.

    All ``last_modified_time`` fields are copied from the table's
    cached :class:`TableMeta` value — partition-level mtimes are not
    tracked separately in the emulator.
    """
    schema = f"{table.project_id}__{table.dataset_id}"
    physical_ref = f'"{schema}"."{table.table_id}"'

    if table.time_partitioning is not None:
        return _list_time_partitions(engine, table, physical_ref)
    if table.range_partitioning is not None:
        return _list_range_partitions(engine, table, physical_ref)
    return _list_unpartitioned(engine, table, physical_ref)


def _list_time_partitions(
    engine: DuckDBEngine,
    table: TableMeta,
    physical_ref: str,
) -> tuple[PartitionMeta, ...]:
    assert table.time_partitioning is not None  # noqa: S101 — type narrowing
    tp = table.time_partitioning
    granularity = tp.type
    fmt = _GRANULARITY_FORMAT.get(granularity)
    if fmt is None:  # pragma: no cover — Pydantic literal-restricted upstream
        return ()

    if tp.field is None:
        # Ingestion-time partitioned: the emulator does not track an
        # ingestion-time column per row, so the entire table collapses
        # to a single partition keyed by the current emulator clock's
        # last_modified_time (BigQuery's PARTITIONS view never returns
        # NULL partition_id for ingestion-time tables — it uses the
        # partition date the rows were ingested on).
        count = _count_rows(engine, physical_ref)
        pid = table.last_modified_time.strftime(fmt)
        return (
            PartitionMeta(
                table_catalog=table.project_id,
                table_schema=table.dataset_id,
                table_name=table.table_id,
                partition_id=pid,
                total_rows=count,
                last_modified_time=table.last_modified_time,
            ),
        )

    col = _quote_identifier(tp.field)
    sql = (
        f"SELECT strftime({col}, '{fmt}') AS pid, COUNT(*) AS rc "
        f"FROM {physical_ref} "
        f"WHERE {col} IS NOT NULL "
        f"GROUP BY 1 ORDER BY 1"
    )
    rows = engine.execute(sql).fetchall()
    null_count = _count_rows(
        engine,
        physical_ref,
        where=f"{col} IS NULL",
    )

    partitions: list[PartitionMeta] = [
        PartitionMeta(
            table_catalog=table.project_id,
            table_schema=table.dataset_id,
            table_name=table.table_id,
            partition_id=str(row[0]),
            total_rows=int(row[1]),
            last_modified_time=table.last_modified_time,
        )
        for row in rows
    ]
    if null_count > 0:
        partitions.append(
            PartitionMeta(
                table_catalog=table.project_id,
                table_schema=table.dataset_id,
                table_name=table.table_id,
                partition_id="__NULL__",
                total_rows=null_count,
                last_modified_time=table.last_modified_time,
            ),
        )
    return tuple(partitions)


def _list_range_partitions(
    engine: DuckDBEngine,
    table: TableMeta,
    physical_ref: str,
) -> tuple[PartitionMeta, ...]:
    assert table.range_partitioning is not None  # noqa: S101 — type narrowing
    rp = table.range_partitioning
    col = _quote_identifier(rp.field)
    bucket_expr = (
        f"CAST({rp.start} + FLOOR(({col} - {rp.start}) / {rp.interval}) * {rp.interval} AS BIGINT)"
    )
    sql = (
        f"SELECT {bucket_expr} AS pid, COUNT(*) AS rc "
        f"FROM {physical_ref} "
        f"WHERE {col} IS NOT NULL AND {col} >= {rp.start} AND {col} < {rp.end} "
        f"GROUP BY 1 ORDER BY 1"
    )
    rows = engine.execute(sql).fetchall()
    return tuple(
        PartitionMeta(
            table_catalog=table.project_id,
            table_schema=table.dataset_id,
            table_name=table.table_id,
            partition_id=str(int(row[0])),
            total_rows=int(row[1]),
            last_modified_time=table.last_modified_time,
        )
        for row in rows
    )


def _list_unpartitioned(
    engine: DuckDBEngine,
    table: TableMeta,
    physical_ref: str,
) -> tuple[PartitionMeta, ...]:
    count = _count_rows(engine, physical_ref)
    return (
        PartitionMeta(
            table_catalog=table.project_id,
            table_schema=table.dataset_id,
            table_name=table.table_id,
            partition_id="__NULL__",
            total_rows=count,
            last_modified_time=table.last_modified_time,
        ),
    )


def _count_rows(engine: DuckDBEngine, physical_ref: str, *, where: str = "") -> int:
    suffix = f" WHERE {where}" if where else ""
    try:
        row = engine.execute(f"SELECT COUNT(*) FROM {physical_ref}{suffix}").fetchone()
    except Exception:  # noqa: BLE001 — best-effort; missing table → 0 rows
        return 0
    return int(row[0]) if row else 0


def _quote_identifier(name: str) -> str:
    """Quote a DuckDB identifier, escaping embedded double quotes."""
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


__all__ = ["list_partitions_for_table"]
