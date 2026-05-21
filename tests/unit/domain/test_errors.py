"""Tests for the domain-error hierarchy and BigQuery ErrorProto rendering."""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import (
    AlreadyExistsError,
    DomainError,
    ErrorDetail,
    InternalError,
    InvalidQueryError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    ResourceRef,
    UnsupportedFeatureError,
    ValidationError,
    resource_already_exists,
    resource_not_found,
)

pytestmark = pytest.mark.unit


class TestDomainErrorBase:
    def test_is_an_exception(self) -> None:
        err = InvalidQueryError("bad sql")
        assert isinstance(err, Exception)
        assert str(err) == "bad sql"

    def test_message_attribute(self) -> None:
        err = InvalidQueryError("bad sql")
        assert err.message == "bad sql"

    def test_details_default_empty(self) -> None:
        err = InvalidQueryError("bad sql")
        assert err.details == []

    def test_details_are_captured(self) -> None:
        detail = ErrorDetail(reason="invalidQuery", message="bad at line 1")
        err = InvalidQueryError("bad sql", details=[detail])
        assert err.details == [detail]


class TestBigQueryErrorRendering:
    def test_shape_matches_bigquery_error_proto(self) -> None:
        err = InvalidQueryError("bad sql")
        payload = err.to_bigquery_error()

        assert set(payload.keys()) == {"error"}
        inner = payload["error"]
        assert inner["code"] == 400
        assert inner["message"] == "bad sql"
        assert inner["status"] == "INVALID_ARGUMENT"
        assert isinstance(inner["errors"], list)

    def test_default_error_detail_uses_class_reason(self) -> None:
        err = InvalidQueryError("bad sql")
        inner = err.to_bigquery_error()["error"]
        assert inner["errors"][0]["reason"] == "invalidQuery"
        assert inner["errors"][0]["domain"] == "global"
        assert inner["errors"][0]["message"] == "bad sql"

    def test_explicit_details_override_default(self) -> None:
        err = InvalidQueryError(
            "bad sql",
            details=[
                ErrorDetail(reason="syntax", message="missing semicolon", location="line 2"),
            ],
        )
        errors = err.to_bigquery_error()["error"]["errors"]
        assert errors[0]["reason"] == "syntax"
        assert errors[0]["location"] == "line 2"

    def test_location_omitted_when_none(self) -> None:
        detail = ErrorDetail(reason="r", message="m")
        assert "location" not in detail.to_dict()
        assert "locationType" not in detail.to_dict()

    def test_location_included_when_present(self) -> None:
        detail = ErrorDetail(
            reason="r",
            message="m",
            location="col:5",
            location_type="query",
        )
        data = detail.to_dict()
        assert data["location"] == "col:5"
        assert data["locationType"] == "query"


class TestHttpStatusCodes:
    @pytest.mark.parametrize(
        ("error_cls", "expected_http", "expected_grpc"),
        [
            (InvalidQueryError, 400, "INVALID_ARGUMENT"),
            (ValidationError, 400, "INVALID_ARGUMENT"),
            (NotFoundError, 404, "NOT_FOUND"),
            (AlreadyExistsError, 409, "ALREADY_EXISTS"),
            (PermissionDeniedError, 403, "PERMISSION_DENIED"),
            (QuotaExceededError, 429, "RESOURCE_EXHAUSTED"),
            (UnsupportedFeatureError, 501, "UNIMPLEMENTED"),
            (InternalError, 500, "INTERNAL"),
        ],
    )
    def test_http_and_grpc_mapping(
        self,
        error_cls: type[DomainError],
        expected_http: int,
        expected_grpc: str,
    ) -> None:
        err = error_cls("message")
        assert err.http_status == expected_http
        assert err.grpc_status_name == expected_grpc


class TestResourceRef:
    def test_dataset_formatting(self) -> None:
        ref = ResourceRef("dataset", "proj", "sales")
        assert ref.format() == "dataset:proj.sales"

    def test_table_formatting(self) -> None:
        ref = ResourceRef("table", "proj", "sales", "orders")
        assert ref.format() == "table:proj.sales.orders"

    def test_project_only_formatting(self) -> None:
        ref = ResourceRef("project", "proj")
        assert ref.format() == "project:proj"

    def test_job_formatting_uses_resource_id_without_dataset(self) -> None:
        ref = ResourceRef("job", "proj", resource_id="job-123")
        # dataset_id is None, but resource_id is rendered after project
        assert ref.format() == "job:proj.job-123"


class TestResourceHelpers:
    def test_resource_not_found_raises_not_found_error(self) -> None:
        err = resource_not_found(ResourceRef("dataset", "proj", "sales"))
        assert isinstance(err, NotFoundError)
        assert "Not found" in err.message
        assert "proj" in err.message and "sales" in err.message

    def test_resource_already_exists_raises_conflict(self) -> None:
        err = resource_already_exists(ResourceRef("table", "proj", "sales", "orders"))
        assert isinstance(err, AlreadyExistsError)
        assert "Already Exists" in err.message
