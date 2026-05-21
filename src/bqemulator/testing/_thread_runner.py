"""Run the emulator on a dedicated background thread.

Pytest's default event loop and a server's event loop cannot share a
thread without elaborate plumbing. This module provides a thin wrapper
that starts the emulator on its own thread with its own event loop, so
synchronous test code (and the Google Python client) can hit it without
async awareness.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.config import Settings


class ThreadedEmulator:
    """Runs an :class:`EmulatorServer` on a background thread + event loop."""

    def __init__(self, settings: Settings) -> None:
        from bqemulator.server import EmulatorServer

        self.server = EmulatorServer(settings)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._stopped = threading.Event()
        # Captured if ``server.start()`` raised on the background
        # thread; surfaced from the foreground ``start()`` call so test
        # code sees the actual error (not just a 30s timeout).
        self._startup_error: BaseException | None = None

    def start(self) -> None:
        """Start the server thread and block until listening.

        The startup path resolves to one of three outcomes:

        1. The server starts cleanly and ``_started`` fires — return.
        2. The server raises during startup; the background thread
           captures the exception, sets ``_started`` so we unblock, and
           we re-raise here. The captured exception is more actionable
           than a generic timeout.
        3. Neither happens within 30 s — surface a timeout.
        """
        self._thread = threading.Thread(target=self._run, name="bqemu-server", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=30):
            if self._startup_error is not None:
                raise self._startup_error
            raise RuntimeError("bqemulator test server failed to start within 30s")
        # Event fired — either the server is up, or startup raised and
        # captured the error before signalling. The latter has
        # ``_startup_error`` set; surface it now.
        if self._startup_error is not None:
            raise self._startup_error

    def stop(self) -> None:
        """Stop the server thread."""
        if self._loop is None or self._thread is None:
            return
        fut = asyncio.run_coroutine_threadsafe(self.server.stop(), self._loop)
        # Best-effort wait for clean shutdown; test teardown must never raise.
        with contextlib.suppress(Exception):
            fut.result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        async def _start_then_park() -> None:
            await self.server.start()
            self._started.set()

        try:
            loop.run_until_complete(_start_then_park())
        except BaseException as exc:  # noqa: BLE001 — re-raised in start()
            # Capture and signal the waiter so the foreground call
            # surfaces the actual cause instead of timing out at 30s.
            self._startup_error = exc
            self._started.set()
            loop.close()
            return
        try:
            loop.run_forever()
        finally:
            loop.close()


__all__ = ["ThreadedEmulator"]
