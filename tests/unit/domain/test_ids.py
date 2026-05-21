"""Tests for typed id validators."""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import ValidationError
from bqemulator.domain.ids import (
    DatasetId,
    JobId,
    ProjectId,
    RoutineId,
    TableId,
    validate_dataset_id,
    validate_job_id,
    validate_project_id,
    validate_routine_id,
    validate_table_id,
    validate_table_ref,
)

pytestmark = pytest.mark.unit


class TestProjectId:
    @pytest.mark.parametrize(
        "value",
        ["my-project", "test-project-123", "a2345b", "alpha-bravo-charlie-0"],
    )
    def test_accepts_valid(self, value: str) -> None:
        assert str(ProjectId(value)) == value

    @pytest.mark.parametrize(
        "value",
        [
            "My-Project",  # uppercase
            "1starts-digit",  # starts with digit
            "ends-with-hyphen-",  # ends with hyphen
            "abc",  # too short
            "a" * 40,  # too long
            "underscore_not_ok",
        ],
    )
    def test_rejects_invalid(self, value: str) -> None:
        with pytest.raises(ValidationError):
            ProjectId(value)


class TestDatasetId:
    @pytest.mark.parametrize("value", ["sales", "Sales_2026", "a", "A_B_C_123"])
    def test_accepts_valid(self, value: str) -> None:
        assert str(DatasetId(value)) == value

    @pytest.mark.parametrize(
        "value",
        ["has-hyphen", "has.dot", "has space", ""],
    )
    def test_rejects_invalid(self, value: str) -> None:
        with pytest.raises(ValidationError):
            DatasetId(value)


class TestTableId:
    @pytest.mark.parametrize("value", ["orders", "orders-2026", "orders_by_month"])
    def test_accepts_valid(self, value: str) -> None:
        assert str(TableId(value)) == value

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValidationError):
            TableId("")


class TestJobId:
    def test_accepts_uuid_like(self) -> None:
        assert str(JobId("job-abc123-def456")) == "job-abc123-def456"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValidationError):
            JobId("")


class TestRoutineId:
    def test_accepts_valid(self) -> None:
        assert str(RoutineId("SafeDivide_v2")) == "SafeDivide_v2"


class TestImmutabilityAndEquality:
    def test_ids_are_frozen(self) -> None:
        d = DatasetId("sales")
        with pytest.raises((AttributeError, Exception)):
            d.value = "other"  # type: ignore[misc]

    def test_equality_by_value(self) -> None:
        assert DatasetId("sales") == DatasetId("sales")
        assert DatasetId("sales") != DatasetId("marketing")

    def test_hashable(self) -> None:
        s = {DatasetId("sales"), DatasetId("sales"), DatasetId("marketing")}
        assert len(s) == 2


class TestFreeValidators:
    """The id-class constructors share validation with the free functions
    in ``bqemulator.domain.ids``. Pin the free-function surface so other
    modules (the catalog repository, the routes layer) that prefer the
    bare validator helpers stay correct."""

    def test_validate_project_id_accepts_valid(self) -> None:
        assert validate_project_id("my-project") == "my-project"

    def test_validate_project_id_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError):
            validate_project_id("Bad/Project")

    def test_validate_dataset_id_accepts_valid(self) -> None:
        assert validate_dataset_id("sales") == "sales"

    def test_validate_dataset_id_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError):
            validate_dataset_id("dash-not-allowed")

    def test_validate_table_id_accepts_valid(self) -> None:
        assert validate_table_id("orders") == "orders"

    def test_validate_table_id_rejects_empty(self) -> None:
        with pytest.raises(ValidationError):
            validate_table_id("")

    def test_validate_routine_id_accepts_valid(self) -> None:
        assert validate_routine_id("SafeDivide_v2") == "SafeDivide_v2"

    def test_validate_routine_id_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError):
            validate_routine_id("dash-not-allowed")

    def test_validate_job_id_accepts_uuid_like(self) -> None:
        job_id = "bqemu_aabbccddee01"
        assert validate_job_id(job_id) == job_id

    def test_validate_job_id_rejects_empty(self) -> None:
        with pytest.raises(ValidationError):
            validate_job_id("")

    def test_validate_table_ref_returns_triple(self) -> None:
        # Project ids must satisfy the documented BigQuery pattern
        # (alphanumerics + hyphen, ≥6 chars when starting with a letter).
        assert validate_table_ref("my-project", "ds", "tbl") == ("my-project", "ds", "tbl")

    def test_validate_table_ref_rejects_when_any_part_invalid(self) -> None:
        with pytest.raises(ValidationError):
            validate_table_ref("my-project", "bad/ds", "tbl")
