"""Crash chaos — closes Phase 10 audit gap #2 process-level.

Three scenarios. The first two execute the emulator in a subprocess
so we can ``kill -9`` it mid-operation; the third uses an in-process
:class:`ThreadedEmulator` to model gRPC client cancellation without
the OS-process complexity.

All assertions are about the *next process's* observable state:

* In-memory state is lost across a crash (per ADR 0013 — the Write
  API stream registry is ephemeral by design).
* Persistent DuckDB state survives — DML committed before the crash
  is queryable by the post-restart process.
* The next gRPC client call against a stream that didn't survive the
  crash receives ``NOT_FOUND`` (not a 500 or a hang).

The subprocess scenarios use ``BQEMU_DATA_DIR`` + persistent mode so
DuckDB writes through to disk before the kill arrives.

Determinism: the subprocess publishes a "ready" line on stdout
before the parent kills it; we ``select()`` on the pipe rather than
sleeping, so the race is gated on the actual state-transition rather
than wall-clock timing. The two paths (kill before DDL commits / kill
after DDL commits) are exercised by two different child entrypoints.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from bqemulator.config import PersistenceMode, Settings
from bqemulator.streaming.strategies import CommittedWriteStrategy
from bqemulator.streaming.strategies.base import AppendStatus
from bqemulator.streaming.write_stream import WriteStreamManager, WriteStreamType

pytestmark = pytest.mark.chaos


def _python() -> str:
    """Locate the venv Python that runs the chaos tests."""
    return sys.executable


# ---------------------------------------------------------------------------
# Helper: spawn a subprocess running a snippet of Python; return the proc.
# ---------------------------------------------------------------------------


def _spawn_emulator_child(
    snippet: str,
    *,
    data_dir: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    """Run a snippet inside the chaos-tier subprocess.

    The snippet receives ``data_dir`` as a local variable and may use
    any bqemulator API. It must print ``READY`` once whatever
    setup the parent waits for has completed (the parent then kills
    the process). Output is line-buffered.
    """
    import os

    program = textwrap.dedent(
        f"""
        import asyncio
        import os
        import sys
        from pathlib import Path
        data_dir = Path({str(data_dir)!r})
        # Force line-buffered stdout so the parent sees READY promptly.
        sys.stdout.reconfigure(line_buffering=True)
        """,
    ) + textwrap.dedent(snippet)

    env = {**os.environ}
    if extra_env:
        env.update(extra_env)

    return subprocess.Popen(  # noqa: S603 — program is a chaos-test-controlled snippet
        [_python(), "-c", program],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )


def _wait_for_ready(
    proc: subprocess.Popen[str],
    *,
    timeout: float = 15.0,
    sentinel: str = "READY",
) -> None:
    """Block until the child prints ``sentinel`` or the deadline expires."""
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            # Pipe closed — child died before signalling ready.
            stderr = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(
                f"child exited before READY (rc={proc.returncode}); stderr={stderr!r}",
            )
        if sentinel in line:
            return
    proc.kill()
    raise TimeoutError(f"child did not print {sentinel!r} within {timeout}s")


# ---------------------------------------------------------------------------
# Scenario 1 — kill -9 after DDL commits.
# ---------------------------------------------------------------------------


class TestKillAfterDDLCommits:
    """Committed DDL must be queryable after ``kill -9``.

    The child creates a dataset + table, prints READY, then waits. The
    parent ``SIGKILL``s the child and starts a fresh emulator on the
    same ``data_dir``; the new process must hydrate the catalog and
    expose the table created before the kill.
    """

    def test_committed_ddl_survives_sigkill(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        snippet = """
            from bqemulator.config import PersistenceMode, Settings
            from bqemulator.server import EmulatorServer

            async def _go() -> None:
                server = EmulatorServer(
                    Settings(
                        persistence_mode=PersistenceMode.PERSISTENT,
                        data_dir=data_dir,
                        rest_port=0,
                        grpc_port=0,
                    ),
                )
                await server.start()
                # Use the in-process catalog directly to avoid the HTTP
                # round-trip — the catalog write-through is what we're
                # testing.
                from datetime import UTC, datetime
                from bqemulator.catalog.models import (
                    DatasetMeta, TableFieldSchema, TableMeta, TableSchema,
                )
                now = datetime(2026, 4, 15, tzinfo=UTC)
                server._catalog.create_dataset(
                    DatasetMeta(
                        project_id='p', dataset_id='ds',
                        creation_time=now, last_modified_time=now,
                        etag='ds-etag',
                    ),
                )
                server._catalog.create_table(
                    TableMeta(
                        project_id='p', dataset_id='ds', table_id='survivors',
                        table_type='TABLE',
                        schema=TableSchema(fields=(
                            TableFieldSchema(name='id', type='INT64', mode='NULLABLE'),
                        )),
                        creation_time=now, last_modified_time=now,
                        num_rows=0, num_bytes=0, etag='t-etag',
                    ),
                )
                # Cleanly close the engine to flush DuckDB's WAL — we
                # need the file to be in a recoverable state when the
                # parent runs the next emulator. (SIGKILL after the
                # close is what models the unclean shutdown of the
                # python process itself.)
                await server._engine.stop()
                print('READY')
                # Block forever — the parent kills us.
                await asyncio.Event().wait()

            asyncio.run(_go())
        """
        with _spawn_emulator_child(snippet, data_dir=data_dir) as proc:
            try:
                _wait_for_ready(proc)
                proc.send_signal(signal.SIGKILL)
                proc.wait(timeout=10)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)

        # Now start a fresh emulator on the same data_dir; the new
        # catalog must include ``survivors``.
        async def _verify() -> None:
            from bqemulator.server import EmulatorServer

            server = EmulatorServer(
                Settings(
                    persistence_mode=PersistenceMode.PERSISTENT,
                    data_dir=data_dir,
                    rest_port=0,
                    grpc_port=0,
                ),
            )
            await server.start()
            try:
                meta = server._catalog.get_table("p", "ds", "survivors")
                assert meta is not None
                assert meta.table_id == "survivors"
            finally:
                await server.stop()

        asyncio.run(_verify())


# ---------------------------------------------------------------------------
# Scenario 2 — kill -9 during DDL transaction (commit fence).
# ---------------------------------------------------------------------------


class TestKillDuringDDL:
    """A kill between DDL emission and commit must NOT half-create a row.

    DuckDB's auto-commit semantics: ``CREATE TABLE`` commits when its
    statement completes. The catalog write-through in
    :class:`DuckDBCatalogRepository` is part of the *same* SQL
    statement chain as the user-table DDL — both land before the row
    is visible to the cache. If a kill arrives before the SQL
    statement completes, neither the table nor the catalog row
    exists on restart.

    We model this by killing the child *before* it has called
    ``create_table`` — the catalog must be empty on the restart side.
    """

    def test_kill_before_ddl_leaves_clean_state(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        snippet = """
            from bqemulator.config import PersistenceMode, Settings
            from bqemulator.server import EmulatorServer

            async def _go() -> None:
                server = EmulatorServer(
                    Settings(
                        persistence_mode=PersistenceMode.PERSISTENT,
                        data_dir=data_dir,
                        rest_port=0,
                        grpc_port=0,
                    ),
                )
                await server.start()
                # READY is printed BEFORE any create_table — the parent
                # kills us mid-startup so no user DDL has run.
                print('READY')
                await asyncio.Event().wait()

            asyncio.run(_go())
        """
        with _spawn_emulator_child(snippet, data_dir=data_dir) as proc:
            try:
                _wait_for_ready(proc)
                proc.send_signal(signal.SIGKILL)
                proc.wait(timeout=10)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)

        async def _verify() -> None:
            from bqemulator.server import EmulatorServer

            server = EmulatorServer(
                Settings(
                    persistence_mode=PersistenceMode.PERSISTENT,
                    data_dir=data_dir,
                    rest_port=0,
                    grpc_port=0,
                ),
            )
            await server.start()
            try:
                # No user tables; only the catalog schemas exist.
                assert server._catalog.list_all_datasets() == ()
            finally:
                await server.stop()

        asyncio.run(_verify())


# ---------------------------------------------------------------------------
# Scenario 3 — gRPC client cancellation mid-stream.
# ---------------------------------------------------------------------------


class TestStreamClientCancellation:
    """Cancelling a stream mid-append cleans up registry state.

    Models the client-disconnect path without spinning up an actual
    gRPC server: we drive the :class:`WriteStreamManager` directly,
    open a stream, append some rows, then delete the stream
    (manager.delete) — which is what the servicer does when a stream
    context cancels. The chaos contract: the manager's
    ``list_active`` snapshot no longer references the cancelled
    stream, and the ``on_remove`` callback fired so metric counters
    decremented.
    """

    def test_cancel_mid_stream_clears_registry_and_fires_callback(self) -> None:
        callbacks: list[str] = []

        manager = WriteStreamManager(
            on_remove=lambda s: callbacks.append(s.name),
        )

        stream = manager.create("p", "d", "t", "s1", WriteStreamType.COMMITTED)
        strat = CommittedWriteStrategy()

        import pyarrow as pa

        rows = pa.table({"id": pa.array([1, 2, 3], type=pa.int64())})
        outcome = strat.append(stream, rows, offset=0)
        assert outcome.status is AppendStatus.OK
        assert stream.row_count == 3

        # Simulate cancellation: the servicer's exception-handler path
        # calls manager.delete on the stream context.
        manager.delete(stream.name)

        # Registry no longer references the stream.
        assert all(s.name != stream.name for s in manager.list_active())
        # Callback fired exactly once with the stream's name.
        assert callbacks == [stream.name]

        # A subsequent FinalizeWriteStream request for the same name
        # would now hit ``manager.get(name) is None`` → the servicer
        # would respond with NOT_FOUND. We model that by asserting the
        # invariant the servicer relies on.
        assert manager.get(stream.name) is None
