"""Unit tests for UDF flat-name generation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.models import RoutineMeta
from bqemulator.domain.errors import ValidationError
from bqemulator.udf.naming import (
    qualified_routine_name,
    qualified_routine_name_parts,
    sanitize_component,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 4, 15, tzinfo=UTC)


def _routine(pid: str, did: str, rid: str) -> RoutineMeta:
    return RoutineMeta(
        project_id=pid,
        dataset_id=did,
        routine_id=rid,
        routine_type="SCALAR_FUNCTION",
        language="SQL",
        definition_body="x",
        creation_time=NOW,
        last_modified_time=NOW,
        etag="e",
    )


def test_qualified_name_basic() -> None:
    r = _routine("proj", "ds", "fn")
    assert qualified_routine_name(r) == "proj__ds__fn"


def test_qualified_name_sanitises_hyphens() -> None:
    r = _routine("test-project", "ds", "fn")
    assert qualified_routine_name(r) == "test_h_project__ds__fn"


def test_sanitize_component_passthrough() -> None:
    assert sanitize_component("plain") == "plain"
    assert sanitize_component("with-dash") == "with_h_dash"


def test_qualified_routine_name_parts_matches() -> None:
    r = _routine("p", "d", "f")
    assert qualified_routine_name_parts("p", "d", "f") == qualified_routine_name(r)


def test_rejects_injection_in_project() -> None:
    with pytest.raises(ValidationError):
        qualified_routine_name_parts("p'; DROP", "d", "f")


def test_rejects_injection_in_dataset() -> None:
    with pytest.raises(ValidationError):
        qualified_routine_name_parts("p", "d' DROP", "f")


def test_rejects_injection_in_routine() -> None:
    with pytest.raises(ValidationError):
        qualified_routine_name_parts("p", "d", 'f";')
