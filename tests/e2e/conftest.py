"""E2E test fixtures: live bqemulator container."""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path

import pytest

from bqemulator.testing.testcontainers import BigQueryEmulatorContainer


@pytest.fixture(scope="session")
def bqemu_gcs_root_host(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped host dir mounted into the container as the GCS local root.

    G1: load/extract jobs that reference ``gs://<anything>/<path>`` URIs
    resolve to ``$BQEMU_GCS_LOCAL_ROOT/<anything>/<path>`` inside the
    executor. The container fixture mounts this host directory at
    ``/var/lib/bqemu-gcs`` so tests can drop Avro/ORC files here and
    reference them via ``gs://<bucket>/<filename>`` URIs the same way
    real client code does.

    The directory is world-readable so the non-root ``bqemu`` user
    inside the container (UID 1000) can read files written by the host
    user (whose UID is typically 501 on macOS / 1000+ in CI). The
    canonical Avro+ORC fixtures are staged here by
    :func:`scripts.stage_g1_e2e_fixtures.stage` so the Python E2E
    suite reads the *same* bytes the Node/Go/Java suites do (those
    pre-stage via the Makefile recipe before container start).
    """
    root = tmp_path_factory.mktemp("bqemu_gcs_root", numbered=False)
    root.chmod(0o777)
    _load_staging_module().stage(root)
    return root


def _load_staging_module():
    """Import ``scripts/stage_g1_e2e_fixtures.py`` without making it a package."""
    import importlib.util

    here = Path(__file__).resolve().parent.parent.parent
    script_path = here / "scripts" / "stage_g1_e2e_fixtures.py"
    spec = importlib.util.spec_from_file_location("stage_g1_e2e_fixtures", script_path)
    if spec is None or spec.loader is None:
        msg = f"failed to load {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def bqemu_container(
    bqemu_gcs_root_host: Path,
) -> Iterator[BigQueryEmulatorContainer]:
    """Start the bqemulator container for the session."""
    image = os.environ.get("BQEMU_IMAGE")  # CI provides a locally-built image tag
    container = BigQueryEmulatorContainer(
        image=image,
        gcs_local_root_host=str(bqemu_gcs_root_host),
    )
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def bqemu_rest_url(bqemu_container: BigQueryEmulatorContainer) -> str:
    return bqemu_container.get_rest_url()


@pytest.fixture(scope="session")
def bqemu_grpc_endpoint(bqemu_container: BigQueryEmulatorContainer) -> str:
    return bqemu_container.get_grpc_endpoint()
