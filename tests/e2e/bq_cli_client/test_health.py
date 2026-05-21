"""E2E: bq CLI smoke test against a live container.

A no-state ``bq query 'SELECT 1'`` proves the canonical CLI surface
is reachable through ``--api=`` + ``CLOUDSDK_AUTH_DISABLE_CREDENTIALS``.
If this test fails, every other bq-CLI test will fail too — keep it
first.
"""

from __future__ import annotations

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e


def test_bq_query_select_one(bq_runner: BqRunner) -> None:
    """``bq query 'SELECT 1 AS n'`` returns one row with ``n=1``."""
    rows = bq_runner.query_json("SELECT 1 AS n")
    assert rows == [{"n": "1"}]


def test_bq_version_invocation_succeeds(bq_runner: BqRunner) -> None:
    """``bq version`` proves the binary is invocable in our env shape.

    This catches sysadmin-level breakage (missing python interpreter
    in the bq shim, malformed CLOUDSDK_CONFIG dir) before any
    emulator-side assertion runs.
    """
    result = bq_runner.run("version")
    assert result.succeeded(), result.stderr
    assert "BigQuery CLI" in result.stdout
