"""Unit tests for the shared inner-query rewrite + translation pipeline.

The pipeline (:mod:`bqemulator.sql.inner_query`) is the single rewrite
chain shared by standalone single-statement jobs and the scripting
interpreter. End-to-end behaviour (MV refresh, time-travel, row-access)
is pinned by the integration suites; these tests cover the module's own
branches: the parse-failure short-circuit in
:func:`refresh_dependent_mvs` and the translation-error path in
:func:`rewrite_and_translate_statement`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, MaterializedViewMeta, TableMeta
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import InvalidQueryError
from bqemulator.domain.events import EventBus
from bqemulator.domain.result import Err, Ok
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.identity import CallerIdentity
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.sql.inner_query import (
    refresh_dependent_mvs,
    rewrite_and_translate_statement,
)
from bqemulator.sql.table_rewriter import rewrite_table_refs
from bqemulator.sql.translator import SQLTranslator
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.storage.sql_identifiers import quoted_schema, quoted_table_ref
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.materialized_views import MaterializedViewManager
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit

_ANON = CallerIdentity(principal="user:anon@bqemulator.local", is_authenticated=False)


@pytest_asyncio.fixture
async def ctx(
    ephemeral_settings: Settings,
    frozen_clock: FrozenClock,
) -> AsyncIterator[AppContext]:
    """A minimal :class:`AppContext` backed by a real DuckDB engine."""
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    catalog = MemoryCatalogRepository()
    events = EventBus()
    snapshots = SnapshotManager(
        engine=engine,
        catalog=catalog,
        clock=frozen_clock,
        events=events,
        retention_days=ephemeral_settings.time_travel_retention_days,
    )
    context = AppContext(
        settings=ephemeral_settings,
        clock=frozen_clock,
        engine=engine,
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=events,
        udf_registry=UDFRegistry(ephemeral_settings),
        snapshots=snapshots,
        row_access=RowAccessPolicyManager(catalog=catalog, clock=frozen_clock),
    )
    try:
        yield context
    finally:
        await engine.stop()


def _ensure_dataset(ctx: AppContext, frozen_clock: FrozenClock) -> None:
    """Idempotently create dataset ``p.ds`` and its DuckDB schema."""
    if ctx.catalog.get_dataset("p", "ds") is not None:
        return
    now = frozen_clock.now()
    ctx.catalog.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="ds",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag("p", "ds", str(now)),
        ),
    )
    ctx.engine.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema('p', 'ds')}")


def _seed_table(ctx: AppContext, frozen_clock: FrozenClock) -> None:
    """Create a plain ``p.ds.t`` table with one row."""
    _ensure_dataset(ctx, frozen_clock)
    now = frozen_clock.now()
    ctx.catalog.create_table(
        TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            table_type="TABLE",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag("p", "ds", "t", str(now)),
        ),
    )
    ctx.engine.execute(f"CREATE TABLE {quoted_table_ref('p', 'ds', 't')} (id BIGINT)")
    ctx.engine.execute(f"INSERT INTO {quoted_table_ref('p', 'ds', 't')} VALUES (1)")


def _register_mv(ctx: AppContext, frozen_clock: FrozenClock) -> None:
    """Register materialized view ``p.ds.mv`` the way production does.

    ``MaterializedViewManager.create`` writes BOTH a ``TableMeta`` with
    ``table_type="MATERIALIZED_VIEW"`` (so ``get_table`` resolves it) AND
    a ``MaterializedViewMeta`` (so ``list_all_materialized_views`` sees
    it). Mirroring both is what lets ``refresh_dependent_mvs`` classify a
    reference to ``ds.mv`` as a view and yield it for refresh.
    """
    _ensure_dataset(ctx, frozen_clock)
    now = frozen_clock.now()
    ctx.catalog.create_table(
        TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="mv",
            table_type="MATERIALIZED_VIEW",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag("p", "ds", "mv", str(now)),
        ),
    )
    ctx.catalog.upsert_materialized_view(
        MaterializedViewMeta(
            project_id="p",
            dataset_id="ds",
            table_id="mv",
            view_query="SELECT 1 AS id",
            base_tables=(),
            last_refresh_time=now,
        ),
    )


@pytest.fixture
def refresh_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Record each ``refresh_if_stale`` call instead of performing it.

    Returns the list of ``(project, dataset, table)`` tuples the walk
    asked to refresh, so tests can assert exactly which views (and how
    many times) the chain refreshes.
    """
    calls: list[tuple[str, ...]] = []

    async def _spy(_self: object, *ref: str) -> None:
        calls.append(ref)

    monkeypatch.setattr(MaterializedViewManager, "refresh_if_stale", _spy)
    return calls


async def test_refresh_dependent_mvs_short_circuits_without_views(
    ctx: AppContext,
    refresh_calls: list[tuple[str, ...]],
) -> None:
    """With no materialized views in the catalog, the walk is skipped entirely."""
    # No MV registered: returns before parsing, so even malformed SQL is a no-op.
    await refresh_dependent_mvs("p", "this is not valid sql @@@", ctx)
    assert refresh_calls == []


