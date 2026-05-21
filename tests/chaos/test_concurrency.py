"""Concurrency chaos — closes Phase 10 audit gap #5 + extends gap #4.

Three scenarios, one per class. All use ``threading.Barrier`` /
``threading.Event`` to force deterministic interleaving — no
sleep-based timing.

1. **Stale MV under 100 concurrent readers** (gap #5). Phase 7's
   :class:`MaterializedViewManager` claims "collapse onto a single
   recompute" via an in-write-lock staleness re-check. We property-
   test it: 100 racing readers call ``refresh_if_stale`` after the
   MV is flagged stale; only one CTAS must fire; every reader must
   observe the same fresh result.

2. **High-volume retry storm on COMMITTED streams** (extends gap #4).
   Phase 10's integration suite exercised 32-thread retry storms;
   chaos scales to 1000 simultaneous offset-0 retries to confirm the
   exactly-once invariant holds under realistic client thunder.

3. **Mixed read/write contention on time-travel queries**. Writers
   keep bumping the live table's row count; readers pinned at a
   captured snapshot observe their pinned row count throughout the
   race, while writers observe their own monotonically-increasing
   state.

Determinism: every race is gated by a ``Barrier`` so all worker
threads release simultaneously. The number of OK/duplicates is then a
function of the contention model, not of OS scheduling. See ADR 0021
for the chaos-tier design contract.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import UTC, datetime
import threading

import httpx
import pyarrow as pa
import pytest

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import (
    DatasetMeta,
    MaterializedViewMeta,
    TableFieldSchema,
    TableMeta,
    TableSchema,
)
from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.events import EventBus
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.server import EmulatorServer
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.streaming.strategies import CommittedWriteStrategy
from bqemulator.streaming.strategies.base import AppendStatus
from bqemulator.streaming.write_stream import (
    WriteStream,
    WriteStreamManager,
    WriteStreamType,
)
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.materialized_views import MaterializedViewManager
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.chaos


# ---------------------------------------------------------------------------
# Scenario 1 — Stale MV under 100 concurrent readers.
# ---------------------------------------------------------------------------


def _build_chaos_context(engine: DuckDBEngine) -> AppContext:
    """Construct a minimal :class:`AppContext` for chaos-tier use.

    Chaos concurrency scenarios drive subsystems directly (without the
    REST/gRPC stacks) for deterministic control over the race. This
    helper wires the same composition shape :mod:`server.py` uses but
    in-process.
    """
    settings = Settings(persistence_mode=PersistenceMode.EPHEMERAL)
    clock = FrozenClock(datetime(2026, 4, 15, tzinfo=UTC))
    catalog = MemoryCatalogRepository()
    events = EventBus()
    return AppContext(
        settings=settings,
        clock=clock,
        engine=engine,
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=events,
        udf_registry=UDFRegistry(settings),
        snapshots=SnapshotManager(
            engine=engine,
            catalog=catalog,
            clock=clock,
            events=events,
            retention_days=7,
        ),
        row_access=RowAccessPolicyManager(catalog=catalog, clock=clock),
        write_streams=WriteStreamManager(),
    )


class _CTASCountingEngine:
    """Engine proxy that counts ``CREATE OR REPLACE TABLE`` executions.

    Used to assert "collapse onto one recompute" — exactly one CTAS
    must fire across the racing readers, regardless of how many
    readers see ``is_stale=True``.
    """

    def __init__(self, inner: DuckDBEngine) -> None:
        self._inner = inner
        self.ctas_count = 0
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def execute(self, sql: str, parameters: list | None = None) -> object:
        if sql.strip().upper().startswith("CREATE OR REPLACE TABLE"):
            with self._lock:
                self.ctas_count += 1
        if parameters is not None:
            return self._inner.execute(sql, parameters)
        return self._inner.execute(sql)


@pytest.mark.asyncio
class TestStaleMVUnderConcurrentReaders:
    """N stale-readers must collapse onto exactly one recompute (gap #5).

    Drives :class:`MaterializedViewManager` directly so the assertion
    is purely about the manager's "in-write-lock re-check" guarantee,
    not about the HTTP server's connection-pooling behaviour. The
    server-level race is exercised by Phase 7's existing integration
    tests; this chaos scenario locks the contention property in.
    """

    async def test_concurrent_stale_readers_collapse_to_one_recompute(self) -> None:
        engine = DuckDBEngine(Settings(persistence_mode=PersistenceMode.EPHEMERAL))
        await engine.start()
        try:
            ctx = _build_chaos_context(engine)
            now = ctx.clock.now()

            # Seed the catalog with a base table + MV. We populate the
            # base table directly via DuckDB so we control the data
            # shape end-to-end.
            ctx.engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
            ctx.engine.execute('CREATE TABLE "p__ds"."orders" (v BIGINT)')
            ctx.engine.execute('INSERT INTO "p__ds"."orders" VALUES (1), (2), (3)')

            base_meta = TableMeta(
                project_id="p",
                dataset_id="ds",
                table_id="orders",
                table_type="TABLE",
                schema=TableSchema(
                    fields=(TableFieldSchema(name="v", type="INT64", mode="NULLABLE"),),
                ),
                creation_time=now,
                last_modified_time=now,
                num_rows=3,
                num_bytes=0,
                etag="etag-base",
            )
            ctx.catalog.create_dataset(
                DatasetMeta(
                    project_id="p",
                    dataset_id="ds",
                    creation_time=now,
                    last_modified_time=now,
                    etag="etag-ds",
                ),
            )
            ctx.catalog.create_table(base_meta)

            # Build the MV catalog row + materialised table by hand —
            # mirrors what ``MaterializedViewManager.create`` would do
            # but skips the SQLGlot/translation plumbing that isn't
            # under test here. ``view_query`` carries BigQuery-dialect
            # SQL; the manager's ``_translate`` runs it through
            # ``rewrite_table_refs`` so ``ds.orders`` resolves to
            # ``p__ds.orders`` at refresh time.
            ctx.engine.execute(
                'CREATE OR REPLACE TABLE "p__ds"."totals" AS '
                'SELECT SUM(v) AS s FROM "p__ds"."orders"',
            )
            mv_meta = MaterializedViewMeta(
                project_id="p",
                dataset_id="ds",
                table_id="totals",
                view_query="SELECT SUM(v) AS s FROM ds.orders",
                base_tables=(("p", "ds", "orders"),),
                last_refresh_time=now,
                is_stale=True,  # pre-flagged stale to set up the race
            )
            mv_table = TableMeta(
                project_id="p",
                dataset_id="ds",
                table_id="totals",
                table_type="MATERIALIZED_VIEW",
                schema=TableSchema(
                    fields=(TableFieldSchema(name="s", type="INTEGER", mode="NULLABLE"),),
                ),
                creation_time=now,
                last_modified_time=now,
                num_rows=1,
                num_bytes=0,
                etag="etag-mv",
                view_query=mv_meta.view_query,
            )
            ctx.catalog.create_table(mv_table)
            ctx.catalog.upsert_materialized_view(mv_meta)

            # Bump the underlying data so the recompute should produce
            # a different value than the seed (1+2+3 → 1+2+3+10 = 16).
            ctx.engine.execute('INSERT INTO "p__ds"."orders" VALUES (10)')

            # Swap the engine for a CTAS counter so we can prove "one
            # recompute fired" — re-wrap the AppContext.
            counting = _CTASCountingEngine(engine)
            counting_ctx = AppContext(
                settings=ctx.settings,
                clock=ctx.clock,
                engine=counting,  # type: ignore[arg-type]
                catalog=ctx.catalog,
                metrics=ctx.metrics,
                events=ctx.events,
                udf_registry=ctx.udf_registry,
                snapshots=ctx.snapshots,
                row_access=ctx.row_access,
                write_streams=ctx.write_streams,
            )
            manager = MaterializedViewManager(counting_ctx)

            n_readers = 100
            barrier = asyncio.Barrier(n_readers)
            results: list[MaterializedViewMeta | None] = []

            async def reader() -> None:
                await barrier.wait()
                result = await manager.refresh_if_stale("p", "ds", "totals")
                results.append(result)

            await asyncio.gather(*(reader() for _ in range(n_readers)))

            # Every reader saw a non-stale MV by the time they returned.
            assert len(results) == n_readers
            assert all(r is not None and r.is_stale is False for r in results)

            # Exactly one CTAS execution covers the recompute — readers
            # 2..N exited inside the write-lock recheck.
            assert counting.ctas_count == 1, (
                f"expected single recompute under concurrent readers, "
                f"observed {counting.ctas_count} CTAS executions"
            )

            # And the recomputed value is the post-mutation sum.
            row = engine.execute('SELECT s FROM "p__ds"."totals"').fetchone()
            assert row == (16,)
        finally:
            await engine.stop()


# ---------------------------------------------------------------------------
# Scenario 2 — 1000-thread retry storm on a COMMITTED stream.
# ---------------------------------------------------------------------------


class TestThousandThreadRetryStorm:
    """1000 threads each submit offset=0 — exactly one OK, 999 ALREADY_EXISTS."""

    def test_thousand_concurrent_retries_yield_one_commit(self) -> None:
        strat = CommittedWriteStrategy()
        stream = WriteStream(
            name="projects/p/datasets/d/tables/t/streams/s",
            project_id="p",
            dataset_id="d",
            table_id="t",
            stream_type=WriteStreamType.COMMITTED,
        )
        n_threads = 1000
        barrier = threading.Barrier(n_threads)
        outcomes: list[AppendStatus] = []
        outcomes_lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            with stream.lock:
                outcome = strat.append(
                    stream,
                    pa.table({"id": pa.array([0], type=pa.int64())}),
                    offset=0,
                )
            with outcomes_lock:
                outcomes.append(outcome.status)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as ex:
            futures = [ex.submit(worker) for _ in range(n_threads)]
            for f in futures:
                f.result()

        ok_count = sum(1 for s in outcomes if s is AppendStatus.OK)
        ae_count = sum(1 for s in outcomes if s is AppendStatus.ALREADY_EXISTS)
        assert ok_count == 1
        assert ae_count == n_threads - 1
        assert stream.next_offset == 1
        assert stream.row_count == 1


# ---------------------------------------------------------------------------
# Scenario 3 — Mixed read/write contention on time-travel queries.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTimeTravelReadWriteContention:
    """Snapshot readers see their pinned state; writers see live state.

    The contract: a query pinned to a captured snapshot observes that
    snapshot's row count for the duration of the race, no matter how
    many writers concurrently advance the live table. Writers'
    subsequent reads (without ``FOR SYSTEM_TIME``) reflect their own
    inserts.

    Determinism: each writer increments ``row_count`` by exactly one
    per insert; the test counts inserts and asserts the final row
    count equals the initial seed plus inserts. The snapshot reader's
    expected output is fixed.
    """

    async def test_snapshot_reader_isolated_from_concurrent_writer(self) -> None:
        """Snapshot reader sees pinned state; writers advance the live table.

        Drives every operation through a single ``httpx.AsyncClient``
        on one event loop. We don't use OS-thread writers because the
        scenario's contract is about SQL-level isolation (snapshot
        vs live), not about HTTP concurrency — the latter is
        Phase 1's integration concern and would re-test the
        connection-pool layer pointlessly here.

        Determinism: the writer task does exactly ``N`` inserts (we
        gather it together with the reader probes); the reader's
        expected value is fixed at the count seeded before the pin
        timestamp.
        """
        settings = Settings(
            persistence_mode=PersistenceMode.EPHEMERAL,
            rest_port=0,
            grpc_port=0,
        )
        server = EmulatorServer(settings)
        await server.start()
        try:
            async with httpx.AsyncClient(base_url=server.rest_url, timeout=30.0) as c:
                # Set up base table with three rows.
                resp = await c.post(
                    "/bigquery/v2/projects/p/datasets",
                    json={"datasetReference": {"projectId": "p", "datasetId": "ds"}},
                )
                resp.raise_for_status()
                resp = await c.post(
                    "/bigquery/v2/projects/p/datasets/ds/tables",
                    json={
                        "tableReference": {
                            "projectId": "p",
                            "datasetId": "ds",
                            "tableId": "events",
                        },
                        "schema": {"fields": [{"name": "v", "type": "INT64"}]},
                    },
                )
                resp.raise_for_status()
                for v in (1, 2, 3):
                    resp = await c.post(
                        "/bigquery/v2/projects/p/queries",
                        json={
                            "query": f"INSERT INTO ds.events VALUES ({v})",
                            "useLegacySql": False,
                        },
                    )
                    resp.raise_for_status()

                # Capture pin timestamp once seeded.
                pin_ts = datetime.now(UTC)
                # Brief gap so subsequent writes carry a strictly later
                # snapshot timestamp than the pin.
                await asyncio.sleep(0.1)

                # FOR SYSTEM_TIME requires ISO-8601 with space separator
                # (matches Phase 7 integration tests).
                pin_str = pin_ts.isoformat(sep=" ", timespec="microseconds")
                n_writes = 10

                async def writer(index: int) -> int:
                    resp = await c.post(
                        "/bigquery/v2/projects/p/queries",
                        json={
                            "query": f"INSERT INTO ds.events VALUES ({100 + index})",
                            "useLegacySql": False,
                        },
                    )
                    return resp.status_code

                async def reader() -> int:
                    resp = await c.post(
                        "/bigquery/v2/projects/p/queries",
                        json={
                            "query": (
                                "SELECT COUNT(*) AS c FROM ds.events "
                                f"FOR SYSTEM_TIME AS OF TIMESTAMP '{pin_str}'"
                            ),
                            "useLegacySql": False,
                        },
                    )
                    resp.raise_for_status()
                    return int(resp.json()["rows"][0]["f"][0]["v"])

                # Interleave: 10 writers + 20 readers all dispatched at
                # once. asyncio.gather forces every coroutine to start
                # in the same loop iteration; FastAPI's request handler
                # decides the order, but the SQL pipeline guarantees
                # each reader resolves the pin against the *captured*
                # snapshot.
                tasks: list[asyncio.Future[int]] = [
                    *(asyncio.create_task(writer(i)) for i in range(n_writes)),
                    *(asyncio.create_task(reader()) for _ in range(20)),
                ]
                results = await asyncio.gather(*tasks)

                writer_codes = results[:n_writes]
                reader_counts = results[n_writes:]

                # Every writer succeeded (200) and every reader saw the
                # pinned count of 3.
                assert all(code == 200 for code in writer_codes), writer_codes
                assert all(count == 3 for count in reader_counts), reader_counts

                # Live count == 3 + number of successful writes.
                resp = await c.post(
                    "/bigquery/v2/projects/p/queries",
                    json={
                        "query": "SELECT COUNT(*) FROM ds.events",
                        "useLegacySql": False,
                    },
                )
                resp.raise_for_status()
                live_count = int(resp.json()["rows"][0]["f"][0]["v"])
                assert live_count == 3 + n_writes
        finally:
            await server.stop()
