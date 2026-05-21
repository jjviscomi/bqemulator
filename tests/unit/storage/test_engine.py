"""Tests for the DuckDBEngine lifecycle and concurrency primitives."""

from __future__ import annotations

from pathlib import Path

import pytest

from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.errors import InternalError
from bqemulator.storage.engine import CATALOG_SCHEMA, DuckDBEngine

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
class TestLifecycle:
    async def test_start_opens_connection(self, ephemeral_settings: Settings) -> None:
        engine = DuckDBEngine(ephemeral_settings)
        await engine.start()
        try:
            assert engine.connection is not None
        finally:
            await engine.stop()

    async def test_stop_is_idempotent(self, ephemeral_settings: Settings) -> None:
        engine = DuckDBEngine(ephemeral_settings)
        await engine.start()
        await engine.stop()
        await engine.stop()  # no raise

    async def test_start_is_idempotent(self, ephemeral_settings: Settings) -> None:
        engine = DuckDBEngine(ephemeral_settings)
        await engine.start()
        await engine.start()  # no raise
        await engine.stop()

    async def test_connection_raises_before_start(self, ephemeral_settings: Settings) -> None:
        engine = DuckDBEngine(ephemeral_settings)
        with pytest.raises(InternalError):
            _ = engine.connection


@pytest.mark.asyncio
class TestExecute:
    async def test_select_one(self, ephemeral_settings: Settings) -> None:
        engine = DuckDBEngine(ephemeral_settings)
        await engine.start()
        try:
            result = engine.execute("SELECT 1 AS one").fetchone()
            assert result == (1,)
        finally:
            await engine.stop()

    async def test_fetch_arrow_returns_arrow_table(
        self,
        ephemeral_settings: Settings,
    ) -> None:
        import pyarrow as pa

        engine = DuckDBEngine(ephemeral_settings)
        await engine.start()
        try:
            table = engine.fetch_arrow("SELECT 1 AS a, 'hi' AS b")
            assert isinstance(table, pa.Table)
            assert table.column_names == ["a", "b"]
            assert table.num_rows == 1
        finally:
            await engine.stop()


@pytest.mark.asyncio
class TestSessionPragmas:
    async def test_timezone_is_utc(self, ephemeral_settings: Settings) -> None:
        engine = DuckDBEngine(ephemeral_settings)
        await engine.start()
        try:
            tz = engine.execute("SELECT current_setting('TimeZone')").fetchone()
            assert tz is not None
            assert tz[0] == "UTC"
        finally:
            await engine.stop()


@pytest.mark.asyncio
class TestCatalogSchemaSetup:
    async def test_catalog_schema_is_created(self, ephemeral_settings: Settings) -> None:
        engine = DuckDBEngine(ephemeral_settings)
        await engine.start()
        try:
            rows = engine.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ?",
                [CATALOG_SCHEMA],
            ).fetchall()
            assert len(rows) == 1
        finally:
            await engine.stop()


@pytest.mark.asyncio
class TestPersistence:
    async def test_persistent_writes_file(self, tmp_path: Path) -> None:
        settings = Settings(
            persistence_mode=PersistenceMode.PERSISTENT,
            data_dir=tmp_path,
        )
        engine = DuckDBEngine(settings)
        await engine.start()
        try:
            engine.execute(
                'CREATE TABLE "_bqemulator_catalog"."probe" (x INTEGER)',
            )
            engine.execute(
                'INSERT INTO "_bqemulator_catalog"."probe" VALUES (1)',
            )
        finally:
            await engine.stop()

        # Re-open and verify the data survived.
        engine2 = DuckDBEngine(settings)
        await engine2.start()
        try:
            rows = engine2.execute(
                'SELECT x FROM "_bqemulator_catalog"."probe"',
            ).fetchall()
            assert rows == [(1,)]
        finally:
            await engine2.stop()


@pytest.mark.asyncio
class TestWriteLock:
    async def test_write_lock_is_async_context_manager(
        self,
        ephemeral_settings: Settings,
    ) -> None:
        engine = DuckDBEngine(ephemeral_settings)
        await engine.start()
        try:
            async with engine.write_lock():
                engine.execute(
                    'CREATE TABLE "_bqemulator_catalog"."write_probe" (x INTEGER)',
                )
        finally:
            await engine.stop()


@pytest.mark.asyncio
class TestSpatialExtension:
    """Phase 9 requires the spatial extension at startup."""

    async def test_spatial_loaded_at_startup(
        self,
        ephemeral_settings: Settings,
    ) -> None:
        engine = DuckDBEngine(ephemeral_settings)
        await engine.start()
        try:
            # ``ST_Point`` is provided by the spatial extension; a
            # successful query proves the extension is loaded.
            row = engine.execute("SELECT ST_AsText(ST_Point(1, 2))").fetchone()
            assert row is not None
            assert "POINT" in row[0]
        finally:
            await engine.stop()

    async def test_fail_fast_on_spatial_unavailable(
        self,
        ephemeral_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from bqemulator.domain.errors import InternalError
        from bqemulator.storage import engine as engine_mod

        # Force the spatial install to fail and verify we raise an
        # InternalError with a clear message — not a confusing query-
        # time catalog error later on.
        engine = engine_mod.DuckDBEngine(ephemeral_settings)

        def _broken_load() -> None:
            raise InternalError("synthetic spatial-load failure")

        monkeypatch.setattr(engine, "_load_spatial", _broken_load)
        with pytest.raises(InternalError, match="synthetic spatial-load failure"):
            await engine.start()
