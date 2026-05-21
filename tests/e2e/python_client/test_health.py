"""E2E: health endpoints against a live Docker container.

This is the "first e2e" test that validates the Phase 0 ship criterion:
the published container image starts, listens, and responds to probes.
"""

from __future__ import annotations

import asyncio

import grpc
import httpx
import pytest

pytestmark = pytest.mark.e2e


def test_healthz_returns_ok(bqemu_rest_url: str) -> None:
    r = httpx.get(f"{bqemu_rest_url}/healthz", timeout=10.0)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_readyz_returns_ok(bqemu_rest_url: str) -> None:
    r = httpx.get(f"{bqemu_rest_url}/readyz", timeout=10.0)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["duckdb"] == "ok"
    assert body["checks"]["catalog"] == "ok"


def test_metrics_exposed(bqemu_rest_url: str) -> None:
    # Make a couple of requests to populate counters
    httpx.get(f"{bqemu_rest_url}/healthz", timeout=10.0)
    httpx.get(f"{bqemu_rest_url}/healthz", timeout=10.0)

    r = httpx.get(f"{bqemu_rest_url}/metrics", timeout=10.0)
    assert r.status_code == 200
    assert "bqemulator_rest_requests_total" in r.text
    assert "bqemulator_build_info" in r.text


def test_correlation_id_echoed(bqemu_rest_url: str) -> None:
    r = httpx.get(
        f"{bqemu_rest_url}/healthz",
        headers={"x-correlation-id": "e2e-test-42"},
        timeout=10.0,
    )
    assert r.headers.get("x-correlation-id") == "e2e-test-42"


def test_grpc_health_check(bqemu_grpc_endpoint: str) -> None:
    from grpc_health.v1 import health_pb2, health_pb2_grpc

    async def _check() -> None:
        channel = grpc.aio.insecure_channel(bqemu_grpc_endpoint)
        try:
            stub = health_pb2_grpc.HealthStub(channel)
            resp = await asyncio.wait_for(
                stub.Check(health_pb2.HealthCheckRequest()),
                timeout=10,
            )
            assert resp.status == health_pb2.HealthCheckResponse.SERVING
        finally:
            await channel.close()

    asyncio.run(_check())
