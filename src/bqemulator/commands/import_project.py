"""Mirror a real BigQuery project's schemas into the local catalog.

The ``bqemulator import`` CLI subcommand delegates to :func:`run_import`,
which:

1. Connects to the real BigQuery REST API using Application Default
   Credentials.
2. Lists datasets in the source project (optionally filtered).
3. For each dataset, reads tables and routines.
4. Writes the metadata into a local persistent DuckDB catalog at
   ``data_dir/bqemulator.duckdb``.

No row data is copied — this is a schema-only mirror so local queries
see the same shape as production. Use :mod:`bqemulator.commands.seed`
afterwards to populate test data.

Requires the ``import`` extra (``pip install 'bqemulator[import]'``).
The :mod:`bqemulator.cli` wrapper surfaces a clean error if
``google.cloud.bigquery`` is not installed.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
from bqemulator.catalog.models import (
    DatasetMeta,
    RoutineArgument,
    RoutineMeta,
    TableFieldSchema,
    TableMeta,
    TableSchema,
)
from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.clock import SystemClock
from bqemulator.domain.errors import DomainError
from bqemulator.observability.logging_ import get_logger
from bqemulator.storage.engine import DuckDBEngine

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path


_log = get_logger(__name__)


def run_import(
    *,
    source_project: str,
    dataset_filters: list[str] | None,
    data_dir: Path,
    target_project: str | None = None,
) -> ImportSummary:
    """Mirror ``source_project`` schemas into the local catalog at ``data_dir``.

    Args:
        source_project: The real BigQuery project id to read from.
        dataset_filters: Optional whitelist of dataset ids to import. When
            ``None`` or empty, every accessible dataset is mirrored.
        data_dir: Directory holding the local persistent
            ``bqemulator.duckdb`` file. Created if absent.
        target_project: Project id used for the mirrored metadata. When
            ``None``, ``source_project`` is reused.

    Returns:
        A summary count of mirrored datasets / tables / routines.

    Raises:
        DomainError: When the local catalog refuses an entity (validation
            errors propagate cleanly from the repository).
    """
    return asyncio.run(
        _run_import_async(
            source_project=source_project,
            dataset_filters=dataset_filters,
            data_dir=data_dir,
            target_project=target_project,
        ),
    )


async def _run_import_async(
    *,
    source_project: str,
    dataset_filters: list[str] | None,
    data_dir: Path,
    target_project: str | None,
) -> ImportSummary:
    """Async implementation of :func:`run_import`."""
    # Deferred import — the ``import`` extra owns google-cloud-bigquery.
    from google.cloud import bigquery

    target = target_project or source_project
    client = bigquery.Client(project=source_project)
    settings = Settings(
        persistence_mode=PersistenceMode.PERSISTENT,
        data_dir=data_dir,
    )
    engine = DuckDBEngine(settings)
    await engine.start()
    try:
        catalog = DuckDBCatalogRepository(engine)
        catalog.ensure_ready()
        clock = SystemClock()
        summary = ImportSummary()

        wanted = set(dataset_filters or [])
        for dataset_ref in client.list_datasets(project=source_project):
            dataset_id: str = dataset_ref.dataset_id
            if wanted and dataset_id not in wanted:
                continue
            ds = client.get_dataset(dataset_ref.reference)
            _mirror_dataset(
                catalog=catalog,
                source_dataset=ds,
                target_project=target,
                clock_now=clock.now().isoformat(),
            )
            summary.datasets += 1

            for table_item in client.list_tables(ds.reference):
                tbl = client.get_table(table_item.reference)
                _mirror_table(
                    catalog=catalog,
                    source_table=tbl,
                    target_project=target,
                    target_dataset=dataset_id,
                    clock_now=clock.now().isoformat(),
                )
                summary.tables += 1

            for routine_item in client.list_routines(ds.reference):
                rtn = client.get_routine(routine_item.reference)
                _mirror_routine(
                    catalog=catalog,
                    source_routine=rtn,
                    target_project=target,
                    target_dataset=dataset_id,
                    clock_now=clock.now().isoformat(),
                )
                summary.routines += 1
        _log.info(
            "import_project.done",
            source=source_project,
            target=target,
            datasets=summary.datasets,
            tables=summary.tables,
            routines=summary.routines,
        )
        return summary
    finally:
        await engine.stop()


def _mirror_dataset(
    *,
    catalog: DuckDBCatalogRepository,
    source_dataset: Any,
    target_project: str,
    clock_now: str,
) -> None:
    """Insert (or replace) a single mirrored dataset in the local catalog."""
    from datetime import datetime

    ds = DatasetMeta(
        project_id=target_project,
        dataset_id=source_dataset.dataset_id,
        friendly_name=getattr(source_dataset, "friendly_name", None),
        description=getattr(source_dataset, "description", None),
        labels=dict(source_dataset.labels or {}),
        location=getattr(source_dataset, "location", None) or "US",
        creation_time=datetime.fromisoformat(clock_now),
        last_modified_time=datetime.fromisoformat(clock_now),
        etag=f"imported-{source_dataset.dataset_id}",
    )
    existing = catalog.get_dataset(target_project, source_dataset.dataset_id)
    if existing is None:
        catalog.create_dataset(ds)
    else:
        catalog.update_dataset(ds)


def _mirror_table(
    *,
    catalog: DuckDBCatalogRepository,
    source_table: Any,
    target_project: str,
    target_dataset: str,
    clock_now: str,
) -> None:
    """Insert a single mirrored table (schema only) in the local catalog."""
    from datetime import datetime

    fields = tuple(_convert_field(f) for f in (source_table.schema or ()))
    schema = TableSchema(fields=fields)
    table_type = getattr(source_table, "table_type", None) or "TABLE"
    view_query = None
    if str(table_type).upper() == "VIEW":
        view = getattr(source_table, "view_query", None)
        if view is not None:
            view_query = str(view)

    tbl = TableMeta(
        project_id=target_project,
        dataset_id=target_dataset,
        table_id=source_table.table_id,
        table_type=str(table_type).upper(),  # type: ignore[arg-type]
        schema=schema,
        friendly_name=getattr(source_table, "friendly_name", None),
        description=getattr(source_table, "description", None),
        labels=dict(source_table.labels or {}),
        creation_time=datetime.fromisoformat(clock_now),
        last_modified_time=datetime.fromisoformat(clock_now),
        num_rows=0,
        num_bytes=0,
        etag=f"imported-{source_table.table_id}",
        view_query=view_query,
    )
    try:
        catalog.create_table(tbl)
    except DomainError:
        catalog.update_table(tbl)


def _mirror_routine(
    *,
    catalog: DuckDBCatalogRepository,
    source_routine: Any,
    target_project: str,
    target_dataset: str,
    clock_now: str,
) -> None:
    """Insert a single mirrored routine (UDF / TVF / procedure) in the catalog."""
    from datetime import datetime

    raw_args = getattr(source_routine, "arguments", None) or ()
    args = tuple(_convert_argument(a) for a in raw_args)
    rtn = RoutineMeta(
        project_id=target_project,
        dataset_id=target_dataset,
        routine_id=source_routine.routine_id,
        routine_type=str(getattr(source_routine, "type_", "SCALAR_FUNCTION") or "SCALAR_FUNCTION"),  # type: ignore[arg-type]
        language=str(getattr(source_routine, "language", "SQL") or "SQL"),  # type: ignore[arg-type]
        definition_body=str(getattr(source_routine, "body", "") or ""),
        arguments=args,
        return_type=getattr(source_routine, "return_type", None),
        description=getattr(source_routine, "description", None),
        creation_time=datetime.fromisoformat(clock_now),
        last_modified_time=datetime.fromisoformat(clock_now),
        etag=f"imported-{source_routine.routine_id}",
    )
    try:
        catalog.create_routine(rtn)
    except DomainError:
        catalog.update_routine(rtn)


def _convert_field(field: Any) -> TableFieldSchema:
    """Convert a ``google.cloud.bigquery.SchemaField`` to our model."""
    nested = tuple(_convert_field(f) for f in getattr(field, "fields", None) or ())
    mode = getattr(field, "mode", None) or "NULLABLE"
    return TableFieldSchema(
        name=field.name,
        type=str(field.field_type),
        mode=str(mode).upper(),  # type: ignore[arg-type]
        fields=nested,
        description=getattr(field, "description", None),
    )


def _convert_argument(arg: Any) -> RoutineArgument:
    """Convert a routine argument from the client model to ours."""
    return RoutineArgument(
        name=getattr(arg, "name", None) or "",
        data_type=getattr(arg, "data_type", None),
    )


class ImportSummary:
    """Counts of mirrored entities for CLI-level reporting."""

    def __init__(self) -> None:
        self.datasets = 0
        self.tables = 0
        self.routines = 0

    def as_dict(self) -> dict[str, int]:
        """Return counts as a plain dict."""
        return {
            "datasets": self.datasets,
            "tables": self.tables,
            "routines": self.routines,
        }


__all__ = ["ImportSummary", "run_import"]
