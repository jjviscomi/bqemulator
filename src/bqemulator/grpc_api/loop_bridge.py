"""Run gRPC sync-handler work that touches DuckDB on the event loop thread.

grpc.aio runs sync handlers on its own thread pool, but the main DuckDB
connection, and the DuckDB-backed catalog built on it, must only be used
from the event loop thread. Two threads on one ``DuckDBPyConnection``
deadlock each other, and a statement from one thread can replace the
pending result another thread is about to fetch.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

_T = TypeVar("_T")


def running_loop() -> asyncio.AbstractEventLoop | None:
    """Return the running event loop, or ``None`` when called outside one."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def run_on_loop(
    loop: asyncio.AbstractEventLoop | None,
    fn: Callable[..., _T],
    *args: Any,
) -> _T:
    """Call ``fn(*args)`` on *loop*'s thread and wait for its result.

    Calls *fn* directly when *loop* is ``None`` (a handler built outside a
    running server, as in unit tests) or is already the current thread's
    loop. *fn* must not call ``abort()`` or ``send_initial_metadata()`` on
    a sync servicer context: grpc.aio implements those by waiting on the
    loop, which would then wait on itself.
    """
    if loop is None or running_loop() is loop:
        return fn(*args)

    async def call() -> _T:
        return fn(*args)

    return asyncio.run_coroutine_threadsafe(call(), loop).result()


__all__ = ["run_on_loop", "running_loop"]
