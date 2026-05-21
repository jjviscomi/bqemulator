"""Unit tests for the G1 Avro/ORC format-extension boot contract.

Mirrors :mod:`tests.unit.storage.test_engine_spatial` for the
``avro`` extension load path. The difference vs. spatial: format
extensions are *best-effort* — a failed install/load must not crash
engine startup (offline / air-gapped builds keep all non-Avro
functionality working).
"""

from __future__ import annotations

from typing import Any

import pytest

from bqemulator.config import PersistenceMode, Settings
from bqemulator.storage.engine import DuckDBEngine

pytestmark = pytest.mark.unit


class _FakeConnection:
    """A DuckDB-like connection that records executed SQL.

    Optionally raises on a SQL substring so we can simulate the
    Avro-install-fails path without taking the spatial path with it.
    """

    def __init__(self, *, raise_on: str | None = None) -> None:
        self._raise_on = raise_on
        self.executed: list[str] = []
        self.closed = False

    def execute(self, sql: str, *_args: Any) -> _FakeConnection:
        self.executed.append(sql)
        if self._raise_on and self._raise_on in sql:
            raise RuntimeError(f"simulated failure: {sql!r}")
        return self

    def create_function(self, *_a: Any, **_kw: Any) -> None:
        """No-op: builtin UDF registration is irrelevant to this test."""

    def close(self) -> None:
        self.closed = True


def _settings(enable: bool) -> Settings:
    return Settings(
        persistence_mode=PersistenceMode.EPHEMERAL,
        rest_port=0,
        grpc_port=0,
        enable_format_extensions=enable,
    )


@pytest.mark.asyncio
class TestFormatExtensionsBoot:
    """``DuckDBEngine.start`` must wire (or skip) ``INSTALL avro`` per the flag."""

    async def test_flag_on_triggers_avro_install_and_load(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = _FakeConnection()
        monkeypatch.setattr("duckdb.connect", lambda *_a, **_kw: fake)

        engine = DuckDBEngine(_settings(enable=True))
        await engine.start()
        try:
            installs = [s for s in fake.executed if "INSTALL avro" in s]
            loads = [s for s in fake.executed if "LOAD avro" in s]
            assert len(installs) == 1, fake.executed
            assert len(loads) == 1, fake.executed
        finally:
            await engine.stop()

    async def test_flag_off_skips_avro_install(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = _FakeConnection()
        monkeypatch.setattr("duckdb.connect", lambda *_a, **_kw: fake)

        engine = DuckDBEngine(_settings(enable=False))
        await engine.start()
        try:
            assert all("avro" not in s.lower() for s in fake.executed), fake.executed
        finally:
            await engine.stop()

    async def test_avro_install_failure_is_best_effort(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Boot must succeed even if ``INSTALL avro`` raises (no network).

        Spatial failure is fatal because GEOGRAPHY needs it; Avro
        failure is recoverable — every other format keeps working and
        only AVRO load/extract degrades.
        """
        fake = _FakeConnection(raise_on="INSTALL avro")
        monkeypatch.setattr("duckdb.connect", lambda *_a, **_kw: fake)

        engine = DuckDBEngine(_settings(enable=True))
        # Must NOT raise — best-effort load.
        await engine.start()
        try:
            assert engine.connection is fake
        finally:
            await engine.stop()

    async def test_avro_load_failure_is_best_effort(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = _FakeConnection(raise_on="LOAD avro")
        monkeypatch.setattr("duckdb.connect", lambda *_a, **_kw: fake)

        engine = DuckDBEngine(_settings(enable=True))
        await engine.start()
        try:
            assert engine.connection is fake
        finally:
            await engine.stop()
