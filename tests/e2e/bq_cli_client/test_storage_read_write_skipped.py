"""Deliberate gap: bq CLI has no Storage Read/Write API command.

The Storage Read gRPC surface and Storage Write gRPC surface
 are exercised by the four SDK client suites
(``python_client/test_storage_read.py`` and siblings,
``python_client/test_storage_write.py`` and siblings).

The bq CLI's closest equivalents are:

* ``bq head`` — exercises ``tabledata.list`` (REST, not the Storage
  Read gRPC surface). Covered in ``test_jobs.py::test_head_returns_rows``.
* ``bq load`` / ``bq insert`` — exercise REST load + streamingInsert
  paths, not the Storage Write Append/Commit gRPC surface. Covered
  in ``test_jobs.py``.

Adding synthetic bq tests for Storage Read/Write would mislead future
readers about what's actually exercised. We document the gap
explicitly via ``pytest.skip`` with a clear reason and let the SDK
suites carry the gRPC contract.

Per the ADR for bq-CLI conformance (``docs/adr/0032-bq-cli-conformance-client.md``):
this is a deliberate scope choice, not an oversight.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def test_storage_read_not_applicable_to_bq_cli() -> None:
    """bq CLI has no Storage Read gRPC command — documented gap."""
    pytest.skip(
        "bq CLI does not expose the Storage Read gRPC API. "
        "The Python/Node/Go/Java suites cover this surface in "
        "test_storage_read.* — see ADR 0032.",
    )


def test_storage_write_not_applicable_to_bq_cli() -> None:
    """bq CLI has no Storage Write gRPC command — documented gap."""
    pytest.skip(
        "bq CLI does not expose the Storage Write gRPC API. "
        "The Python/Node/Go/Java suites cover this surface in "
        "test_storage_write.* — see ADR 0032.",
    )
