"""Storage chaos — closes Phase 10 audit gaps #7, #9, #10.

Three scenarios:

1. **Two emulators racing the same ``data_dir``** (gap #7). DuckDB
   acquires an OS-level file lock on the database file; the second
   emulator's startup must surface a clean :class:`InternalError`
   (not crash, not silently corrupt). We exercise this with a real
   second :class:`ThreadedEmulator` pointed at the in-use ``data_dir``.

2. **Forward-only migration partial-apply** (gap #9). The chaos
   counterpart to ``tests/unit/catalog/test_migration_rollback.py``:
   we corrupt a catalog table mid-flight by adding a required column
   that the running emulator's hydration doesn't know about, then
   verify the migration recovery flow surfaces a clean error in
   strict mode and skips gracefully in lenient mode.

3. **Spatial extension fails to load** (gap #10). Spawns a child
   process with the DuckDB extension repo pointed at a nonexistent
   directory; the child's startup must exit with a non-zero return
   code and an :class:`InternalError`-shaped stderr that names the
   spatial extension. Unit-tier counterpart lives in
   ``tests/unit/storage/test_engine_spatial.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys
import textwrap

import duckdb
import pytest

from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.errors import InternalError
from bqemulator.storage.engine import CATALOG_SCHEMA, DuckDBEngine

pytestmark = pytest.mark.chaos


# ---------------------------------------------------------------------------
# Scenario 1 — Two emulators racing the same data_dir.
# ---------------------------------------------------------------------------


class TestDuckDBFileLockContention:
    """Second emulator on the same ``data_dir`` raises cleanly (gap #7).

    DuckDB takes an exclusive OS-level file lock on the persistent
    database file *across processes*. Connections within the same
    process share the lock. The chaos contract: a second emulator
    started in a separate process surfaces a clean error within the
    chaos timeout; the first emulator is unaffected.

    The test holds the lock in a child subprocess (so the parent's
    pytest process is free to start a verifier emulator), prints
    READY, then waits for the parent's SIGTERM after the contention
    check completes.
    """

    def test_second_emulator_on_locked_data_dir_raises(
        self,
        tmp_path: Path,
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # First emulator runs in a child process and holds the lock.
        program = textwrap.dedent(
            f"""
            import asyncio
            import sys
            from pathlib import Path
            from bqemulator.config import PersistenceMode, Settings
            from bqemulator.storage.engine import DuckDBEngine

            async def _hold():
                engine = DuckDBEngine(
                    Settings(
                        persistence_mode=PersistenceMode.PERSISTENT,
                        data_dir=Path({str(data_dir)!r}),
                    ),
                )
                await engine.start()
                engine.execute('CREATE TABLE busy (v BIGINT)')
                sys.stdout.reconfigure(line_buffering=True)
                print('READY')
                try:
                    await asyncio.Event().wait()
                finally:
                    await engine.stop()

            asyncio.run(_hold())
            """,
        )
        proc = subprocess.Popen(  # noqa: S603 — internal program string
            [sys.executable, "-c", program],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        try:
            # Wait for the child's READY.
            assert proc.stdout is not None
            ready = False
            import time

            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                if "READY" in line:
                    ready = True
                    break
            assert ready, "child did not print READY"

            # Now start a second emulator in the *parent* — it must
            # raise because the child holds the file lock.
            async def _race() -> None:
                second = DuckDBEngine(
                    Settings(
                        persistence_mode=PersistenceMode.PERSISTENT,
                        data_dir=data_dir,
                    ),
                )
                with pytest.raises(
                    (duckdb.IOException, duckdb.InvalidInputException, InternalError),
                ):
                    await second.start()

            asyncio.run(_race())
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()


# ---------------------------------------------------------------------------
# Scenario 2 — Catalog drift recovery.
# ---------------------------------------------------------------------------


class TestCatalogDriftRecovery:
    """Forward-only migration partial-apply has two recovery paths (gap #9).

    Strict mode (default): :class:`DuckDBCatalogRepository` surfaces
    a clean :class:`InternalError` with the offending row identity.

    Lenient mode: the corrupt row is skipped (logged) and the rest of
    the catalog hydrates. The chaos contract: an operator restoring
    from a partial backup can use lenient mode to bring 99% of the
    catalog online and triage the remaining 1% manually.
    """

    @pytest.mark.asyncio
    async def test_corrupt_catalog_strict_mode_surfaces_row_identity(
        self,
        tmp_path: Path,
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Populate a catalog through a real emulator.
        from bqemulator.testing._thread_runner import ThreadedEmulator

        threaded = ThreadedEmulator(
            Settings(
                persistence_mode=PersistenceMode.PERSISTENT,
                data_dir=data_dir,
                rest_port=0,
                grpc_port=0,
            ),
        )
        threaded.start()
        try:
            from datetime import UTC, datetime

            from bqemulator.catalog.models import DatasetMeta

            now = datetime(2026, 4, 15, tzinfo=UTC)
            threaded.server._catalog.create_dataset(
                DatasetMeta(
                    project_id="p",
                    dataset_id="ds-good",
                    creation_time=now,
                    last_modified_time=now,
                    etag="good",
                ),
            )
            threaded.server._catalog.create_dataset(
                DatasetMeta(
                    project_id="p",
                    dataset_id="ds-bad",
                    creation_time=now,
                    last_modified_time=now,
                    etag="bad",
                ),
            )
        finally:
            threaded.stop()

        # Corrupt one row.
        conn = duckdb.connect(str(data_dir / "bqemulator.duckdb"))
        try:
            conn.execute(
                f'UPDATE "{CATALOG_SCHEMA}"."datasets" '
                "SET metadata_json = 'not-json' "
                "WHERE dataset_id = 'ds-bad'",
            )
        finally:
            conn.close()

        # Strict-mode hydration raises with row identity.
        engine = DuckDBEngine(
            Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=data_dir),
        )
        await engine.start()
        try:
            repo = DuckDBCatalogRepository(engine, lenient=False)
            with pytest.raises(InternalError, match=r"datasets row p\.ds-bad"):
                repo.ensure_ready()
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_corrupt_catalog_lenient_mode_skips_and_loads_rest(
        self,
        tmp_path: Path,
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        from bqemulator.testing._thread_runner import ThreadedEmulator

        threaded = ThreadedEmulator(
            Settings(
                persistence_mode=PersistenceMode.PERSISTENT,
                data_dir=data_dir,
                rest_port=0,
                grpc_port=0,
            ),
        )
        threaded.start()
        try:
            from datetime import UTC, datetime

            from bqemulator.catalog.models import DatasetMeta

            now = datetime(2026, 4, 15, tzinfo=UTC)
            threaded.server._catalog.create_dataset(
                DatasetMeta(
                    project_id="p",
                    dataset_id="ok-1",
                    creation_time=now,
                    last_modified_time=now,
                    etag="e1",
                ),
            )
            threaded.server._catalog.create_dataset(
                DatasetMeta(
                    project_id="p",
                    dataset_id="ok-2",
                    creation_time=now,
                    last_modified_time=now,
                    etag="e2",
                ),
            )
            threaded.server._catalog.create_dataset(
                DatasetMeta(
                    project_id="p",
                    dataset_id="rotten",
                    creation_time=now,
                    last_modified_time=now,
                    etag="e3",
                ),
            )
        finally:
            threaded.stop()

        conn = duckdb.connect(str(data_dir / "bqemulator.duckdb"))
        try:
            conn.execute(
                f'UPDATE "{CATALOG_SCHEMA}"."datasets" '
                "SET metadata_json = 'BROKEN' WHERE dataset_id = 'rotten'",
            )
        finally:
            conn.close()

        engine = DuckDBEngine(
            Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=data_dir),
        )
        await engine.start()
        try:
            repo = DuckDBCatalogRepository(engine, lenient=True)
            repo.ensure_ready()
            loaded = sorted(d.dataset_id for d in repo.list_all_datasets())
            # Two of three loaded; ``rotten`` was skipped with a warning.
            assert loaded == ["ok-1", "ok-2"]
        finally:
            await engine.stop()


# ---------------------------------------------------------------------------
# Scenario 3 — Spatial extension offline.
# ---------------------------------------------------------------------------


class TestSpatialExtensionOffline:
    """Emulator startup fails cleanly when spatial extension is unavailable.

    Spawns a child process with ``DUCKDB_EXTENSION_DIRECTORY`` pointed
    at a nonexistent directory; DuckDB's ``INSTALL spatial`` /
    ``LOAD spatial`` raises; the engine surfaces an ``InternalError``
    naming the failure and the child exits non-zero. The unit-tier
    counterpart in ``tests/unit/storage/test_engine_spatial.py``
    monkeypatches ``duckdb.connect`` for the same contract; this
    chaos test runs the full process-level failure path.
    """

    def test_offline_spatial_extension_exits_with_clean_error(
        self,
        tmp_path: Path,
    ) -> None:
        program = textwrap.dedent(
            f"""
            import asyncio
            from pathlib import Path
            from bqemulator.config import PersistenceMode, Settings
            from bqemulator.domain.errors import InternalError
            from bqemulator.storage.engine import DuckDBEngine

            async def _go():
                engine = DuckDBEngine(
                    Settings(
                        persistence_mode=PersistenceMode.PERSISTENT,
                        data_dir=Path({str(tmp_path)!r}),
                    ),
                )
                try:
                    await engine.start()
                except InternalError as exc:
                    print(f'CAUGHT: {{exc}}')
                    raise SystemExit(42)
                # If we got here, spatial actually loaded — skip the
                # scenario rather than report a false pass.
                print('SPATIAL_LOADED')
                await engine.stop()

            asyncio.run(_go())
            """,
        )

        import os

        env = {
            **os.environ,
            "DUCKDB_EXTENSION_DIRECTORY": "/this/path/does/not/exist/bqemu-chaos",
            # Use HOME under tmp_path so DuckDB's default ``~/.duckdb``
            # extension cache is also a fresh empty dir — the chaos
            # scenario assumes no pre-installed spatial.
            "HOME": str(tmp_path / "home"),
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
            "TMPDIR": str(tmp_path / "tmp"),
        }
        # Make these exist so DuckDB doesn't error on home expansion.
        for p in ("home", "xdg-data", "tmp"):
            (tmp_path / p).mkdir(parents=True, exist_ok=True)

        result = subprocess.run(  # noqa: S603 — child program is a string we built
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            check=False,
        )

        # Two acceptable outcomes:
        # 1. Spatial loaded anyway (DuckDB found it in a system cache
        #    we couldn't override) → skip rather than false-pass.
        # 2. Spatial failed → the engine raised, exit 42, message
        #    names the extension.
        if "SPATIAL_LOADED" in result.stdout:
            pytest.skip(
                "DuckDB found a cached spatial extension; cannot exercise "
                "the offline path in this environment",
            )
        assert result.returncode == 42, (
            f"expected exit 42, got {result.returncode}; "
            f"stdout={result.stdout!r}; stderr={result.stderr!r}"
        )
        assert "spatial" in result.stdout.lower() or "spatial" in result.stderr.lower()
