"""Pre-start property guards on :class:`EmulatorServer`.

The ``rest_port`` / ``grpc_port`` / ``rest_url`` / ``grpc_endpoint``
properties raise ``RuntimeError`` when the server hasn't been started
yet — the integration tests always start the server first, so the
not-started branches go uncovered without this unit test.
"""

from __future__ import annotations

import pytest

from bqemulator.config import PersistenceMode, Settings
from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.unit


@pytest.fixture
def stopped_server() -> EmulatorServer:
    """An EmulatorServer that has been constructed but not started."""
    return EmulatorServer(
        Settings(
            persistence_mode=PersistenceMode.EPHEMERAL,
            rest_port=0,
            grpc_port=0,
        )
    )


class TestPreStartPropertyGuards:
    def test_rest_port_raises_when_not_started(
        self,
        stopped_server: EmulatorServer,
    ) -> None:
        with pytest.raises(RuntimeError, match="not started"):
            _ = stopped_server.rest_port

    def test_grpc_port_raises_when_not_started(
        self,
        stopped_server: EmulatorServer,
    ) -> None:
        with pytest.raises(RuntimeError, match="not started"):
            _ = stopped_server.grpc_port

    def test_rest_url_raises_when_not_started(
        self,
        stopped_server: EmulatorServer,
    ) -> None:
        # rest_url depends on rest_port — same guard chains through.
        with pytest.raises(RuntimeError, match="not started"):
            _ = stopped_server.rest_url

    def test_grpc_endpoint_raises_when_not_started(
        self,
        stopped_server: EmulatorServer,
    ) -> None:
        with pytest.raises(RuntimeError, match="not started"):
            _ = stopped_server.grpc_endpoint
