"""Perf-tier shared fixtures + per-arch baseline loader.

Every perf test inherits:

1. The ``perf`` pytest marker (auto-applied so a developer can opt-in
   with ``pytest -m perf`` without remembering the directory layout).
2. A deterministic ``BQEMU_PERF_SEED`` (default ``0``) wired into
   :mod:`random` so dataset construction is reproducible across runs.
3. The per-arch baseline path (auto-detected) surfaced as a fixture
   so the comparison gate can be wired by the operator without
   editing the file.

See [`ADR 0025`](../../docs/adr/0025-perf-tier-design-contract.md) for
the design contract.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import random
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator

# Default deterministic seed for dataset construction.
_DEFAULT_PERF_SEED = 0
PERF_SEED = int(os.environ.get("BQEMU_PERF_SEED", str(_DEFAULT_PERF_SEED)))

# Per-arch baseline directory.
BASELINES_DIR = Path(__file__).parent / "baselines"

# Note: per the chaos-tier convention, each ``test_*.py`` file in this
# directory declares ``pytestmark = pytest.mark.perf`` at module level
# so ``pytest -m perf`` collects them without remembering the
# directory layout. ``pytestmark`` at *conftest* level is a no-op —
# pytest only reads it from collected modules.


def _detect_arch() -> str:
    """Return the canonical arch identifier for the current host.

    Maps ``platform.machine()`` + ``sys.platform`` to one of the three
    arch labels that have committed baseline files. Falls back to
    ``linux-x86_64`` (the CI canonical) with a warning if the host
    doesn't match a known arch.
    """
    machine = platform.machine().lower()
    plat = sys.platform.lower()
    if plat.startswith("darwin") and machine in {"arm64", "aarch64"}:
        return "darwin-arm64"
    if plat.startswith("linux") and machine in {"arm64", "aarch64"}:
        return "linux-arm64"
    if plat.startswith("linux") and machine in {"x86_64", "amd64"}:
        return "linux-x86_64"
    # Darwin x86_64 dev boxes are a real configuration but ship no baseline
    # of their own; fall back to the CI canonical so the comparison still
    # surfaces drift, even if absolute numbers shift.
    return "linux-x86_64"


HOST_ARCH = _detect_arch()


def pytest_report_header(config: pytest.Config) -> str:
    """Print active perf config in the pytest session header.

    Mirrors the chaos tier's ``pytest_report_header`` — surfaces the
    seed + arch in CI logs without using ``warnings.warn`` (which the
    project's ``filterwarnings = ["error", ...]`` would convert into a
    test failure).
    """
    baseline_path = BASELINES_DIR / f"{HOST_ARCH}.json"
    exists = "present" if baseline_path.exists() else "missing"
    return f"perf.seed={PERF_SEED} perf.arch={HOST_ARCH} perf.baseline={exists}"


@pytest.fixture(autouse=True)
def _perf_deterministic_seed() -> Iterator[None]:
    """Reset every perf test to the same RNG state.

    Tests that use ``random.randint`` / ``random.choice`` to assemble
    fixed datasets must do so under a fixed seed; otherwise scenarios
    become non-reproducible. This fixture runs before each test,
    restores the seed, and lets the test consume the RNG freely.
    """
    random.seed(PERF_SEED)
    return


@pytest.fixture(scope="session")
def perf_arch() -> str:
    """Return the auto-detected arch identifier for the host."""
    return HOST_ARCH


@pytest.fixture(scope="session")
def perf_baseline() -> dict[str, dict[str, float]] | None:
    """Load the per-arch baseline file, if present.

    Returns ``None`` when no baseline has been recorded yet — the first
    invocation on a new arch lands here. Tests use this fixture to skip
    the comparison gate gracefully on uncovered arches; the
    ``--benchmark-compare`` CLI flag handles the actual gate when CI
    sets it.

    The baseline schema is documented in
    [`ADR 0025 §6`](../../docs/adr/0025-perf-tier-design-contract.md);
    each entry is keyed by the fully-qualified test name and maps to a
    ``{"median": float, "stddev": float, "unit": str}`` dict.
    """
    path = BASELINES_DIR / f"{HOST_ARCH}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    return {entry["name"]: entry for entry in raw.get("benchmarks", [])}
