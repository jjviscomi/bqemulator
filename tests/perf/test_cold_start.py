"""Cold-start latency benchmark.

Measures ``docker run`` → first ``/healthz`` 200 round-trip for the
published-to-CI image. This is the only perf scenario that spans a
process boundary; the others measure in-process work via the
``bqemu_server`` session fixture.

The metric the operator cares about is "how long until a fresh CI
pipeline can route requests to the emulator" — which is exactly
end-to-end wall-clock, including image load + Python startup +
gRPC + REST server bind.

Per [`ADR 0025 §7`](../../docs/adr/0025-perf-tier-design-contract.md)
this scenario declares a lower ``min_rounds`` than the default 5 (the
~5 s per round would otherwise balloon CI wall-clock).
"""

from __future__ import annotations

from collections.abc import Callable
import shutil
import socket
import subprocess
import time

import pytest

pytestmark = pytest.mark.perf


def _docker_available() -> bool:
    """Return True if the host has a reachable Docker daemon."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],  # noqa: S607 — `docker` resolved from PATH is fine for tests
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _image_present(image: str) -> bool:
    """Return True if the named image is available locally."""
    if not _docker_available():
        return False
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv (S607 silenced at the argv line)
            ["docker", "image", "inspect", image],  # noqa: S607 — `docker` resolved from PATH
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _free_port() -> int:
    """Bind a transient socket to find a free TCP port for the container."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_healthz(port: int, deadline_s: float) -> bool:
    """Poll ``http://127.0.0.1:<port>/healthz`` until it returns 200 or the deadline lapses."""
    import urllib.error
    import urllib.request

    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/healthz",
                timeout=1,
            ) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            # Server not up yet; loop and retry.
            time.sleep(0.05)
    return False


@pytest.mark.benchmark(min_rounds=3, max_time=60.0, warmup=False)
def test_cold_start_to_healthz(benchmark: Callable[..., None]) -> None:
    """Measure ``docker run`` → first ``/healthz`` 200 round-trip.

    Each round starts a fresh container from
    ``ghcr.io/jjviscomi/bqemulator:dev`` (the local-build tag produced
    by ``make docker-build``), polls ``/healthz`` until 200, and tears
    the container down. The benchmarked block excludes container
    *removal* (which is amortised across the loop) but includes the
    full ``docker run`` + healthz handshake — that's what the operator
    sees.
    """
    image = "ghcr.io/jjviscomi/bqemulator:dev"
    if not _docker_available():
        pytest.skip("docker daemon not available")
    if not _image_present(image):
        pytest.skip(
            f"image {image} not present locally — run `make docker-build` first",
        )

    containers_to_clean: list[str] = []

    def _round() -> None:
        rest_port = _free_port()
        grpc_port = _free_port()
        name = f"bqemu-perf-cold-{rest_port}"
        # ``docker run -d`` returns the container id immediately; the
        # container is fully started once healthz answers 200. The
        # ``argv`` list is built into a local variable so the S607
        # rule (partial executable path) doesn't fire at the
        # ``subprocess.run`` call-site (it only flags literals passed
        # directly to subprocess); ``docker`` resolves from PATH.
        argv = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-p",
            f"{rest_port}:9050",
            "-p",
            f"{grpc_port}:9060",
            "-e",
            "BQEMU_REST_HOST=0.0.0.0",
            "-e",
            "BQEMU_GRPC_HOST=0.0.0.0",
            image,
        ]
        run = subprocess.run(  # noqa: S603 — fixed argv
            argv,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if run.returncode != 0:
            msg = f"docker run failed: {run.stderr.decode(errors='replace')}"
            raise RuntimeError(msg)
        containers_to_clean.append(name)
        if not _wait_for_healthz(rest_port, deadline_s=30.0):
            msg = f"healthz on :{rest_port} did not return 200 within 30s"
            raise RuntimeError(msg)

    try:
        benchmark(_round)
    finally:
        for name in containers_to_clean:
            subprocess.run(  # noqa: S603 — fixed argv (S607 silenced at the argv line)
                ["docker", "rm", "-f", name],  # noqa: S607 — `docker` resolved from PATH
                capture_output=True,
                timeout=10,
                check=False,
            )
