"""Unit + in-process tests for the ``CREATE MODEL`` statement (RFC 0002 / ADR 0047).

Covers the parser / OPTIONS-validation surface (pure functions), the
StandardSQL feature/label-column derivation, and the end-to-end registration
path against a real DuckDB engine (no mocking, per the project's testing
contract): standalone and scripted ``CREATE MODEL``, the
``CREATE`` / ``REPLACE`` / ``SKIP`` dispositions, the error parity
(duplicate 409, missing dataset 404, bad OPTIONS / label 400), the REST shape
of the registered model, and the dry-run no-side-effect guarantee.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from bqemulator.api.dependencies import AppContext
from bqemulator.api.routes.jobs import _dry_run_response
from bqemulator.api.routes.models import _model_to_rest
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta
from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import (
    AlreadyExistsError,
    InvalidQueryError,
    NotFoundError,
    UnsupportedFeatureError,
)
from bqemulator.domain.events import EventBus
from bqemulator.jobs.executor import (
    JOB_RESULTS,
    JOB_SCHEMAS,
    _schema_field_to_standard_sql,
    classify_statement_type,
    execute_query_job,
    parse_create_model,
)
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.identity import CallerIdentity
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit

NOW = datetime(2026, 5, 16, tzinfo=UTC)
CALLER = CallerIdentity(principal="user:anonymous@bqemulator.local", is_authenticated=False)


def _settings() -> Settings:
    """Ephemeral in-memory settings."""
    return Settings(persistence_mode=PersistenceMode.EPHEMERAL, rest_port=0, grpc_port=0)  # type: ignore[arg-type]


@asynccontextmanager
async def _model_ctx() -> AsyncIterator[AppContext]:
    """Yield an in-process ``AppContext`` with dataset ``p.ds`` + table ``p.ds.t``.

    ``t`` carries one column per StandardSQL ``typeKind`` exercised below:
    ``x`` FLOAT64, ``y`` INT64, ``label`` STRING, ``flag`` BOOL.
    """
    settings = _settings()
    engine = DuckDBEngine(settings)
    await engine.start()
    try:
        catalog = MemoryCatalogRepository()
        catalog.create_dataset(
            DatasetMeta(
                project_id="p",
                dataset_id="ds",
                creation_time=NOW,
                last_modified_time=NOW,
                etag="e",
            ),
        )
        engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
        engine.execute(
            'CREATE TABLE "p__ds"."t" AS SELECT '
            "CAST(1.5 AS DOUBLE) AS x, CAST(2 AS BIGINT) AS y, "
            "'a' AS label, TRUE AS flag",
        )
        yield AppContext(
            settings=settings,
            clock=FrozenClock(NOW),
            engine=engine,
            catalog=catalog,
            metrics=MetricsRegistry(),
            events=EventBus(),
            udf_registry=UDFRegistry(settings),
            snapshots=SnapshotManager(
                engine=engine,
                catalog=MemoryCatalogRepository(),
                clock=FrozenClock(NOW),
                events=EventBus(),
                retention_days=7,
            ),
            row_access=RowAccessPolicyManager(catalog=catalog, clock=FrozenClock(NOW)),
        )
    finally:
        await engine.stop()


class TestClassifyAndParse:
    """Statement classification + OPTIONS parsing/validation (pure)."""

    @pytest.mark.parametrize(
        "sql",
        [
            "CREATE MODEL ds.m OPTIONS(model_type='linear_reg') AS SELECT 1 AS a",
            "create or replace model ds.m options(model_type='kmeans') as select 1 as a",
            "CREATE MODEL IF NOT EXISTS ds.m OPTIONS(model_type='x') AS SELECT 1 AS a",
        ],
    )
    def test_classify_create_model(self, sql: str) -> None:
        """Every CREATE MODEL spelling classifies as ``CREATE_MODEL``."""
        assert classify_statement_type(sql) == "CREATE_MODEL"

    def test_parse_basic(self) -> None:
        """A basic statement yields the target, options, and training query."""
        req = parse_create_model(
            "CREATE MODEL ds.m OPTIONS(model_type='linear_reg', input_label_cols=['y']) "
            "AS SELECT x, y FROM ds.t",
        )
        assert req is not None
        assert (req.project_id, req.dataset_id, req.model_id) == (None, "ds", "m")
        assert req.model_type == "linear_reg"
        assert req.label_cols == ("y",)
        assert req.select_sql == "SELECT x, y FROM ds.t"
        assert (req.replace, req.if_not_exists) == (False, False)

    def test_parse_flags_and_project(self) -> None:
        """``OR REPLACE`` / ``IF NOT EXISTS`` / explicit project are captured."""
        replaced = parse_create_model(
            "CREATE OR REPLACE MODEL `proj-1`.ds.m OPTIONS(model_type='x') AS SELECT 1 AS a",
        )
        assert replaced is not None
        assert (replaced.project_id, replaced.replace) == ("proj-1", True)
        skipped = parse_create_model(
            "CREATE MODEL IF NOT EXISTS ds.m OPTIONS(model_type='x') AS SELECT 1 AS a",
        )
        assert skipped is not None
        assert skipped.if_not_exists is True

    @pytest.mark.parametrize(
        "sql",
        ["SELECT 1", "CREATE TABLE ds.t AS SELECT 1 AS a", "DROP TABLE ds.t"],
    )
    def test_parse_returns_none_for_non_model(self, sql: str) -> None:
        """A non-``CREATE MODEL`` statement returns ``None`` (defers to the normal path)."""
        assert parse_create_model(sql) is None

    @pytest.mark.parametrize(
        ("sql", "match"),
        [
            (
                "CREATE MODEL ds.m OPTIONS(input_label_cols=['y']) AS SELECT y FROM ds.t",
                "model_type",
            ),
            ("CREATE MODEL ds.m AS SELECT 1 AS a", "OPTIONS"),
            (
                "CREATE MODEL ds.m OPTIONS(model_type='x', bogus=1) AS SELECT 1 AS a",
                "Unknown CREATE MODEL option",
            ),
            ("CREATE MODEL m OPTIONS(model_type='x') AS SELECT 1 AS a", "dataset-qualified"),
            ("CREATE MODEL ds.m OPTIONS(model_type='x')", "query_statement"),
            ("CREATE MODEL ds.m OPTIONS(model_type=1) AS SELECT 1 AS a", "string literal"),
            (
                "CREATE MODEL ds.m OPTIONS(model_type='x', input_label_cols='y') AS SELECT 1 AS a",
                "array of strings",
            ),
            (
                "CREATE MODEL ds.m OPTIONS(model_type='x', input_label_cols=[1]) AS SELECT 1 AS a",
                "string literals",
            ),
        ],
    )
    def test_parse_rejects_invalid(self, sql: str, match: str) -> None:
        """Malformed target / OPTIONS raise ``InvalidQueryError`` with a clear message."""
        with pytest.raises(InvalidQueryError, match=match):
            parse_create_model(sql)

    def test_transform_clause_unsupported(self) -> None:
        """The out-of-scope ``TRANSFORM()`` clause raises ``UnsupportedFeatureError``."""
        with pytest.raises(UnsupportedFeatureError, match="TRANSFORM"):
            parse_create_model(
                "CREATE MODEL ds.m TRANSFORM(ML.STANDARD_SCALER(x) OVER() AS x) "
                "OPTIONS(model_type='linear_reg') AS SELECT x FROM ds.t",
            )

    @pytest.mark.parametrize(
        "option",
        [
            "max_iterations=20",
            "num_clusters=3",
            "data_split_method='AUTO_SPLIT'",
            "hidden_units=[32, 16]",
            "time_series_data_col='v'",
        ],
    )
    def test_known_options_accepted(self, option: str) -> None:
        """Documented training OPTIONS across model types are recognised (no error)."""
        req = parse_create_model(
            f"CREATE MODEL ds.m OPTIONS(model_type='x', {option}) AS SELECT 1 AS a",
        )
        assert req is not None


class TestStandardSqlFieldMapping:
    """Feature/label column rendering as ``StandardSqlField`` dicts (pure)."""

    @pytest.mark.parametrize(
        ("bq_type", "type_kind"),
        [
            ("INTEGER", "INT64"),
            ("FLOAT", "FLOAT64"),
            ("BOOLEAN", "BOOL"),
            ("NUMERIC", "NUMERIC"),
        ],
    )
    def test_scalar_type_kinds(self, bq_type: str, type_kind: str) -> None:
        """Legacy REST scalar types map to their StandardSQL ``typeKind``."""
        field = _schema_field_to_standard_sql({"name": "c", "type": bq_type, "mode": "NULLABLE"})
        assert field == {"name": "c", "type": {"typeKind": type_kind}}

    def test_repeated_becomes_array(self) -> None:
        """A REPEATED column renders as ``ARRAY`` of the element type."""
        field = _schema_field_to_standard_sql(
            {"name": "tags", "type": "STRING", "mode": "REPEATED"}
        )
        assert field == {
            "name": "tags",
            "type": {"typeKind": "ARRAY", "arrayElementType": {"typeKind": "STRING"}},
        }

    def test_record_becomes_struct(self) -> None:
        """A RECORD column renders as ``STRUCT`` with nested fields."""
        field = _schema_field_to_standard_sql(
            {
                "name": "addr",
                "type": "RECORD",
                "mode": "NULLABLE",
                "fields": [{"name": "zip", "type": "STRING", "mode": "NULLABLE"}],
            },
        )
        assert field["type"]["typeKind"] == "STRUCT"
        assert field["type"]["structType"]["fields"] == [
            {"name": "zip", "type": {"typeKind": "STRING"}},
        ]


class TestCreateModelEndToEnd:
    """Registration path against a real DuckDB engine."""

    async def test_basic_registration(self) -> None:
        """A standalone CREATE MODEL registers metadata and reports CREATE_MODEL."""
        async with _model_ctx() as ctx:
            meta = await execute_query_job(
                "p",
                "j-create",
                "CREATE MODEL ds.m OPTIONS(model_type='linear_reg', input_label_cols=['label']) "
                "AS SELECT x, y, label, flag FROM ds.t",
                None,
                ctx,
            )
            query_stats = meta.statistics["query"]
            assert query_stats["statementType"] == "CREATE_MODEL"
            assert query_stats["ddlOperationPerformed"] == "CREATE"
            assert query_stats["totalRows"] == "0"
            assert JOB_RESULTS["j-create"].num_rows == 0
            assert JOB_SCHEMAS["j-create"] == []

            model = ctx.catalog.get_model("p", "ds", "m")
            assert model is not None
            assert model.model_type == "linear_reg"
            assert model.training_query == "SELECT x, y, label, flag FROM ds.t"
            # Features keep training-query order; label is split out by name.
            assert model.feature_columns == (
                {"name": "x", "type": {"typeKind": "FLOAT64"}},
                {"name": "y", "type": {"typeKind": "INT64"}},
                {"name": "flag", "type": {"typeKind": "BOOL"}},
            )
            assert model.label_columns == ({"name": "label", "type": {"typeKind": "STRING"}},)

            # REST shape carries identity + columns but hides the provenance.
            rest = _model_to_rest(model)
            assert rest["modelReference"] == {"projectId": "p", "datasetId": "ds", "modelId": "m"}
            assert rest["labelColumns"] == [{"name": "label", "type": {"typeKind": "STRING"}}]
            assert "training_query" not in rest
            assert "kind" not in rest

    async def test_no_label_cols_all_features(self) -> None:
        """Without ``input_label_cols`` every output column is a feature."""
        async with _model_ctx() as ctx:
            await execute_query_job(
                "p",
                "j-nolabel",
                "CREATE MODEL ds.m OPTIONS(model_type='x') AS SELECT x, y FROM ds.t",
                None,
                ctx,
            )
            rest = _model_to_rest(ctx.catalog.get_model("p", "ds", "m"))
            assert rest["featureColumns"] == [
                {"name": "x", "type": {"typeKind": "FLOAT64"}},
                {"name": "y", "type": {"typeKind": "INT64"}},
            ]
            assert "labelColumns" not in rest  # no input_label_cols → all features

    async def test_duplicate_without_flags_conflicts(self) -> None:
        """A second CREATE MODEL onto the same model raises AlreadyExists (409)."""
        async with _model_ctx() as ctx:
            sql = "CREATE MODEL ds.m OPTIONS(model_type='x') AS SELECT x FROM ds.t"
            await execute_query_job("p", "j-a", sql, None, ctx)
            with pytest.raises(AlreadyExistsError):
                await execute_query_job("p", "j-b", sql, None, ctx)

    async def test_if_not_exists_skips(self) -> None:
        """``IF NOT EXISTS`` on an existing model is a no-op (no retrain, SKIP)."""
        async with _model_ctx() as ctx:
            await execute_query_job(
                "p",
                "j-1",
                "CREATE MODEL ds.m OPTIONS(model_type='linear_reg') AS SELECT x FROM ds.t",
                None,
                ctx,
            )
            meta = await execute_query_job(
                "p",
                "j-2",
                "CREATE MODEL IF NOT EXISTS ds.m OPTIONS(model_type='kmeans') "
                "AS SELECT y FROM ds.t",
                None,
                ctx,
            )
            assert meta.statistics["query"]["ddlOperationPerformed"] == "SKIP"
            # Original model is untouched.
            assert ctx.catalog.get_model("p", "ds", "m").model_type == "linear_reg"

    async def test_or_replace_replaces(self) -> None:
        """``CREATE OR REPLACE`` overwrites the model and reports REPLACE."""
        async with _model_ctx() as ctx:
            await execute_query_job(
                "p",
                "j-1",
                "CREATE MODEL ds.m OPTIONS(model_type='linear_reg', input_label_cols=['label']) "
                "AS SELECT x, label FROM ds.t",
                None,
                ctx,
            )
            meta = await execute_query_job(
                "p",
                "j-2",
                "CREATE OR REPLACE MODEL ds.m OPTIONS(model_type='kmeans') "
                "AS SELECT x, y FROM ds.t",
                None,
                ctx,
            )
            assert meta.statistics["query"]["ddlOperationPerformed"] == "REPLACE"
            model = ctx.catalog.get_model("p", "ds", "m")
            assert model.model_type == "kmeans"
            assert [c["name"] for c in model.feature_columns] == ["x", "y"]
            assert model.label_columns == ()

    async def test_or_replace_on_fresh_is_create(self) -> None:
        """``OR REPLACE`` on a non-existent model registers it (CREATE)."""
        async with _model_ctx() as ctx:
            meta = await execute_query_job(
                "p",
                "j-fresh",
                "CREATE OR REPLACE MODEL ds.m OPTIONS(model_type='x') AS SELECT x FROM ds.t",
                None,
                ctx,
            )
            assert meta.statistics["query"]["ddlOperationPerformed"] == "CREATE"
            assert ctx.catalog.get_model("p", "ds", "m") is not None

    async def test_missing_dataset_not_found(self) -> None:
        """A model in an absent dataset raises NotFound (404) before the query runs."""
        async with _model_ctx() as ctx:
            with pytest.raises(NotFoundError):
                await execute_query_job(
                    "p",
                    "j-no-ds",
                    "CREATE MODEL nope.m OPTIONS(model_type='x') AS SELECT 1 AS a",
                    None,
                    ctx,
                )

    async def test_label_col_not_in_output_rejected(self) -> None:
        """A label column absent from the training query output raises 400."""
        async with _model_ctx() as ctx:
            with pytest.raises(InvalidQueryError, match="input_label_cols not found"):
                await execute_query_job(
                    "p",
                    "j-bad-label",
                    "CREATE MODEL ds.m OPTIONS(model_type='x', input_label_cols=['zzz']) "
                    "AS SELECT x FROM ds.t",
                    None,
                    ctx,
                )


class TestScriptedCreateModel:
    """``CREATE MODEL`` inside a multi-statement script (the interpreter path)."""

    async def test_create_model_inside_script(self) -> None:
        """A scripted CREATE MODEL registers via the shared dual-wired helper."""
        async with _model_ctx() as ctx:
            await execute_query_job(
                "p",
                "j-script",
                "BEGIN\n"
                "  CREATE MODEL ds.scripted OPTIONS(model_type='linear_reg', "
                "input_label_cols=['label']) AS SELECT x, label FROM ds.t;\n"
                "END",
                None,
                ctx,
            )
            model = ctx.catalog.get_model("p", "ds", "scripted")
            assert model is not None
            assert [c["name"] for c in model.feature_columns] == ["x"]
            assert [c["name"] for c in model.label_columns] == ["label"]

    async def test_scripted_if_not_exists_skips(self) -> None:
        """A scripted ``IF NOT EXISTS`` on an existing model leaves it unchanged."""
        async with _model_ctx() as ctx:
            await execute_query_job(
                "p",
                "j-1",
                "CREATE MODEL ds.m OPTIONS(model_type='linear_reg') AS SELECT x FROM ds.t",
                None,
                ctx,
            )
            await execute_query_job(
                "p",
                "j-2",
                "BEGIN\n"
                "  CREATE MODEL IF NOT EXISTS ds.m OPTIONS(model_type='kmeans') "
                "AS SELECT y FROM ds.t;\n"
                "END",
                None,
                ctx,
            )
            assert ctx.catalog.get_model("p", "ds", "m").model_type == "linear_reg"

    async def test_scripted_duplicate_raises(self) -> None:
        """A scripted duplicate without flags raises AlreadyExists (409)."""
        async with _model_ctx() as ctx:
            await execute_query_job(
                "p",
                "j-1",
                "CREATE MODEL ds.m OPTIONS(model_type='x') AS SELECT x FROM ds.t",
                None,
                ctx,
            )
            with pytest.raises(AlreadyExistsError):
                await execute_query_job(
                    "p",
                    "j-2",
                    "BEGIN\n  CREATE MODEL ds.m OPTIONS(model_type='x') "
                    "AS SELECT x FROM ds.t;\nEND",
                    None,
                    ctx,
                )


class TestDryRun:
    """A dry-run ``CREATE MODEL`` previews the classification without side effects."""

    async def test_dry_run_does_not_register(self) -> None:
        """Dry-run reports ``CREATE_MODEL`` and registers nothing."""
        async with _model_ctx() as ctx:
            body = await _dry_run_response(
                project_id="p",
                job_id="j-dry",
                bq_sql="CREATE MODEL ds.m OPTIONS(model_type='x') AS SELECT x FROM ds.t",
                query_params=None,
                ctx=ctx,
                caller=CALLER,
                wire_shape="query",
            )
            assert body["statementType"] == "CREATE_MODEL"
            assert body["ddlOperationPerformed"] == "CREATE"
            assert body["schema"] == {"fields": []}
            assert ctx.catalog.get_model("p", "ds", "m") is None
