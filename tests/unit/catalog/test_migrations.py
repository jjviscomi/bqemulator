"""Tests for the catalog migration runner."""

from __future__ import annotations

import pytest

from bqemulator.catalog.migrations import run_migrations
from bqemulator.config import Settings
from bqemulator.storage.engine import CATALOG_SCHEMA, DuckDBEngine

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_migrations_apply_cleanly(ephemeral_settings: Settings) -> None:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    try:
        run_migrations(engine)
        version = engine.execute(
            f'SELECT MAX(version) FROM "{CATALOG_SCHEMA}"."_schema_version"',
        ).fetchone()
        assert version is not None
        assert version[0] is not None
        assert version[0] >= 1
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_migrations_are_idempotent(ephemeral_settings: Settings) -> None:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    try:
        run_migrations(engine)
        first_count = engine.execute(
            f'SELECT COUNT(*) FROM "{CATALOG_SCHEMA}"."_schema_version"',
        ).fetchone()
        run_migrations(engine)  # second run must be a no-op
        second_count = engine.execute(
            f'SELECT COUNT(*) FROM "{CATALOG_SCHEMA}"."_schema_version"',
        ).fetchone()
        assert first_count == second_count
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_expected_tables_exist(ephemeral_settings: Settings) -> None:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    try:
        run_migrations(engine)
        tables = {
            row[0]
            for row in engine.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
                [CATALOG_SCHEMA],
            ).fetchall()
        }
        assert {"datasets", "tables", "routines", "jobs", "_schema_version"} <= tables
    finally:
        await engine.stop()
