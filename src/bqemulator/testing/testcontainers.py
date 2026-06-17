"""Testcontainers wrapper for the published Docker image.

Use this when:

* You want a real subprocess (matches CI/CD more closely than in-process).
* You are testing clients in languages other than Python.
* You want persistence across test functions.

Example::

    with BigQueryEmulatorContainer() as emu:
        rest_url = emu.get_rest_url()
        grpc_endpoint = emu.get_grpc_endpoint()
        # ... run tests ...
"""

from __future__ import annotations

import contextlib
import os
import re
from typing import Self
import warnings

from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

DEFAULT_IMAGE = "ghcr.io/jjviscomi/bqemulator:latest"
DEFAULT_REST_PORT = 9050
DEFAULT_GRPC_PORT = 9060
_READY_RE = re.compile(r"rest\.listen")
# Grace period for the container's REST listener to log readiness.
# ``wait_for_logs`` returns the instant the pattern matches, so this bounds
# only a slow start under shared-CI load (where a tighter window
# intermittently elapsed while the container was still booting), never a
# normal run.
_READY_TIMEOUT_SECONDS = 90


class BigQueryEmulatorContainer(DockerContainer):
    """Manages a bqemulator Docker container for testing."""

    def __init__(
        self,
        image: str | None = None,
        *,
        rest_port: int = DEFAULT_REST_PORT,
        grpc_port: int = DEFAULT_GRPC_PORT,
        gcs_local_root_host: str | None = None,
    ) -> None:
        # Allow overriding via env var — useful in CI where an in-tree
        # build is loaded as `ghcr.io/jjviscomi/bqemulator:ci-<sha>`.
        effective_image = image or os.environ.get("BQEMU_IMAGE", DEFAULT_IMAGE)
        super().__init__(effective_image)
        self._rest_internal = rest_port
        self._grpc_internal = grpc_port
        self.with_exposed_ports(rest_port, grpc_port)
        # Inside the container, bind on all interfaces so the exposed
        # ports are reachable from the host. The container's network
        # scope already restricts access.
        self.with_env("BQEMU_REST_HOST", "0.0.0.0")  # noqa: S104
        self.with_env("BQEMU_GRPC_HOST", "0.0.0.0")  # noqa: S104
        self.with_env("BQEMU_REST_PORT", str(rest_port))
        self.with_env("BQEMU_GRPC_PORT", str(grpc_port))
        # Admin endpoints are off by default in the published image.
        # The testcontainer wrapper always opts them in so the Python /
        # Node / Go / Java E2E suites can exercise the ``/admin/*``
        # surface without a custom image build.
        self.with_env("BQEMU_ADMIN_ENABLED", "1")
        # Load/extract jobs that reference ``gs://`` URIs need a
        # host→container bind mount so the file the test writes on the
        # host is visible to the executor inside the container. The
        # caller passes a host directory; the wrapper mounts it at
        # ``/var/lib/bqemu-gcs`` and points ``BQEMU_GCS_LOCAL_ROOT``
        # at the same path. The test writes its Avro/ORC files under
        # the host dir and references them via ``gs://anybucket/<file>``
        # — the executor's ``_resolve_uri`` strips the ``gs://``
        # prefix and joins the remaining path under the local root.
        if gcs_local_root_host is not None:
            self.with_volume_mapping(
                gcs_local_root_host,
                "/var/lib/bqemu-gcs",
                mode="rw",
            )
            self.with_env("BQEMU_GCS_LOCAL_ROOT", "/var/lib/bqemu-gcs")

    def start(self) -> Self:
        """Start the container and wait until the REST listener is ready.

        ``testcontainers`` emits a ``DeprecationWarning`` when
        ``wait_for_logs`` is passed a plain string / regex because the
        library plans to replace it with a structured wait strategy.
        We silence that warning at the call site so consumers of this
        wrapper don't need to edit their own pytest filter list.
        """
        super().start()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                wait_for_logs(self, _READY_RE.pattern, timeout=_READY_TIMEOUT_SECONDS)
        except Exception:
            # The container started but never signalled readiness in time. Stop
            # it before re-raising so a caller that runs ``start()`` before its
            # own ``try``/``finally`` does not leak a running container.
            with contextlib.suppress(Exception):
                self.stop()
            raise
        return self

    def get_rest_url(self) -> str:
        """Return the externally-reachable REST URL."""
        host = self.get_container_host_ip()
        port = self.get_exposed_port(self._rest_internal)
        return f"http://{host}:{port}"

    def get_grpc_endpoint(self) -> str:
        """Return the externally-reachable gRPC endpoint (host:port)."""
        host = self.get_container_host_ip()
        port = self.get_exposed_port(self._grpc_internal)
        return f"{host}:{port}"


__all__ = ["BigQueryEmulatorContainer"]
