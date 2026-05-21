"""Test helpers.

Two entry points:

* :mod:`bqemulator.testing.fixtures` — pytest plugin providing
  ``bqemu_server``, ``bqemu_client``, and related fixtures. Registered
  automatically via the ``pytest11`` entry point in ``pyproject.toml``.

* :mod:`bqemulator.testing.testcontainers` — Testcontainers wrapper around
  the published Docker image for non-Python clients or full e2e tests.
"""

from __future__ import annotations

from bqemulator.testing.testcontainers import BigQueryEmulatorContainer

__all__ = ["BigQueryEmulatorContainer"]
