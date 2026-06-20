"""Characterization tests for ``_run_single_sql``'s pre-execution error boundary.

``_run_single_sql`` runs the rewrite + translate chain, then qualifies
table references, binds parameters, and executes. Its error boundary is
deliberate, and these tests pin both halves:

* a pre-execution domain error from table-reference qualification
  (``rewrite_table_refs``) is reshaped by ``translate_runtime_error``
  into BigQuery's wire form, so qualification runs *inside* the
  execution ``try``; and
* a translation failure (an unsupported feature) surfaces *unwrapped*,
  keeping its own ``501`` reason, so translation is raised *before* the
  ``try`` (routing it through the mapper would rewrap it into a generic
  ``invalidQuery``).

Both behaviours share lines with the happy path, so line coverage does
not pin them; the assertions here do.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, TableMeta
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import (
    InvalidQueryError,
    UnsupportedFeatureError,
    ValidationError,
)
from bqemulator.domain.events import EventBus
from bqemulator.jobs import executor
from bqemulator.jobs.executor import _run_single_sql
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.identity import CallerIdentity
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 16, tzinfo=UTC)
_ANON = CallerIdentity(principal="user:anon@bqemulator.local", is_authenticated=False)


@pytest_asyncio.fixture
async def ctx(ephemeral_settings: Settings) -> AsyncIterator[AppContext]:
    """In-process ``AppContext`` with a translatable table ``p.ds.t``."""
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    catalog = MemoryCatalogRepository()
    clock = FrozenClock(_NOW)
    events = EventBus()
    catalog.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="ds",
            creation_time=_NOW,
            last_modified_time=_NOW,
            etag=generate_etag("p", "ds", str(_NOW)),
        ),
    )
    catalog.create_table(
        TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            table_type="TABLE",
            creation_time=_NOW,
            last_modified_time=_NOW,
            etag=generate_etag("p", "ds", "t", str(_NOW)),
        ),
    )
    engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
    engine.execute('CREATE TABLE "p__ds"."t" (id BIGINT)')
    context = AppContext(
        settings=ephemeral_settings,
        clock=clock,
        engine=engine,
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=events,
        udf_registry=None,
        snapshots=SnapshotManager(
            engine=engine,
            catalog=catalog,
            clock=clock,
            events=events,
            retention_days=7,
        ),
        row_access=RowAccessPolicyManager(catalog=catalog, clock=clock),
    )
    try:
        yield context
    finally:
        await engine.stop()


async def test_qualification_validation_error_is_reshaped(
    ctx: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``ValidationError`` from ``rewrite_table_refs`` is mapped to BQ shape.

    Qualification runs inside the execution ``try``, so a raw
    ``ValidationError`` is reshaped rather than escaping unmapped.
    """

    def _raise_validation(*_args: object, **_kwargs: object) -> str:
        raise ValidationError("Invalid dataset id for SQL: 'bad.dataset'")

    monkeypatch.setattr(executor, "rewrite_table_refs", _raise_validation)

    with pytest.raises(InvalidQueryError) as excinfo:
        await _run_single_sql("p", "SELECT id FROM ds.t", None, ctx, caller=_ANON)

    # Reshaped to the BigQuery "Function not found" envelope, not the raw
    # ValidationError that rewrite_table_refs raised.
    assert "Function not found" in str(excinfo.value)
    assert not isinstance(excinfo.value, ValidationError)


async def test_unsupported_feature_translation_error_surfaces_unwrapped(
    ctx: AppContext,
) -> None:
    """An unsupported-feature translation error keeps its ``501`` type.

    Translation is raised before the execution ``try``, so an
    ``UnsupportedFeatureError`` surfaces unwrapped rather than being
    rewrapped by ``translate_runtime_error`` into a generic
    ``InvalidQueryError`` (``400``).
    """
    with pytest.raises(UnsupportedFeatureError):
        await _run_single_sql(
            "p",
            "SELECT * FROM ML.PREDICT(MODEL m, TABLE t)",
            None,
            ctx,
            caller=_ANON,
        )
