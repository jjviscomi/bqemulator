"""In-process tests for ``ML.PREDICT`` execution (RFC 0002 / ADR 0047).

Exercises the surface-only ``ML.PREDICT`` path end-to-end against a real DuckDB
engine (no mocking, per the project's testing contract): a model registered via
``CREATE MODEL`` is predicted with, and the output is the input rows plus a
deterministic ``predicted_<label>`` stub column (constant ``0.0``). Covers the
subquery and ``TABLE`` input forms, passthrough preservation, the row-count
invariant, model-not-found (404) parity, and the scripted (``BEGIN ... END``)
path that proves the rewrite is dual-wired through the shared inner-query chain.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta
from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import NotFoundError
from bqemulator.domain.events import EventBus
from bqemulator.jobs.executor import JOB_RESULTS, JOB_SCHEMAS, execute_query_job
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit

NOW = datetime(2026, 5, 16, tzinfo=UTC)

#: Register a regressor whose training query yields feature ``x`` + label ``label``.
_CREATE = (
    "CREATE MODEL ds.m OPTIONS(model_type='linear_reg', input_label_cols=['label']) "
    "AS SELECT x, label FROM ds.t"
)


@asynccontextmanager
async def _ctx() -> AsyncIterator[AppContext]:
    """Yield an in-process ``AppContext`` with dataset ``p.ds`` + 3-row table ``p.ds.t``."""
    settings = Settings(persistence_mode=PersistenceMode.EPHEMERAL, rest_port=0, grpc_port=0)  # type: ignore[arg-type]
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
            'CREATE TABLE "p__ds"."t" AS '
            "SELECT CAST(col0 AS DOUBLE) AS x, col1 AS label "
            "FROM (VALUES (1.5, 'a'), (2.5, 'b'), (3.5, 'c'))",
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


async def test_predict_subquery_appends_stub_column() -> None:
    """``ML.PREDICT`` returns input rows plus a constant-0.0 ``predicted_<label>``."""
    async with _ctx() as ctx:
        await execute_query_job("p", "j-create", _CREATE, None, ctx)
        await execute_query_job(
            "p",
            "j-pred",
            "SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT x, label FROM ds.t))",
            None,
            ctx,
        )
        names = [f["name"] for f in JOB_SCHEMAS["j-pred"]]
        result = JOB_RESULTS["j-pred"]
        assert names == ["x", "label", "predicted_label"]
        assert result.num_rows == 3
        assert result.to_pydict()["predicted_label"] == [0.0, 0.0, 0.0]


async def test_predict_table_form_preserves_passthrough() -> None:
    """The ``TABLE ref`` input form preserves every input column plus the stub."""
    async with _ctx() as ctx:
        await execute_query_job("p", "j-create", _CREATE, None, ctx)
        await execute_query_job(
            "p",
            "j-pred",
            "SELECT * FROM ML.PREDICT(MODEL ds.m, TABLE ds.t)",
            None,
            ctx,
        )
        names = [f["name"] for f in JOB_SCHEMAS["j-pred"]]
        assert names == ["x", "label", "predicted_label"]
        assert JOB_RESULTS["j-pred"].num_rows == 3


async def test_predict_predicted_column_type_is_float64() -> None:
    """The appended ``predicted_<label>`` column is ``FLOAT64`` (same as feature ``x``).

    Asserted against the type the emulator reports for the known-``FLOAT64``
    passthrough column ``x`` rather than a hard-coded name, since the REST
    schema uses BigQuery's legacy type spelling (``FLOAT``) for ``FLOAT64``.
    """
    async with _ctx() as ctx:
        await execute_query_job("p", "j-create", _CREATE, None, ctx)
        await execute_query_job(
            "p",
            "j-pred",
            "SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT x, label FROM ds.t))",
            None,
            ctx,
        )
        types = {f["name"]: f["type"] for f in JOB_SCHEMAS["j-pred"]}
        assert types["predicted_label"] == types["x"]


@pytest.mark.parametrize("k", [0, 1, 3])
async def test_predict_row_count_equals_input(k: int) -> None:
    """The output row count equals the input row count (RFC 0002 invariant).

    The 3-row seed table is sliced with ``LIMIT k`` to drive the input size.
    """
    async with _ctx() as ctx:
        await execute_query_job("p", "j-create", _CREATE, None, ctx)
        await execute_query_job(
            "p",
            f"j-pred-{k}",
            f"SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT x, label FROM ds.t LIMIT {k}))",
            None,
            ctx,
        )
        assert JOB_RESULTS[f"j-pred-{k}"].num_rows == k


async def test_predict_missing_model_raises_not_found() -> None:
    """Predicting with an unregistered model surfaces BigQuery's 404 ``notFound``."""
    async with _ctx() as ctx:
        with pytest.raises(NotFoundError, match=r"model:p\.ds\.absent"):
            await execute_query_job(
                "p",
                "j-pred",
                "SELECT * FROM ML.PREDICT(MODEL ds.absent, (SELECT x FROM ds.t))",
                None,
                ctx,
            )


async def test_scripted_predict_is_dual_wired() -> None:
    """A scripted ``ML.PREDICT`` flows through the shared rewrite (404 on missing model)."""
    async with _ctx() as ctx:
        with pytest.raises(NotFoundError, match=r"model:p\.ds\.absent"):
            await execute_query_job(
                "p",
                "j-script",
                "BEGIN\n  SELECT * FROM ML.PREDICT(MODEL ds.absent, (SELECT x FROM ds.t));\nEND",
                None,
                ctx,
            )


async def test_scripted_predict_runs_against_registered_model() -> None:
    """A scripted ``ML.PREDICT`` on a registered model completes without error."""
    async with _ctx() as ctx:
        await execute_query_job("p", "j-create", _CREATE, None, ctx)
        await execute_query_job(
            "p",
            "j-script",
            "BEGIN\n  SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT x, label FROM ds.t));\nEND",
            None,
            ctx,
        )
