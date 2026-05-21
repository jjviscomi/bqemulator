"""Integration test: the pytest plugin itself."""

from __future__ import annotations

import httpx
import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def test_bqemu_server_fixture_starts_emulator(bqemu_server: EmulatorServer) -> None:
    r = httpx.get(f"{bqemu_server.rest_url}/healthz", timeout=5.0)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_bqemu_endpoint_fixture(bqemu_endpoint: object) -> None:
    assert bqemu_endpoint.rest_url.startswith("http://")  # type: ignore[attr-defined]
    assert ":" in bqemu_endpoint.grpc_endpoint  # type: ignore[attr-defined]


def test_emulator_host_env_var_is_set(bqemu_server: EmulatorServer) -> None:
    import os

    assert os.environ.get("BIGQUERY_EMULATOR_HOST", "").endswith(
        f":{bqemu_server.rest_port}",
    )
