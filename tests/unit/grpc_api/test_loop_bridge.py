"""Tests for running gRPC sync-handler work on the event loop thread."""

from __future__ import annotations

import asyncio
import threading

import pytest

from bqemulator.grpc_api.loop_bridge import run_on_loop, running_loop

pytestmark = pytest.mark.unit


def test_running_loop_is_none_outside_a_loop() -> None:
    assert running_loop() is None


def test_calls_directly_without_a_loop() -> None:
    assert run_on_loop(None, threading.get_ident) == threading.get_ident()


@pytest.mark.asyncio
async def test_calls_directly_on_the_loop_thread() -> None:
    loop = asyncio.get_running_loop()
    assert run_on_loop(loop, threading.get_ident) == threading.get_ident()


@pytest.mark.asyncio
async def test_runs_on_the_loop_thread_from_a_worker_thread() -> None:
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()

    def worker() -> tuple[int, int]:
        return run_on_loop(loop, lambda a, b: (threading.get_ident(), a + b), 2, 3)

    ran_on, result = await asyncio.to_thread(worker)
    assert ran_on == loop_thread
    assert result == 5


@pytest.mark.asyncio
async def test_propagates_exceptions_to_the_worker_thread() -> None:
    loop = asyncio.get_running_loop()

    def boom() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await asyncio.to_thread(run_on_loop, loop, boom)
