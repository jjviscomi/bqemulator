"""Pytest fixtures for the bq-CLI E2E suite.

Reuses the session-scoped ``bqemu_rest_url`` fixture from
``tests/e2e/conftest.py`` (which starts the live emulator container)
and wraps it with a ``BqRunner`` whose subprocesses talk to that
endpoint via ``--api=`` per invocation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .bq_runner import BqRunner


@pytest.fixture(scope="session")
def bq_runner(
    bqemu_rest_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> BqRunner:
    """Session-scoped ``bq`` subprocess wrapper bound to the live container."""
    work_dir: Path = tmp_path_factory.mktemp("bq_cli_config")
    return BqRunner(
        api_url=bqemu_rest_url,
        project_id="e2e-bq-cli",
        work_dir=work_dir,
    )
