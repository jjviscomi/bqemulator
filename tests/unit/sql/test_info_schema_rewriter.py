"""Unit tests for the INFORMATION_SCHEMA.ROUTINES rewriter."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, RoutineArgument, RoutineMeta
from bqemulator.sql.rewriter.information_schema import (
    expand_information_schema_routines,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 4, 15, tzinfo=UTC)


def _catalog_with_routine(routine_id: str, language: str = "SQL") -> MemoryCatalogRepository:
    cat = MemoryCatalogRepository()
    cat.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="ds",
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        ),
    )
    cat.create_routine(
        RoutineMeta(
            project_id="p",
            dataset_id="ds",
            routine_id=routine_id,
            routine_type="SCALAR_FUNCTION",
            language=language,
            definition_body="x + 1",
            arguments=(RoutineArgument(name="x", data_type={"typeKind": "INT64"}),),
            return_type={"typeKind": "INT64"},
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        ),
    )
    return cat


def test_no_reference_unchanged() -> None:
    cat = MemoryCatalogRepository()
    sql = "SELECT 1"
    assert expand_information_schema_routines(sql, "p", cat) == sql


def test_uppercase_detected() -> None:
    cat = _catalog_with_routine("f1")
    sql = "SELECT routine_name FROM ds.INFORMATION_SCHEMA.ROUTINES"
    out = expand_information_schema_routines(sql, "p", cat)
    assert "VALUES" in out
    assert "'f1'" in out


def test_project_qualified() -> None:
    cat = _catalog_with_routine("f2")
    sql = "SELECT * FROM p.ds.INFORMATION_SCHEMA.ROUTINES"
    out = expand_information_schema_routines(sql, "p", cat)
    assert "'f2'" in out


def test_no_routines_emits_empty_values() -> None:
    cat = MemoryCatalogRepository()
    cat.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="empty",
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        ),
    )
    sql = "SELECT * FROM empty.INFORMATION_SCHEMA.ROUTINES"
    out = expand_information_schema_routines(sql, "p", cat)
    assert "WHERE FALSE" in out


def test_unqualified_emits_empty_values() -> None:
    cat = _catalog_with_routine("f")
    sql = "SELECT * FROM INFORMATION_SCHEMA.ROUTINES"
    out = expand_information_schema_routines(sql, "p", cat)
    # Unqualified form emits an empty placeholder
    assert "WHERE FALSE" in out


def test_javascript_routine_body() -> None:
    cat = _catalog_with_routine("js_fn", language="JAVASCRIPT")
    sql = "SELECT routine_body FROM ds.INFORMATION_SCHEMA.ROUTINES"
    out = expand_information_schema_routines(sql, "p", cat)
    assert "'EXTERNAL'" in out