async def test_refresh_dependent_mvs_ignores_unparseable_sql(
    ctx: AppContext,
    frozen_clock: FrozenClock,
    refresh_calls: list[tuple[str, ...]],
) -> None:
    """Unparseable SQL refreshes nothing; later pipeline layers surface the error."""
    _register_mv(ctx, frozen_clock)
    await refresh_dependent_mvs("p", "this is not valid sql @@@", ctx)
    assert refresh_calls == []


async def test_refresh_dependent_mvs_skips_plain_table(
    ctx: AppContext,
    frozen_clock: FrozenClock,
    refresh_calls: list[tuple[str, ...]],
) -> None:
    """A query over a non-materialized-view table triggers no refresh."""
    _seed_table(ctx, frozen_clock)
    _register_mv(ctx, frozen_clock)
    await refresh_dependent_mvs("p", "SELECT id FROM ds.t", ctx)
    assert refresh_calls == []


async def test_refresh_dependent_mvs_skips_unqualified_refs(
    ctx: AppContext,
    frozen_clock: FrozenClock,
    refresh_calls: list[tuple[str, ...]],
) -> None:
    """A bare table reference (e.g. a CTE) has no dataset and is skipped."""
    _register_mv(ctx, frozen_clock)
    await refresh_dependent_mvs("p", "WITH cte AS (SELECT 1 AS id) SELECT id FROM cte", ctx)
    assert refresh_calls == []


async def test_refresh_dependent_mvs_skips_anonymous_table_function(
    ctx: AppContext,
    frozen_clock: FrozenClock,
    refresh_calls: list[tuple[str, ...]],
) -> None:
    """A table-function call (``Anonymous`` node) is skipped, not treated as a table."""
    _register_mv(ctx, frozen_clock)
    # ``ds.tvf(1)`` parses as a Table whose ``this`` is an Anonymous call.
    await refresh_dependent_mvs("p", "SELECT * FROM ds.tvf(1)", ctx)
    assert refresh_calls == []


async def test_refresh_dependent_mvs_refreshes_referenced_view(
    ctx: AppContext,
    frozen_clock: FrozenClock,
    refresh_calls: list[tuple[str, ...]],
) -> None:
    """A query reading a materialized view refreshes exactly that view."""
    _register_mv(ctx, frozen_clock)
    await refresh_dependent_mvs("p", "SELECT id FROM ds.mv", ctx)
    assert refresh_calls == [("p", "ds", "mv")]


async def test_refresh_dependent_mvs_dedups_repeated_refs(
    ctx: AppContext,
    frozen_clock: FrozenClock,
    refresh_calls: list[tuple[str, ...]],
) -> None:
    """A view referenced more than once (self-join) is refreshed exactly once."""
    _register_mv(ctx, frozen_clock)
    # ``ds.mv`` appears twice; the second occurrence hits the dedup guard.
    await refresh_dependent_mvs(
        "p",
        "SELECT a.id FROM ds.mv AS a JOIN ds.mv AS b ON a.id = b.id",
        ctx,
    )
    assert refresh_calls == [("p", "ds", "mv")]


async def test_rewrite_and_translate_statement_returns_duckdb_sql(
    ctx: AppContext,
    frozen_clock: FrozenClock,
) -> None:
    """The happy path returns translated DuckDB SQL the caller can qualify and run."""
    _seed_table(ctx, frozen_clock)
    translated = await rewrite_and_translate_statement(
        "SELECT id FROM ds.t",
        project_id="p",
        ctx=ctx,
        caller=_ANON,
        translator=SQLTranslator(),
    )
    # The helper translates; the caller qualifies table refs, after which it runs.
    duckdb_sql = rewrite_table_refs(translated, "p")
    assert quoted_table_ref("p", "ds", "t") in duckdb_sql
    assert ctx.engine.fetch_arrow(duckdb_sql).to_pylist() == [{"id": 1}]


async def test_rewrite_and_translate_statement_raises_translator_error(
    ctx: AppContext,
    frozen_clock: FrozenClock,
) -> None:
    """A failed translation propagates the translator's error verbatim."""
    _seed_table(ctx, frozen_clock)
    sentinel = InvalidQueryError("boom")

    class _FailingTranslator:
        def translate(self, *_args: object, **_kwargs: object) -> Err:
            return Err(sentinel)

    with pytest.raises(InvalidQueryError) as excinfo:
        await rewrite_and_translate_statement(
            "SELECT id FROM ds.t",
            project_id="p",
            ctx=ctx,
            caller=_ANON,
            translator=_FailingTranslator(),  # type: ignore[arg-type]
        )
    assert excinfo.value is sentinel


async def test_rewrite_and_translate_statement_ok_branch_with_stub(
    ctx: AppContext,
    frozen_clock: FrozenClock,
) -> None:
    """The Ok branch returns the translator's output unchanged (no qualification)."""
    _seed_table(ctx, frozen_clock)

    class _OkTranslator:
        def translate(self, *_args: object, **_kwargs: object) -> Ok:
            return Ok("SELECT id FROM p.ds.t")

    result = await rewrite_and_translate_statement(
        "SELECT id FROM ds.t",
        project_id="p",
        ctx=ctx,
        caller=_ANON,
        translator=_OkTranslator(),  # type: ignore[arg-type]
    )
    # Table-ref qualification is the caller's step, so the helper returns
    # the translated SQL verbatim.
    assert result == "SELECT id FROM p.ds.t"
