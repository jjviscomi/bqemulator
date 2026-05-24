"""Frozen Pydantic models for catalog entities.

These models are the single in-memory shape for metadata. They are serialized
to JSON for storage (in DuckDB catalog tables) and to REST responses.

Models are ``frozen=True`` — any mutation must use ``.model_copy(update=...)``
which produces a new instance. This is cheap (shallow copy) and makes the
catalog thread/task-safe by construction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    """Base class for frozen catalog models."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
        arbitrary_types_allowed=False,
    )


# ---------------------------------------------------------------------------
# Schema + field
# ---------------------------------------------------------------------------


FieldMode = Literal["NULLABLE", "REQUIRED", "REPEATED"]


class TableFieldSchema(_Frozen):
    """A single column in a BigQuery table schema.

    Matches the ``TableFieldSchema`` REST resource.
    """

    name: str
    type: str  # INT64 | FLOAT64 | ... | GEOGRAPHY | RANGE | INTERVAL | ...
    mode: FieldMode = "NULLABLE"
    fields: tuple[TableFieldSchema, ...] = ()  # nested STRUCT fields
    description: str | None = None
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    default_value_expression: str | None = None
    collation: str | None = None
    rounding_mode: str | None = None
    # RANGE field subtype. Required when ``type == "RANGE"``; the inner
    # type is one of DATE / DATETIME / TIMESTAMP. Matches BigQuery's
    # REST ``rangeElementType`` shape.
    range_element_type: TableFieldSchema | None = None


TableFieldSchema.model_rebuild()


class TableSchema(_Frozen):
    """Ordered collection of :class:`TableFieldSchema`."""

    fields: tuple[TableFieldSchema, ...] = ()


# ---------------------------------------------------------------------------
# Partitioning + clustering
# ---------------------------------------------------------------------------


PartitionType = Literal["DAY", "HOUR", "MONTH", "YEAR"]


class TimePartitioning(_Frozen):
    """Time-unit partitioning configuration."""

    type: PartitionType = "DAY"
    field: str | None = None  # None => ingestion-time partitioned
    expiration_ms: int | None = None
    require_partition_filter: bool = False


class RangePartitioning(_Frozen):
    """Integer-range partitioning configuration."""

    field: str
    start: int
    end: int
    interval: int


class Clustering(_Frozen):
    """Clustering configuration."""

    fields: tuple[str, ...]


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


class AccessEntry(_Frozen):
    """A single entry in a dataset's ``access`` array.

    Mirrors the BigQuery REST ``Dataset.access`` shape. The fields are
    mutually exclusive — exactly one of ``role + (user_by_email |
    group_by_email | domain | special_group | iam_member)``, ``view``,
    ``dataset``, or ``routine`` should be populated. The model does
    not enforce mutual exclusion (the REST adapter does), so callers
    can deserialize a heterogeneous array uniformly.
    """

    role: str | None = None  # OWNER / WRITER / READER, or roles/...
    user_by_email: str | None = None
    group_by_email: str | None = None
    domain: str | None = None
    special_group: str | None = None
    iam_member: str | None = None
    view: tuple[str, str, str] | None = None  # (project, dataset, view-id)
    routine: tuple[str, str, str] | None = None
    dataset: tuple[str, str] | None = None  # (project, dataset)


class DatasetMeta(_Frozen):
    """Metadata for a BigQuery dataset."""

    project_id: str
    dataset_id: str
    friendly_name: str | None = None
    description: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    location: str = "US"
    default_table_expiration_ms: int | None = None
    default_partition_expiration_ms: int | None = None
    default_collation: str | None = None
    creation_time: datetime
    last_modified_time: datetime
    etag: str
    is_case_insensitive: bool = False
    access_entries: tuple[AccessEntry, ...] = ()


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


TableType = Literal[
    "TABLE",
    "VIEW",
    "MATERIALIZED_VIEW",
    "EXTERNAL",
    "SNAPSHOT",
    "CLONE",
]


class TableMeta(_Frozen):
    """Metadata for a BigQuery table, view, or related entity."""

    project_id: str
    dataset_id: str
    table_id: str
    table_type: TableType = "TABLE"
    schema_: TableSchema = Field(default_factory=TableSchema, alias="schema")
    friendly_name: str | None = None
    description: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    time_partitioning: TimePartitioning | None = None
    range_partitioning: RangePartitioning | None = None
    clustering: Clustering | None = None
    expiration_time: datetime | None = None
    creation_time: datetime
    last_modified_time: datetime
    num_rows: int = 0
    num_bytes: int = 0
    etag: str

    # View-specific
    view_query: str | None = None
    use_legacy_sql: bool = False

    # Snapshot / Clone / Materialized-view specific
    base_table: str | None = None  # project.dataset.table of source
    snapshot_time: datetime | None = None


# ---------------------------------------------------------------------------
# Snapshot — time travel + explicit snapshots
# ---------------------------------------------------------------------------


SnapshotKind = Literal["AUTO", "USER"]


class SnapshotMeta(_Frozen):
    """Metadata for a captured snapshot.

    ``AUTO`` snapshots power ``FOR SYSTEM_TIME AS OF`` time travel. They
    live in the reserved ``_bqemulator_snapshots`` DuckDB schema and
    expire after the configured retention window.

    ``USER`` snapshots back ``CREATE SNAPSHOT TABLE`` statements. They
    live in the regular ``project__dataset`` schema, appear in the
    ``tables`` catalog with ``table_type=SNAPSHOT``, and never expire
    under the retention policy — they are only removed by an explicit
    ``DROP SNAPSHOT TABLE``.
    """

    snapshot_id: str  # DuckDB identifier in the snapshots schema
    project_id: str
    dataset_id: str
    table_id: str  # source table id
    snapshot_time: datetime
    kind: SnapshotKind = "AUTO"
    duckdb_schema: str  # where the snapshot physically lives
    duckdb_table: str  # snapshot table name within the schema
    expires_at: datetime | None = None


