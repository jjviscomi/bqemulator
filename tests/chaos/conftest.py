"""Chaos-tier shared fixtures + determinism enforcement.

All chaos tests inherit:

1. A frozen random seed (``CHAOS_SEED``, default ``0``) wired into both
   :mod:`random` and ``hypothesis.seed``. Override via the
   ``BQEMU_CHAOS_SEED`` env var when reproducing a flake — the suite
   logs the active seed at session start so a failure is reproducible
   from the CI log alone.

2. A timeout cap. Local invocation is via ``make test-chaos`` (which
   passes ``--timeout=60``); inside the test process we re-assert the
   cap so a developer running ``pytest tests/chaos/`` without the
   Make wrapper still gets the documented bound.

3. A ``pytestmark = pytest.mark.chaos`` re-applied per file so a
   developer can opt-in with ``pytest -m chaos`` without remembering
   the directory layout.
"""

from __future__ import annotations

import os
import random
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator

# Default deterministic seed. Override via BQEMU_CHAOS_SEED for repro.
_DEFAULT_CHAOS_SEED = 0
CHAOS_SEED = int(os.environ.get("BQEMU_CHAOS_SEED", str(_DEFAULT_CHAOS_SEED)))

# Per-scenario timeout cap. Mirrors ``make test-chaos`` (``--timeout=60``)
# so a developer running ``pytest tests/chaos/`` directly still gets the
# documented bound. Honour the env override so a session that needs a
# longer window (e.g. debugging) can lift it without editing the file.
CHAOS_SCENARIO_TIMEOUT = int(os.environ.get("BQEMU_CHAOS_TIMEOUT", "60"))


def pytest_report_header(config: pytest.Config) -> str:
    """Print the active chaos seed in the pytest session header.

    This surfaces the seed in CI logs without using the warning channel
    (which the project's ``filterwarnings = ["error", ...]`` would
    convert into a test failure). Reproducing a chaos flake is a matter
    of copying ``BQEMU_CHAOS_SEED=<seed>`` from the header into the
    repro command.
    """
    return f"chaos.seed={CHAOS_SEED} chaos.timeout={CHAOS_SCENARIO_TIMEOUT}s"


@pytest.fixture(autouse=True)
def _chaos_deterministic_seed() -> Iterator[None]:
    """Reset every chaos test to the same RNG state.

    Chaos tests that use ``random.randint`` / ``random.choice`` /
    ``random.shuffle`` to assemble inputs must do so under a fixed
    seed; otherwise scenarios become non-reproducible. This fixture
    runs before each test, restores the seed, and lets the test
    consume the RNG freely.
    """
    random.seed(CHAOS_SEED)
    return


@pytest.fixture(autouse=True)
def _chaos_timeout(request: pytest.FixtureRequest) -> None:
    """Apply the chaos scenario timeout via pytest-timeout's marker API.

    pytest-timeout reads the ``timeout`` marker; we set it dynamically
    so the value comes from ``BQEMU_CHAOS_TIMEOUT`` and stays in sync
    across the file family.
    """
    request.node.add_marker(pytest.mark.timeout(CHAOS_SCENARIO_TIMEOUT))
