"""Composition root — wires every subsystem into a running server.

This module is the ONLY place that constructs top-level objects. Every
other subsystem takes its collaborators via constructor injection.

Public entry points:

* :func:`run_forever` — blocking, used by the CLI's ``start`` command.
* :class:`EmulatorServer` — programmatic, used by the pytest fixture and
  for embedding into other Python processes.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from types import FrameType

import uvicorn

from bqemulator import __version__
from bqemulator.api.app import create_app
from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.repository import CatalogRepository
from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.clock import Clock, SystemClock
from bqemulator.domain.errors import InternalError
from bqemulator.domain.events import EventBus
from bqemulator.grpc_api.server import build_grpc_server
from bqemulator.observability.logging_ import configure_logging, get_logger
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.observability.tracing import configure_tracing
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.streaming.write_stream import WriteStreamManager
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.materialized_views import (
    clear_subscriptions_for_context,
    hydrate_subscriptions,
)
from bqemulator.versioning.snapshots import SnapshotManager

_log = get_logger(__name__)


class EmulatorServer:
    """Programmatic lifecycle for the emulator.

    Example::

        server = EmulatorServer(Settings())
        await server.start()
        # ... use the emulator ...
        await server.stop()

    Or synchronously via :meth:`run_forever`, used by the CLI.
    """

    def __init__(self, settings: Settings, *, clock: Clock | None = None) -> None:
        self._settings = settings
        self._clock: Clock = clock or SystemClock()
        self._engine = DuckDBEngine(settings)
        self._metrics = MetricsRegistry()
        self._events = EventBus()
        self._catalog: CatalogRepository
        if settings.persistence_mode is PersistenceMode.EPHEMERAL:
            # Ephemeral mode: memory catalog is sufficient and avoids DuckDB
            # catalog startup cost in tight CI loops. The engine is
            # threaded through so storage introspection (used by the
            # wildcard expander) still sees DDL-created tables — REST
            # CRUD doesn't see them but the catalog cache always
            # mirrors REST, so the only "missing" surface is SQL DDL.
            self._catalog = MemoryCatalogRepository(self._engine)
        else:
            self._catalog = DuckDBCatalogRepository(self._engine)

        self._context: AppContext | None = None
        self._fastapi_server: uvicorn.Server | None = None
        self._fastapi_task: asyncio.Task[None] | None = None
        self._grpc_server: object | None = None  # grpc.aio.Server, typed loosely
        self._grpc_port: int | None = None
        self._rest_port: int | None = None
        self._gc_task: asyncio.Task[None] | None = None

    # -- Properties (post-start) -------------------------------------------

    @property
    def rest_port(self) -> int:
        """The actual REST port (useful when the user passed 0)."""
        if self._rest_port is None:
            raise RuntimeError("EmulatorServer not started")
        return self._rest_port

    @property
    def grpc_port(self) -> int:
        """The actual gRPC port (useful when the user passed 0)."""
        if self._grpc_port is None:
            raise RuntimeError("EmulatorServer not started")
        return self._grpc_port

    @property
    def rest_url(self) -> str:
        """Base URL for the REST endpoint."""
        return f"http://{self._settings.rest_host}:{self.rest_port}"

    @property
    def grpc_endpoint(self) -> str:
        """``host:port`` form of the gRPC endpoint."""
        return f"{self._settings.grpc_host}:{self.grpc_port}"

    # -- Lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start all subsystems. Returns once the servers are accepting traffic."""
        configure_logging(level=self._settings.log_level, fmt=self._settings.log_format)
        configure_tracing(self._settings)
        _log.info(
            "bqemulator.start",
            version=__version__,
            persistence_mode=self._settings.persistence_mode.value,
        )

        await self._engine.start()

        # For the DuckDB-backed catalog, run migrations up-front so readiness
        # probes succeed immediately.
        if isinstance(self._catalog, DuckDBCatalogRepository):
            self._catalog.ensure_ready()

        udf_registry = UDFRegistry(self._settings)
        snapshot_manager = SnapshotManager(
            engine=self._engine,
            catalog=self._catalog,
            clock=self._clock,
            events=self._events,
            retention_days=self._settings.time_travel_retention_days,
        )
        row_access_manager = RowAccessPolicyManager(
            catalog=self._catalog,
            clock=self._clock,
        )
        # Storage Write API stream registry shared between the gRPC servicer
        # (which owns lifecycle) and the admin /admin/streams endpoint
        # (which reads it for diagnostics). Phase 10 lifts construction out
        # of the servicer so both consumers see the same instance; the
        # servicer wires the metric-cleanup callback after AppContext is
        # built (see ``BigQueryWriteHandler.__init__``).
        write_stream_manager = WriteStreamManager()
        # G2 — upload host (resumable / multipart). The manager owns the
        # in-memory session map and the temp staging directory under
        # ``Settings.upload_staging_dir`` (or the system tempdir when
        # unset). See ADR 0029.
        from bqemulator.jobs.upload_session_manager import UploadSessionManager

        upload_session_manager = UploadSessionManager(
            staging_dir=self._settings.upload_staging_dir,
            max_bytes=self._settings.upload_max_bytes,
            ttl_seconds=self._settings.upload_session_ttl_seconds,
            clock=self._clock,
        )
        self._context = AppContext(
            settings=self._settings,
            clock=self._clock,
            engine=self._engine,
            catalog=self._catalog,
            metrics=self._metrics,
            events=self._events,
            udf_registry=udf_registry,
            snapshots=snapshot_manager,
            row_access=row_access_manager,
            write_streams=write_stream_manager,
            upload_sessions=upload_session_manager,
        )
        # Hydrate routines from the catalog into DuckDB. Best-effort —
        # a broken routine in the catalog should not block startup.
        udf_registry.hydrate(self._catalog, self._engine)
        # Rebuild materialized-view event subscriptions from the catalog.
        hydrate_subscriptions(self._context)
        # Kick off snapshot GC in the background.
        gc_interval = max(
            60.0,
            self._settings.time_travel_retention_days * 86400.0 / 48.0,
        )
        self._gc_task = asyncio.create_task(
            snapshot_manager.run_gc_loop(interval_seconds=gc_interval),
            name="bqemulator.snapshot-gc",
        )

        # REST (uvicorn).
        # Lifespan events are disabled because we manage all resources
        # (DuckDB, gRPC) from the composition root, not from FastAPI's
        # lifespan. This avoids an async deadlock when embedding uvicorn
        # in an already-running event loop.
        app = create_app(self._context)
        uvicorn_config = uvicorn.Config(
            app,
            host=self._settings.rest_host,
            port=self._settings.rest_port,
            log_config=None,  # structlog handles logging
            access_log=False,
            lifespan="off",
            loop="none",  # reuse the caller's event loop
        )
        self._fastapi_server = uvicorn.Server(uvicorn_config)
        # uvicorn's default signal handlers conflict with our own. We use
        # setattr to bypass mypy's attribute-defined check since uvicorn's
        # type stubs don't declare install_signal_handlers (though it is
        # a real instance method on uvicorn.Server).
        setattr(self._fastapi_server, "install_signal_handlers", lambda: None)  # noqa: B010

        self._fastapi_task = asyncio.create_task(
            self._fastapi_server.serve(),
            name="bqemulator.rest",
        )
        # Wait for uvicorn to bind the socket, then pull the actual port.
        await self._wait_for_uvicorn_started(timeout=10.0)
        servers = getattr(self._fastapi_server, "servers", ())
        if servers and servers[0].sockets:
            self._rest_port = servers[0].sockets[0].getsockname()[1]
        else:  # pragma: no cover
            self._rest_port = self._settings.rest_port
        _log.info("rest.listen", host=self._settings.rest_host, port=self._rest_port)

        # gRPC
        grpc_server, grpc_port = build_grpc_server(self._context)
        await grpc_server.start()
        self._grpc_server = grpc_server
        self._grpc_port = grpc_port

    async def _wait_for_uvicorn_started(self, *, timeout: float) -> None:  # noqa: ASYNC109 — deliberate timeout parameter
        """Poll ``uvicorn.Server.started`` until True or raise on timeout."""
        if self._fastapi_server is None:
            raise InternalError("FastAPI server not initialized")
        deadline = asyncio.get_event_loop().time() + timeout
        while not self._fastapi_server.started:
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("uvicorn did not signal 'started' in time")
            await asyncio.sleep(0.02)

    async def stop(self) -> None:
        """Stop all subsystems. Idempotent."""
        _log.info("bqemulator.stop")

        # Snapshot GC
        if self._gc_task is not None:
            self._gc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._gc_task
            self._gc_task = None

        # MV subscriptions — unwire from the event bus.
        if self._context is not None:
            clear_subscriptions_for_context(self._context)

        # gRPC
        if self._grpc_server is not None:
            with contextlib.suppress(Exception):
                await self._grpc_server.stop(grace=2.0)  # type: ignore[attr-defined]
            self._grpc_server = None

        # REST
        if self._fastapi_server is not None:
            self._fastapi_server.should_exit = True
        if self._fastapi_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._fastapi_task
            self._fastapi_task = None
        self._fastapi_server = None

        # Storage
        await self._engine.stop()

    # -- Synchronous entry --------------------------------------------------

    def run_forever(self) -> None:
        """Start the server and block on SIGINT/SIGTERM."""
        asyncio.run(self._run_forever_async())

    async def _run_forever_async(self) -> None:
        await self.start()
        stop_event = asyncio.Event()

        def _on_signal(_signum: int, _frame: FrameType | None) -> None:
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:  # pragma: no cover — Windows
                signal.signal(sig, _on_signal)

        try:
            await stop_event.wait()
        finally:
            await self.stop()


def run_forever(settings: Settings) -> None:
    """Module-level entry point used by the CLI."""
    EmulatorServer(settings).run_forever()


__all__ = ["EmulatorServer", "run_forever"]