# ---------------------------------------------------------------------------
# Materialized view
# ---------------------------------------------------------------------------


class MaterializedViewMeta(_Frozen):
    """Metadata for a materialized view.

    The view's physical rows live in a regular DuckDB table in the
    dataset's schema, so ``TableMeta`` still carries the schema and
    identity. ``MaterializedViewMeta`` captures the additional data
    the refresh subsystem needs: the BigQuery source query, its base
    table dependencies, staleness, and refresh bookkeeping.
    """

    project_id: str
    dataset_id: str
    table_id: str
    view_query: str  # BigQuery SQL stored verbatim
    base_tables: tuple[tuple[str, str, str], ...]  # (project, dataset, table)
    last_refresh_time: datetime
    is_stale: bool = False


# ---------------------------------------------------------------------------
# Row access policy
# ---------------------------------------------------------------------------


class PartitionMeta(_Frozen):
    """Derived metadata for a single partition slice of a partitioned table.

    Not a persisted catalog entity — synthesised on demand from the
    underlying DuckDB rows when ``INFORMATION_SCHEMA.PARTITIONS`` is
    queried. ``partition_id`` follows BigQuery's documented format:

    * DAY-partitioned: ``"YYYYMMDD"`` (e.g. ``"20260520"``).
    * HOUR-partitioned: ``"YYYYMMDDHH"``.
    * MONTH-partitioned: ``"YYYYMM"``.
    * YEAR-partitioned: ``"YYYY"``.
    * Integer-range partitioned: stringified bucket start (e.g. ``"100"``).
    * Unpartitioned tables: ``"__NULL__"`` (BigQuery's documented sentinel).
    """

    table_catalog: str
    table_schema: str
    table_name: str
    partition_id: str
    total_rows: int = 0
    total_logical_bytes: int = 0
    last_modified_time: datetime
    storage_tier: Literal["ACTIVE", "LONG_TERM"] = "ACTIVE"


class RowAccessPolicyMeta(_Frozen):
    """Metadata for a BigQuery row access policy.

    A row access policy restricts a SELECT against ``project.dataset.table``
    to the rows for which ``filter_predicate`` evaluates to TRUE *and*
    where the caller's IAM-member identity matches one of ``grantees``.
    See ADR 0018 for the enforcement model and matching rules.
    """

    project_id: str
    dataset_id: str
    table_id: str
    policy_id: str
    filter_predicate: str
    grantees: tuple[str, ...] = ()
    creation_time: datetime
    last_modified_time: datetime
    etag: str


# ---------------------------------------------------------------------------
# Routine
# ---------------------------------------------------------------------------


RoutineType = Literal["SCALAR_FUNCTION", "PROCEDURE", "TABLE_VALUED_FUNCTION"]
RoutineLanguage = Literal["SQL", "JAVASCRIPT"]


class RoutineArgument(_Frozen):
    """A single argument to a routine."""

    name: str
    argument_kind: Literal["FIXED_TYPE", "ANY_TYPE"] = "FIXED_TYPE"
    mode: Literal["IN", "OUT", "INOUT"] = "IN"
    data_type: dict[str, Any] | None = None  # BigQuery-typed structure


class RoutineMeta(_Frozen):
    """Metadata for a BigQuery routine (UDF, procedure, or TVF)."""

    project_id: str
    dataset_id: str
    routine_id: str
    routine_type: RoutineType
    language: RoutineLanguage = "SQL"
    definition_body: str
    arguments: tuple[RoutineArgument, ...] = ()
    return_type: dict[str, Any] | None = None
    imported_libraries: tuple[str, ...] = ()
    description: str | None = None
    determinism_level: Literal["DETERMINISTIC", "NOT_DETERMINISTIC"] | None = None
    creation_time: datetime
    last_modified_time: datetime
    etag: str


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


JobState = Literal["PENDING", "RUNNING", "DONE"]
JobType = Literal["QUERY", "LOAD", "EXTRACT", "COPY"]


class JobMeta(_Frozen):
    """Metadata for a BigQuery job."""

    project_id: str
    job_id: str
    job_type: JobType
    state: JobState
    configuration: dict[str, Any]
    statistics: dict[str, Any] = Field(default_factory=dict)
    error_result: dict[str, Any] | None = None
    creation_time: datetime
    start_time: datetime | None = None
    end_time: datetime | None = None
    user_email: str | None = None
    etag: str


__all__ = [
    "AccessEntry",
    "Clustering",
    "DatasetMeta",
    "FieldMode",
    "JobMeta",
    "JobState",
    "JobType",
    "MaterializedViewMeta",
    "PartitionMeta",
    "PartitionType",
    "RangePartitioning",
    "RoutineArgument",
    "RoutineLanguage",
    "RoutineMeta",
    "RoutineType",
    "RowAccessPolicyMeta",
    "SnapshotKind",
    "SnapshotMeta",
    "TableFieldSchema",
    "TableMeta",
    "TableSchema",
    "TableType",
    "TimePartitioning",
]
