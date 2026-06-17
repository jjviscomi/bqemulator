"""Unit tests for the threaded in-process emulator runner."""

from __future__ import annotations

import pytest

from bqemulator.config import PersistenceMode, Settings
from bqemulator.testing._thread_runner import ThreadedEmulator

pytestmark = pytest.mark.unit


def _ephemeral_settings() -> Settings:
    return Settings(
        persistence_mode=PersistenceMode.EPHEMERAL,
        rest_host="127.0.0.1",
        rest_port=0,
        grpc_host="127.0.0.1",
        grpc_port=0,
    )


class _BoomServer:
    """Stand-in server whose startup fails immediately."""

    async def start(self) -> None:
        raise RuntimeError("boom")

    async def stop(self) -> None:  # pragma: no cover - unreachable on the error path
        return


def test_failed_start_surfaces_error_then_stop_reclaims_thread() -> None:
    """A failed startup surfaces the real error, and ``stop`` stays safe.

    Regression guard for the abort-on-failed-start path: ``stop`` must not
    raise when the background loop is already closed (the startup-error path
    closes it), and it must join the background thread rather than leak it into
    interpreter shutdown (which aborts with "terminate called without an active
    exception"). Mirrors the ``bqemu_server`` fixture calling ``stop`` from its
    ``finally`` even when ``start`` raised.
    """
    runner = ThreadedEmulator(_ephemeral_settings())
    runner.server = _BoomServer()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="boom"):
        runner.start()

    # Safe to call after a failed start: the loop is already closed, so this
    # skips loop work and just joins the (already-finished) background thread.
    runner.stop()
    assert runner._thread is not None
    assert not runner._thread.is_alive()

    # ``stop`` is idempotent: a second call is a no-op and must not raise.
    runner.stop()
    assert not runner._thread.is_alive()
