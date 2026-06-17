"""Pytest fixtures for consumers of bqemulator.

Registered as a pytest plugin via the ``pytest11`` entry point declared in
``pyproject.toml``. Installing bqemulator automatically makes these
fixtures available — no ``conftest.py`` wiring required.

Fixtures
--------

``bqemu_settings``
    Session-scoped. Returns a :class:`Settings` configured for ephemeral
    in-memory use on random free ports. Override via ``indirect``
    parametrization for per-test tweaks.

``bqemu_server``
    Session-scoped. A running :class:`EmulatorServer`. Sets the
    ``BIGQUERY_EMULATOR_HOST`` env var for the session and unsets it on
    teardown.

``bqemu_endpoint``
    Session-scoped. ``{"rest_url": ..., "grpc_endpoint": ...}`` dict.

``bqemu_client``
    Function-scoped. A configured ``google.cloud.bigquery.Client`` pointing
    at the emulator. Only available if ``google-cloud-bigquery`` is
    installed.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import os
from typing import TYPE_CHECKING

import pytest

# NOTE: bqemulator.* imports are deferred inside fixture bodies.
# Rationale: this module is loaded by pytest's plugin discovery (pytest11
# entry point in pyproject.toml) BEFORE pytest-cov can install coverage
# hooks. Importing bqemulator.config at module-load time means its class
# bodies execute before coverage is active and show as uncovered even
# though tests exercise them. Deferring the imports to fixture bodies
# ensures they're first imported after coverage starts.

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.config import Settings
    from bqemulator.server import EmulatorServer


@dataclass(slots=True, frozen=True)
class EmulatorEndpoint:
    """Connection info for fixtures and tests."""

    rest_url: str
    grpc_endpoint: str
    project_id: str


@pytest.fixture(scope="session")
def bqemu_settings() -> Settings:
    """Default settings for session-scoped fixtures.

    Ephemeral mode, random ports, INFO logging.
    """
    from bqemulator.config import PersistenceMode, Settings

    return Settings(
        persistence_mode=PersistenceMode.EPHEMERAL,
        rest_host="127.0.0.1",
        rest_port=0,
        grpc_host="127.0.0.1",
        grpc_port=0,
    )


@pytest.fixture(scope="session")
def bqemu_server(
    bqemu_settings: Settings,
) -> Iterator[EmulatorServer]:
    """Start an in-process emulator for the entire test session.

    Runs the server on a background asyncio loop to avoid conflicting with
    test event loops.
    """
    # Deferred import so the plugin module itself stays cheap.
    from bqemulator.testing._thread_runner import ThreadedEmulator

    threaded = ThreadedEmulator(bqemu_settings)
    previous = os.environ.get("BIGQUERY_EMULATOR_HOST")
    try:
        # ``start()`` runs inside the ``try`` so a failed or slow startup still
        # reaches ``threaded.stop()`` in the ``finally``. Otherwise a
        # half-started server leaks its background thread (and the native gRPC /
        # DuckDB resources it holds) into interpreter shutdown, which aborts the
        # process with "terminate called without an active exception".
        threaded.start()
        os.environ["BIGQUERY_EMULATOR_HOST"] = (
            f"{bqemu_settings.rest_host}:{threaded.server.rest_port}"
        )
        yield threaded.server
    finally:
        if previous is None:
            os.environ.pop("BIGQUERY_EMULATOR_HOST", None)
        else:
            os.environ["BIGQUERY_EMULATOR_HOST"] = previous
        threaded.stop()


@pytest.fixture(scope="session")
def bqemu_endpoint(
    bqemu_settings: Settings,
    bqemu_server: EmulatorServer,
) -> EmulatorEndpoint:
    """Session-scoped :class:`EmulatorEndpoint`."""
    return EmulatorEndpoint(
        rest_url=bqemu_server.rest_url,
        grpc_endpoint=bqemu_server.grpc_endpoint,
        project_id=bqemu_settings.default_project_id,
    )


@pytest.fixture
def bqemu_client(bqemu_endpoint: EmulatorEndpoint) -> object:
    """Return a ``google.cloud.bigquery.Client`` pointed at the emulator.

    Raises :class:`pytest.skip.Exception` if ``google-cloud-bigquery`` is
    not installed.
    """
    try:
        from google.api_core.client_options import ClientOptions
        from google.auth.credentials import AnonymousCredentials
        from google.cloud import bigquery
    except ImportError:  # pragma: no cover
        pytest.skip("google-cloud-bigquery not installed")

    return bigquery.Client(
        project=bqemu_endpoint.project_id,
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=bqemu_endpoint.rest_url),
    )


__all__ = ["EmulatorEndpoint", "bqemu_client", "bqemu_endpoint", "bqemu_server", "bqemu_settings"]
