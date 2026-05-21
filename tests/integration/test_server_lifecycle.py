"""Integration test: start + stop the full server.

Exercises the composition root (`bqemulator.server.EmulatorServer`)
end-to-end with an ephemeral configuration, verifying that:

* Both REST and gRPC servers start and bind ports.
* `/healthz` and `/readyz` return 200.
* The gRPC standard health service reports SERVING.
* Shutdown is clean.
"""

from __future__ import annotations

import asyncio

import grpc
import httpx
import pytest

from bqemulator.config import PersistenceMode, Settings
from bqemulator.server import EmulatorServer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_full_server_lifecycle() -> None:
    settings = Settings(
        persistence_mode=PersistenceMode.EPHEMERAL,
        rest_host="127.0.0.1",
        rest_port=0,
        grpc_host="127.0.0.1",
        grpc_port=0,
    )
    server = EmulatorServer(settings)
    await server.start()
    try:
        # REST health
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{server.rest_url}/healthz")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

            r = await client.get(f"{server.rest_url}/readyz")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

            r = await client.get(f"{server.rest_url}/metrics")
            assert r.status_code == 200
            assert "bqemulator_build_info" in r.text

        # gRPC health
        from grpc_health.v1 import health_pb2, health_pb2_grpc

        channel = grpc.aio.insecure_channel(server.grpc_endpoint)
        try:
            stub = health_pb2_grpc.HealthStub(channel)
            resp = await asyncio.wait_for(stub.Check(health_pb2.HealthCheckRequest()), timeout=5)
            assert resp.status == health_pb2.HealthCheckResponse.SERVING
        finally:
            await channel.close()
    finally:
        await server.stop()


async def test_persistent_mode_lifecycle(tmp_path: object) -> None:
    settings = Settings(
        persistence_mode=PersistenceMode.PERSISTENT,
        data_dir=tmp_path,  # type: ignore[arg-type]
        rest_host="127.0.0.1",
        rest_port=0,
        grpc_host="127.0.0.1",
        grpc_port=0,
    )
    server = EmulatorServer(settings)
    await server.start()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{server.rest_url}/healthz")
            assert r.status_code == 200
    finally:
        await server.stop()
