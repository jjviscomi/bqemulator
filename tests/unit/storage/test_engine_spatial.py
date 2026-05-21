"""Deterministic unit test for the spatial-extension load contract.

Closes Phase 10 production-readiness audit gap #10 (spatial extension
offline) at the unit-test tier. The chaos storage tier exercises the
same surface against a subprocess with ``DUCKDB_EXTENSION_DIRECTORY``
pointed at a non-existent path; this unit test verifies the engine's
own behaviour without a subprocess so the error message contract stays
locked in even when the spatial extension is installable.

Strategy:
   Monkeypatch ``duckdb.connect`` so the returned connection raises on
   ``INSTALL spatial`` / ``LOAD spatial``. :meth:`DuckDBEngine.start`
   must convert the underlying exception into an
   :class:`InternalError` whose message names the failed operation,
   tells the operator how to fix it, and chains the original cause.

The chaos counterpart runs the same scenario against a real subprocess
to assert the operator-facing failure path is end-to-end clean.
"""

from __future__ import annotations

from typing import Any

import pytest

from bqemulator.config import Settings
from bqemulator.domain.errors import InternalError
from bqemulator.storage.engine import DuckDBEngine

pytestmark = pytest.mark.unit


class _FakeConnection:
    """A DuckDB-like connection whose spatial install raises."""

    def __init__(self, *, raise_on: str = "INSTALL spatial") -> None:
        self._raise_on = raise_on
        self.executed: list[str] = []
        self.closed = False

    def execute(self, sql: str, *_args: Any) -> _FakeConnection:
        self.executed.append(sql)
        if self._raise_on in sql:
            msg = f"spatial extension unavailable: {sql!r}"
            raise RuntimeError(msg)
        return self

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
class TestSpatialExtensionFailure:
    """``DuckDBEngine.start`` must surface a clean error if spatial can't load."""

    async def test_install_spatial_failure_raises_internal_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ephemeral_settings: Settings,
    ) -> None:
        fake = _FakeConnection(raise_on="INSTALL spatial")
        monkeypatch.setattr(
            "duckdb.connect",
            lambda *_a, **_kw: fake,
        )

        engine = DuckDBEngine(ephemeral_settings)
        with pytest.raises(InternalError) as excinfo:
            await engine.start()

        message = str(excinfo.value)
        # Operator-facing message must name the extension and the
        # actionable remediation.
        assert "spatial" in message
        assert "INSTALL/LOAD spatial" in message
        # And the original underlying exception is chained.
        assert isinstance(excinfo.value.__cause__, RuntimeError)
        # The startup did get as far as setting the catalog schema before
        # spatial failed — confirms we run pragmas / schemas first so the
        # spatial failure doesn't leave the catalog half-initialised.
        assert any("SET TimeZone" in stmt for stmt in fake.executed)

    async def test_load_spatial_failure_raises_internal_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ephemeral_settings: Settings,
    ) -> None:
        # ``INSTALL spatial`` succeeds but ``LOAD spatial`` fails — different
        # failure mode (e.g. extension downloaded but binary incompatible).
        fake = _FakeConnection(raise_on="LOAD spatial")
        monkeypatch.setattr(
            "duckdb.connect",
            lambda *_a, **_kw: fake,
        )

        engine = DuckDBEngine(ephemeral_settings)
        with pytest.raises(InternalError) as excinfo:
            await engine.start()

        assert "spatial" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, RuntimeError)
        # INSTALL was attempted and succeeded; LOAD was attempted and failed.
        installs = [stmt for stmt in fake.executed if "INSTALL spatial" in stmt]
        loads = [stmt for stmt in fake.executed if "LOAD spatial" in stmt]
        assert len(installs) == 1
        assert len(loads) == 1

    async def test_error_message_documents_remediation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ephemeral_settings: Settings,
    ) -> None:
        """The error must tell an operator what to do next.

        Phase 9 deliberately fails fast on spatial-extension load so the
        operator knows GEOGRAPHY queries cannot work. The message must
        give them the next step: check network access / pre-bundle the
        extension. We assert the documented remediation tokens are
        present so a regression doesn't silently drop the guidance.
        """
        monkeypatch.setattr(
            "duckdb.connect",
            lambda *_a, **_kw: _FakeConnection(raise_on="INSTALL spatial"),
        )

        engine = DuckDBEngine(ephemeral_settings)
        with pytest.raises(InternalError) as excinfo:
            await engine.start()

        msg = str(excinfo.value)
        for required in ("spatial", "GEOGRAPHY", "network", "extension"):
            assert required in msg, f"missing remediation token: {required!r}"
