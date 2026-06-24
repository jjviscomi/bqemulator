"""Tests for catalog models — ensure BigQuery-shape invariants hold."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.models import (
    DatasetMeta,
    JobMeta,
    ModelMeta,
    RoutineArgument,
    RoutineMeta,
    TableFieldSchema,
    TableMeta,
    TableSchema,
    TimePartitioning,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 4, 15, tzinfo=UTC)


class TestTableFieldSchema:
    def test_basic_field(self) -> None:
        f = TableFieldSchema(name="id", type="INT64", mode="REQUIRED")
        assert f.name == "id"
        assert f.type == "INT64"
        assert f.mode == "REQUIRED"
        assert f.fields == ()

    def test_nested_struct(self) -> None:
        address = TableFieldSchema(
            name="address",
            type="STRUCT",
            fields=(
                TableFieldSchema(name="street", type="STRING"),
                TableFieldSchema(name="city", type="STRING"),
            ),
        )
        assert len(address.fields) == 2
        assert address.fields[0].name == "street"

    def test_field_is_frozen(self) -> None:
        f = TableFieldSchema(name="x", type="INT64")
        with pytest.raises(Exception):
            f.name = "y"  # type: ignore[misc]


class TestTableSchema:
    def test_empty_default(self) -> None:
        assert TableSchema().fields == ()

    def test_multi_field(self) -> None:
        s = TableSchema(
            fields=(
                TableFieldSchema(name="id", type="INT64", mode="REQUIRED"),
                TableFieldSchema(name="amount", type="NUMERIC"),
            ),
        )
        assert len(s.fields) == 2


class TestDatasetMeta:
    def test_minimal(self) -> None:
        d = DatasetMeta(
            project_id="p",
            dataset_id="sales",
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e1",
        )
        assert d.location == "US"
        assert d.labels == {}

    def test_labels_and_description(self) -> None:
        d = DatasetMeta(
            project_id="p",
            dataset_id="sales",
            description="customer orders",
            labels={"team": "data"},
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e1",
        )
        assert d.description == "customer orders"
        assert d.labels == {"team": "data"}


class TestTableMeta:
    def test_minimal(self) -> None:
        t = TableMeta(
            project_id="p",
            dataset_id="sales",
            table_id="orders",
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e1",
        )
        assert t.table_type == "TABLE"
        assert t.schema_.fields == ()
        assert t.num_rows == 0

    def test_partitioned_and_clustered(self) -> None:
        t = TableMeta(
            project_id="p",
            dataset_id="sales",
            table_id="orders",
            time_partitioning=TimePartitioning(type="DAY", field="placed_at"),
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e1",
        )
        assert t.time_partitioning is not None
        assert t.time_partitioning.field == "placed_at"


class TestRoutineMeta:
    def test_sql_udf(self) -> None:
        r = RoutineMeta(
            project_id="p",
            dataset_id="utils",
            routine_id="SafeDivide",
            routine_type="SCALAR_FUNCTION",
            language="SQL",
            definition_body="IF(b = 0, NULL, a / b)",
            arguments=(
                RoutineArgument(name="a", data_type={"typeKind": "FLOAT64"}),
                RoutineArgument(name="b", data_type={"typeKind": "FLOAT64"}),
            ),
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e1",
        )
        assert r.language == "SQL"
        assert len(r.arguments) == 2


class TestModelMeta:
    def test_minimal_defaults(self) -> None:
        m = ModelMeta(
            project_id="p",
            dataset_id="ml",
            model_id="churn",
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e1",
        )
        assert m.model_type == "MODEL_TYPE_UNSPECIFIED"
        assert m.location == "US"
        assert m.labels == {}
        assert m.feature_columns == ()
        assert m.label_columns == ()
        assert m.expiration_time is None
        assert m.encryption_configuration is None
        assert m.training_query is None

    def test_full_surface_fields(self) -> None:
        m = ModelMeta(
            project_id="p",
            dataset_id="ml",
            model_id="churn",
            model_type="LOGISTIC_REGRESSION",
            friendly_name="Churn",
            description="surface-only model",
            labels={"team": "ds"},
            feature_columns=({"name": "tenure", "type": {"typeKind": "FLOAT64"}},),
            label_columns=({"name": "churned", "type": {"typeKind": "BOOL"}},),
            encryption_configuration={"kmsKeyName": "projects/p/keys/k"},
            training_query="SELECT tenure, churned FROM p.ml.customers",
            expiration_time=NOW,
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e1",
        )
        assert m.model_type == "LOGISTIC_REGRESSION"
        assert m.feature_columns[0]["name"] == "tenure"
        assert m.label_columns[0]["type"] == {"typeKind": "BOOL"}
        assert m.training_query == "SELECT tenure, churned FROM p.ml.customers"


class TestJobMeta:
    def test_query_job_shape(self) -> None:
        j = JobMeta(
            project_id="p",
            job_id="j-1",
            job_type="QUERY",
            state="DONE",
            configuration={"query": {"query": "SELECT 1"}},
            statistics={"totalBytesProcessed": 0},
            creation_time=NOW,
            start_time=NOW,
            end_time=NOW,
            etag="e1",
        )
        assert j.job_type == "QUERY"
        assert j.state == "DONE"
        assert j.error_result is None
