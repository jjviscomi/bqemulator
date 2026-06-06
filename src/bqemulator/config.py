"""Settings — the single source of runtime configuration.

Configuration sources in priority order (high to low):

1. Explicit constructor kwargs (set by CLI flags in :mod:`bqemulator.cli`).
2. Environment variables prefixed ``BQEMU_``.
3. ``.bqemu.toml`` file in the current working directory.
4. Built-in defaults.

The settings object is constructed once at startup by the composition root
(:mod:`bqemulator.server`) and injected into every subsystem that needs it.
Never access settings via a module-level global.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PersistenceMode(StrEnum):
    """How the emulator persists its DuckDB data."""

    EPHEMERAL = "ephemeral"  # :memory: — fastest start, no persistence
    PERSISTENT = "persistent"  # file on disk, survives restart
    IMPORT = "import"  # file + schema sync from real project


class LogLevel(StrEnum):
    """Supported log levels."""

    TRACE = "trace"
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LogFormat(StrEnum):
    """Log output format."""

    JSON = "json"
    CONSOLE = "console"


class Settings(BaseSettings):
    """Runtime settings for bqemulator.

    Attributes map 1:1 to ``BQEMU_*`` env vars via the prefix and to CLI
    flags via the :mod:`bqemulator.cli` definitions.
    """

    model_config = SettingsConfigDict(
        env_prefix="BQEMU_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=False,
        validate_assignment=True,
    )

    # -- Network ------------------------------------------------------------
    rest_host: str = Field(default="127.0.0.1", description="REST bind host")
    rest_port: int = Field(
        default=9050,
        ge=0,
        le=65535,
        description="REST bind port (0 = random free port)",
    )
    grpc_host: str = Field(default="127.0.0.1", description="gRPC bind host")
    grpc_port: int = Field(
        default=9060,
        ge=0,
        le=65535,
        description="gRPC bind port (0 = random free port)",
    )

    # -- Persistence --------------------------------------------------------
    persistence_mode: PersistenceMode = Field(
        default=PersistenceMode.EPHEMERAL,
        description="How DuckDB data persists across restarts",
    )
    data_dir: Path | None = Field(
        default=None,
        description="Directory for persistent DuckDB file and working state",
    )

    # -- Emulation --------------------------------------------------------
    default_project_id: str = Field(
        default="test-project",
        description="Default project id used when a request omits it",
    )
    gcs_local_root: Path | None = Field(
        default=None,
        description="Local directory that 'gs://' URIs are resolved under",
    )
    export_shard_threshold_bytes: int = Field(
        default=1024 * 1024 * 1024,
        ge=1,
        description=(
            "Approximate per-file size threshold (bytes) for an EXPORT DATA "
            "statement that writes to a wildcard URI. The exported result is "
            "split into ``ceil(in-memory_size / threshold)`` shard files, "
            "mirroring BigQuery's multi-file sharding and 12-digit zero-padded "
            "naming. The default of 1 GiB matches BigQuery's per-file limit so "
            "realistic small exports produce a single file; lower it to "
            "exercise multi-file sharding deterministically in tests. The "
            "in-memory Arrow size is an approximation of the compressed "
            "on-disk size (see ADR 0043)."
        ),
    )
    max_concurrent_jobs: int = Field(
        default=8,
        ge=1,
        le=1024,
        description="Maximum concurrent query/load/extract/copy jobs",
    )
    query_cache_ttl_seconds: int = Field(
        default=86400,
        ge=0,
        description="Query result cache TTL (seconds). 0 disables the cache.",
    )
    time_travel_retention_days: int = Field(
        default=7,
        ge=0,
        le=90,
        description="How long table snapshots are retained for time travel",
    )
    write_api_max_request_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        le=1024 * 1024 * 1024,
        description=(
            "Maximum serialized AppendRowsRequest size in bytes (BigQuery "
            "caps production at 10 MiB; reject anything larger with "
            "RESOURCE_EXHAUSTED)."
        ),
    )
    write_api_max_stream_rows: int = Field(
        default=10_000_000,
        ge=1,
        description=(
            "Maximum number of rows a PENDING/BUFFERED stream may buffer "
            "before the server returns RESOURCE_EXHAUSTED on further "
            "AppendRows. Protects the server from unbounded memory growth "
            "when a client never commits or flushes."
        ),
    )
    udf_js_timeout_ms: int = Field(
        default=5000,
        ge=1,
        le=600_000,
        description=(
            "Per-invocation CPU timeout (milliseconds) for JavaScript UDFs. "
            "BigQuery defaults to 5 s; the real service caps at 60 min. We "
            "cap at 10 min to keep the emulator responsive."
        ),
    )
    udf_js_memory_bytes: int = Field(
        default=256 * 1024 * 1024,
        ge=16 * 1024 * 1024,
        le=4 * 1024 * 1024 * 1024,
        description=(
            "Per-routine V8 heap cap (bytes) for JavaScript UDFs. "
            "256 MiB matches BigQuery's production cap."
        ),
    )
    scripting_max_statements: int = Field(
        default=10_000,
        ge=1,
        description=(
            "Maximum number of statements a single script may execute. "
            "Protects the server from run-away loops in user scripts."
        ),
    )
    scripting_max_loop_iterations: int = Field(
        default=1_000_000,
        ge=1,
        description=(
            "Maximum number of iterations a single loop may run before "
            "the interpreter raises a QuotaExceededError. Per-loop, not "
            "per-script."
        ),
    )
    enable_format_extensions: bool = Field(
        default=True,
        description=(
            "Install and load DuckDB's ``avro`` extension at engine boot "
            "(needed for Avro load/extract). Disable in constrained "
            "deployments that cannot reach the DuckDB extension repository "
            "at ``extensions.duckdb.org``. When disabled, ``AVRO`` load + "
            "extract jobs surface ``UnsupportedFeatureError``; ``ORC`` "
            "load continues to work because it uses the Python "
            "``pyorc`` package (optional ``[orc]`` extra) rather than a "
            "DuckDB extension."
        ),
    )

    # -- Upload host (multipart / resumable upload endpoints) -------------
    upload_max_bytes: int = Field(
        default=1024 * 1024 * 1024,
        ge=1024,
        le=10 * 1024 * 1024 * 1024,
        description=(
            "Maximum total bytes accepted on a single ``/upload/bigquery/"
            "v2`` request (media, multipart, or all resumable chunks "
            "combined). Requests exceeding the cap are rejected with HTTP "
            "413 before the bytes are materialised to disk. BigQuery's "
            "production cap is 5 TiB; the emulator default of 1 GiB keeps "
            "local CI runs bounded. See ADR 0029."
        ),
    )
    upload_session_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=24 * 3600,
        description=(
            "How long a resumable upload session is retained after its "
            "last chunk before being evicted. Sessions older than this "
            "are pruned lazily on the next request that touches the "
            "manager (no background sweeper). See ADR 0029."
        ),
    )
    upload_staging_dir: Path | None = Field(
        default=None,
        description=(
            "Directory used to materialise inbound upload bodies before "
            "the load executor consumes them. Defaults to "
            "``<system tempdir>/bqemu_uploads`` when unset. Files are "
            "deleted in a ``finally`` arm whether the load succeeds or "
            "fails. See ADR 0029."
        ),
    )

    # -- Observability ------------------------------------------------------
    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.JSON
    metrics_enabled: bool = True
    tracing_enabled: bool = False
    otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP gRPC endpoint for trace export (enables tracing when set)",
    )

    # -- Admin --------------------------------------------------------------
    admin_enabled: bool = False

    @field_validator("data_dir", mode="after")
    @classmethod
    def _expand_data_dir(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value.expanduser().resolve()

    @field_validator("gcs_local_root", mode="after")
    @classmethod
    def _expand_gcs_root(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value.expanduser().resolve()

    @field_validator("upload_staging_dir", mode="after")
    @classmethod
    def _expand_upload_staging_dir(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value.expanduser().resolve()

    def duckdb_path(self) -> str:
        """Return the DuckDB connection string for this configuration.

        ``':memory:'`` for ephemeral, otherwise the file path under
        ``data_dir``. Raises :class:`ValueError` if ``data_dir`` is required
        but missing.
        """
        if self.persistence_mode is PersistenceMode.EPHEMERAL:
            return ":memory:"
        if self.data_dir is None:
            raise ValueError(f"persistence_mode={self.persistence_mode.value} requires data_dir")
        return str(self.data_dir / "bqemulator.duckdb")


__all__ = ["LogFormat", "LogLevel", "PersistenceMode", "Settings"]
