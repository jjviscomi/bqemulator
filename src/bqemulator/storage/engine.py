"""DuckDB engine — single-writer connection with async-safe lifecycle.

The emulator uses exactly one :class:`duckdb.DuckDBPyConnection` for the
entire process. Writes serialize on an :class:`asyncio.Lock`; reads do not
take the lock (DuckDB provides internal read/write concurrency for the
same connection object).

The engine also handles startup tasks:

* Ensure the reserved ``_bqemulator_catalog`` schema exists.
* Set the connection's time zone to ``UTC`` (BigQuery TIMESTAMP semantics).
* Install and load the ``spatial`` extension. Required — startup fails
  fast with a clear error if the extension cannot be installed/loaded
  (e.g. offline build with no cached extension), because GEOGRAPHY
  queries depend on it.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.errors import InternalError
from bqemulator.observability.logging_ import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator

    import duckdb
    import pyarrow as pa

_log = get_logger(__name__)

# Reserved schema name for bqemulator's own metadata tables. Users cannot
# create datasets that collide with this (validated elsewhere).
CATALOG_SCHEMA = "_bqemulator_catalog"

# Reserved schema where time-travel snapshot tables live. Like
# ``CATALOG_SCHEMA`` it's created unconditionally at engine startup so
# the snapshot layer works under both memory- and DuckDB-backed
# catalogs (the memory path never runs migrations).
SNAPSHOTS_SCHEMA = "_bqemulator_snapshots"


class DuckDBEngine:
    """Async-friendly wrapper around a single DuckDB connection.

    Usage::

        engine = DuckDBEngine(settings)
        await engine.start()
        async with engine.write_lock():
            engine.execute("INSERT INTO ...")
        await engine.stop()

    :class:`DuckDBEngine` is intentionally synchronous-under-the-hood. DuckDB
    releases the GIL during query execution, so awaiting the write lock
    gives other tasks a chance to progress. Long queries should be run
    inside ``asyncio.to_thread`` by the caller.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._write_lock = asyncio.Lock()
        self._started = False

    # -- Lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Open the DuckDB connection and run startup hooks."""
        if self._started:
            return

        path = self._resolve_path()
        _log.info("duckdb.open", path=path)

        # Import here to keep module import cheap.
        import duckdb

        self._connection = duckdb.connect(path, read_only=False)
        self._apply_session_pragmas()
        self._ensure_catalog_schema()
        self._load_spatial()
        if self._settings.enable_format_extensions:
            self._load_format_extensions()
        self._register_builtin_udfs()
        self._started = True

    async def stop(self) -> None:
        """Close the DuckDB connection (idempotent)."""
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception as exc:  # noqa: BLE001
                _log.warning("duckdb.close_failed", error=str(exc))
            self._connection = None
        self._started = False

    # -- Execution ---------------------------------------------------------

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """Return the underlying DuckDB connection.

        Raises :class:`InternalError` if :meth:`start` has not been called.
        """
        if self._connection is None:
            raise InternalError("DuckDBEngine not started")
        return self._connection

    def execute(self, sql: str, parameters: list[Any] | None = None) -> duckdb.DuckDBPyConnection:
        """Execute a SQL statement. Returns the cursor-like connection."""
        conn = self.connection
        return conn.execute(sql, parameters) if parameters is not None else conn.execute(sql)

    def fetch_arrow(self, sql: str, parameters: list[Any] | None = None) -> pa.Table:
        """Execute and fetch results as a pyarrow.Table.

        Uses ``to_arrow_table()`` (DuckDB >=1.4) with a fallback to the
        deprecated ``fetch_arrow_table()`` for older builds.

        Annotates each field with the original DuckDB column type as
        ``bqemu.duckdb_type`` metadata. DuckDB's JSON / DECIMAL /
        TIMESTAMP_TZ etc. flatten to their underlying physical Arrow
        type (``string`` / ``int64`` / …) at conversion time, so the
        REST schema renderer has no way to distinguish a JSON-typed
        column from a regular VARCHAR after the fact. Preserving the
        DuckDB-side type as field metadata lets
        :func:`bqemulator.jobs.executor._arrow_field_to_schema_entry`
        recover the BigQuery wire-format type for those columns.
        """
        result = self.execute(sql, parameters)
        description = result.description
        arrow_table = (
            result.to_arrow_table()
            if hasattr(result, "to_arrow_table")
            else result.fetch_arrow_table()
        )
        return _annotate_with_duckdb_types(arrow_table, description)

    @asynccontextmanager
    async def write_lock(self) -> AsyncIterator[None]:
        """Acquire the exclusive write lock for this engine.

        All DDL and DML must be wrapped in ``async with engine.write_lock()``.
        Concurrent readers may proceed without the lock.
        """
        async with self._write_lock:
            yield

    # -- Helpers ------------------------------------------------------------

    def _resolve_path(self) -> str:
        mode = self._settings.persistence_mode
        if mode is PersistenceMode.EPHEMERAL:
            return ":memory:"
        if self._settings.data_dir is None:
            raise InternalError(
                f"persistence_mode={mode.value} requires data_dir to be set",
            )
        data_dir = Path(self._settings.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        return str(data_dir / "bqemulator.duckdb")

    def _apply_session_pragmas(self) -> None:
        if self._connection is None:  # internal invariant
            raise InternalError("DuckDBEngine not started")
        # BigQuery TIMESTAMP is always UTC; align DuckDB.
        self._connection.execute("SET TimeZone = 'UTC'")

    def _ensure_catalog_schema(self) -> None:
        if self._connection is None:  # internal invariant
            raise InternalError("DuckDBEngine not started")
        self._connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{CATALOG_SCHEMA}"')
        self._connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{SNAPSHOTS_SCHEMA}"')

    def _register_builtin_udfs(self) -> None:
        """Register Python-backed scalar UDFs that fill DuckDB gaps.

        See :mod:`bqemulator.sql.builtin_udfs` for the list — helpers
        cover BigQuery builtins DuckDB lacks (``JSON_REMOVE``,
        ``JSON_SET``, ``JSON_STRIP_NULLS``, ``NORMALIZE``,
        ``NORMALIZE_AND_CASEFOLD``, ``FARM_FINGERPRINT``).
        """
        if self._connection is None:  # internal invariant
            raise InternalError("DuckDBEngine not started")
        # Import here so the engine module remains importable even if
        # the SQL package has an unrelated load error.
        from bqemulator.sql.builtin_udfs import register_builtin_udfs

        register_builtin_udfs(self._connection)
        _log.debug("duckdb.builtin_udfs_registered")

    def _load_spatial(self) -> None:
        """Load DuckDB's spatial extension; fail fast if unavailable.

        GEOGRAPHY support is backed by DuckDB's spatial extension. The
        extension powers every ``ST_*`` function the emulator translates
        BigQuery GEOGRAPHY queries into. Without it the emulator cannot
        honour its GEOGRAPHY contract, so we surface the failure at
        startup rather than letting query-time ``ST_*`` calls fail with
        confusing catalog errors.
        """
        if self._connection is None:  # internal invariant
            raise InternalError("DuckDBEngine not started")
        try:
            self._connection.execute("INSTALL spatial")
            self._connection.execute("LOAD spatial")
        except Exception as exc:
            _log.error("duckdb.spatial_unavailable", error=str(exc))
            raise InternalError(
                "DuckDB spatial extension is required for bqemulator (GEOGRAPHY "
                "support). INSTALL/LOAD spatial failed — check network access "
                "for the DuckDB extension repository or pre-bundle the extension "
                f"in the image. Underlying error: {exc}",
            ) from exc
        _log.debug("duckdb.spatial_loaded")

    def _load_format_extensions(self) -> None:
        """Best-effort load of DuckDB's ``avro`` extension (G1).

        Unlike ``spatial`` this is best-effort: the load/extract path
        gracefully reports ``UnsupportedFeatureError`` to the client if
        the extension is absent, rather than failing startup. This lets
        offline / air-gapped deployments keep all non-Avro functionality
        even when ``extensions.duckdb.org`` is unreachable. ORC support
        is provided by the Python ``pyorc`` package (optional ``[orc]``
        extra) and does not touch the DuckDB extension repo.
        """
        if self._connection is None:  # internal invariant
            raise InternalError("DuckDBEngine not started")
        try:
            self._connection.execute("INSTALL avro")
            self._connection.execute("LOAD avro")
        except Exception as exc:  # noqa: BLE001 — best-effort
            _log.warning("duckdb.avro_unavailable", error=str(exc))
            return
        _log.debug("duckdb.avro_loaded")


def _annotate_with_duckdb_types(
    table: pa.Table,
    description: list[tuple[Any, ...]] | None,
) -> pa.Table:
    """Return *table* with ``bqemu.duckdb_type`` metadata on each field.

    *description* is the DuckDB cursor's ``description`` attribute — a
    list of per-column tuples whose first two elements are
    ``(name, duckdb_type)``. Fields without a matching description
    entry (or whose DuckDB type is already the natural Arrow mapping)
    are left untouched so the downstream Arrow→BigQuery type mapper
    keeps its existing behaviour.
    """
    import pyarrow as pa  # local import — keeps engine import cheap.

    if not description:
        return table
    by_name: dict[str, str] = {row[0]: str(row[1]) for row in description}
    fields: list[pa.Field] = []
    changed = False
    for field in table.schema:
        duckdb_type = by_name.get(field.name)
        if not duckdb_type:
            fields.append(field)
            continue
        existing = field.metadata or {}
        merged: dict[bytes, bytes] = dict(existing)
        merged[b"bqemu.duckdb_type"] = duckdb_type.encode("utf-8")
        fields.append(field.with_metadata(merged))
        changed = True
    if not changed:
        return table
    return table.cast(pa.schema(fields, metadata=table.schema.metadata))


__all__ = ["CATALOG_SCHEMA", "SNAPSHOTS_SCHEMA", "DuckDBEngine"]
