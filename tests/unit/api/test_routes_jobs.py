"""Unit tests for job/query REST routes via FastAPI TestClient."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import pytest_asyncio

from bqemulator.api.app import create_app
from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.events import EventBus
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def app(ephemeral_settings: Settings) -> AsyncIterator[FastAPI]:
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    catalog = MemoryCatalogRepository()
    events = EventBus()
    ctx = AppContext(
        settings=ephemeral_settings,
        clock=FrozenClock(),
        engine=engine,
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=events,
        udf_registry=UDFRegistry(ephemeral_settings),
        snapshots=SnapshotManager(
            engine=engine,
            catalog=catalog,
            clock=FrozenClock(),
            events=events,
            retention_days=7,
        ),
        row_access=RowAccessPolicyManager(catalog=catalog, clock=FrozenClock()),
    )
    try:
        yield create_app(ctx)
    finally:
        await engine.stop()


class TestSyncQueryEndpoint:
    """POST /bigquery/v2/projects/{p}/queries — the simple sync path."""

    def test_select_literal(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 1 AS one", "useLegacySql": False},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "bigquery#queryResponse"
        assert body["jobComplete"] is True
        assert body["totalRows"] == "1"
        assert len(body["rows"]) == 1
        assert body["schema"]["fields"][0]["name"] == "one"

    def test_select_multiple_rows(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT * FROM (VALUES (1), (2), (3)) AS t(x)"},
        )
        assert r.status_code == 200
        assert r.json()["totalRows"] == "3"

    def test_invalid_sql_returns_400(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "", "useLegacySql": False},
        )
        assert r.status_code == 400

    def test_legacy_sql_compat_mode_passes(self, app: FastAPI) -> None:
        """P7.c — narrow legacy-to-standard rewriter handles type-casts."""
        c = TestClient(app)
        # ``SELECT INTEGER(1)`` is legacy-SQL syntax; the rewriter
        # rewrites it to ``SELECT CAST(1 AS INT64)`` so the standard
        # pipeline can execute. Queries using legacy-SQL features
        # outside the rewritten subset (JOIN EACH, WITHIN, …) still
        # surface a translation error.
        r = c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT INTEGER(1) AS n", "useLegacySql": True},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["totalRows"] == "1"
        assert body["rows"][0]["f"][0]["v"] == "1"

    def test_dry_run(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 1", "dryRun": True},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["jobComplete"] is True
        assert body["totalRows"] == "0"
        assert "totalBytesProcessed" in body


class TestJobsInsertEndpoint:
    """POST /bigquery/v2/projects/{p}/jobs — used by the Python client."""

    def test_query_job(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/jobs",
            json={
                "configuration": {
                    "query": {
                        "query": "SELECT 42 AS answer",
                        "useLegacySql": False,
                    },
                },
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "bigquery#job"
        assert body["status"]["state"] == "DONE"
        job_id = body["jobReference"]["jobId"]

        # Fetch results via getQueryResults.
        r2 = c.get(f"/bigquery/v2/projects/p/queries/{job_id}")
        assert r2.status_code == 200
        result = r2.json()
        assert result["totalRows"] == "1"
        assert result["rows"][0]["f"][0]["v"] == "42"

    def test_dry_run_job(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/jobs",
            json={
                "configuration": {
                    "dryRun": True,
                    "query": {"query": "SELECT 1"},
                },
            },
        )
        assert r.status_code == 200
        assert r.json()["status"]["state"] == "DONE"

    def test_unsupported_bqml_returns_501(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/bigquery/v2/projects/p/jobs",
            json={
                "configuration": {
                    "query": {
                        "query": "SELECT * FROM ML.PREDICT(MODEL m, TABLE t)",
                    },
                },
            },
        )
        assert r.status_code == 501


class TestCreateSessionRoundTrip:
    """P7.c — ``createSession=true`` mints a token surfaced on the response."""

    def test_jobs_query_surfaces_session_id(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 1", "createSession": True},
        )
        assert r.status_code == 200
        body = r.json()
        token = body["sessionInfo"]["sessionId"]
        assert isinstance(token, str) and len(token) >= 8

    def test_jobs_insert_surfaces_session_id(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/jobs",
            json={
                "configuration": {
                    "query": {"query": "SELECT 1", "createSession": True},
                },
            },
        )
        assert r.status_code == 200
        body = r.json()
        token = body["statistics"]["sessionInfo"]["sessionId"]
        assert isinstance(token, str) and len(token) >= 8

    def test_minted_token_accepted_on_subsequent_job(self, app: FastAPI) -> None:
        c = TestClient(app)
        first = c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 1", "createSession": True},
        )
        assert first.status_code == 200
        token = first.json()["sessionInfo"]["sessionId"]
        # Same emulator process — the minted token should be accepted.
        second = c.post(
            "/bigquery/v2/projects/p/queries",
            json={
                "query": "SELECT 2",
                "connectionProperties": [{"key": "session_id", "value": token}],
            },
        )
        assert second.status_code == 200

    def test_bogus_session_id_rejected(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/bigquery/v2/projects/p/queries",
            json={
                "query": "SELECT 1",
                "connectionProperties": [{"key": "session_id", "value": "nope-not-a-real-token"}],
            },
        )
        assert r.status_code == 400
        assert r.json()["error"]["message"] == "Invalid input session id."

    def test_no_create_session_no_session_info(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 1"},
        )
        assert r.status_code == 200
        assert "sessionInfo" not in r.json()


class TestDryRunInvalidFunctionErrorEnvelope:
    """P7.c — dry-run resolver errors use ``location='q'`` + identifier case."""

    def test_jobs_query_dry_run_unknown_function(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/bigquery/v2/projects/p/queries",
            json={
                "query": "SELECT BQEMU_NONEXISTENT_FUNCTION(1) AS x",
                "dryRun": True,
                "useQueryCache": False,
            },
        )
        assert r.status_code == 400
        body = r.json()
        # ``error.errors[0].location`` is ``"q"`` for dry-run resolver
        # errors (real BQ's wire shape — distinct from the runtime
        # ``"query"`` location).
        assert body["error"]["errors"][0]["location"] == "q"
        # Identifier case from the BQ SQL is preserved through the
        # error message (DuckDB's parser lowercases identifiers).
        assert "BQEMU_NONEXISTENT_FUNCTION" in body["error"]["message"]

    def test_non_dry_run_keeps_query_location(self, app: FastAPI) -> None:
        # The dry-run rewrite is opt-in via the dry_run branch. The
        # regular execution path keeps ``location="query"`` and renders
        # the lowercased identifier (DuckDB's contract).
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT BQEMU_NONEXISTENT_FUNCTION(1) AS x"},
        )
        assert r.status_code == 200  # error surfaces on the queryResponse body
        body = r.json()
        assert body["errors"][0]["location"] == "query"


class TestGetJob:
    def test_not_found_returns_404(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/bigquery/v2/projects/p/jobs/no_such_job")
        assert r.status_code == 404

    def test_get_existing_job(self, app: FastAPI) -> None:
        c = TestClient(app)
        create = c.post(
            "/bigquery/v2/projects/p/jobs",
            json={"configuration": {"query": {"query": "SELECT 1"}}},
        )
        job_id = create.json()["jobReference"]["jobId"]
        r = c.get(f"/bigquery/v2/projects/p/jobs/{job_id}")
        assert r.status_code == 200
        assert r.json()["status"]["state"] == "DONE"


class TestGetQueryResults:
    def test_not_found_returns_404(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/bigquery/v2/projects/p/queries/ghost_job")
        assert r.status_code == 404

    def test_pagination(self, app: FastAPI) -> None:
        c = TestClient(app)
        # Create a result with multiple rows.
        create = c.post(
            "/bigquery/v2/projects/p/jobs",
            json={
                "configuration": {
                    "query": {
                        "query": "SELECT * FROM (VALUES (1), (2), (3), (4), (5)) AS t(x)",
                    },
                },
            },
        )
        job_id = create.json()["jobReference"]["jobId"]

        # Page 1: first 2 rows.
        r1 = c.get(f"/bigquery/v2/projects/p/queries/{job_id}?maxResults=2")
        body1 = r1.json()
        assert len(body1["rows"]) == 2
        assert "pageToken" in body1

        # Page 2: next 2 rows.
        r2 = c.get(
            f"/bigquery/v2/projects/p/queries/{job_id}?maxResults=2&pageToken={body1['pageToken']}",
        )
        body2 = r2.json()
        assert len(body2["rows"]) == 2

        # Page 3: last row.
        r3 = c.get(
            f"/bigquery/v2/projects/p/queries/{job_id}?maxResults=2&pageToken={body2['pageToken']}",
        )
        body3 = r3.json()
        assert len(body3["rows"]) == 1
        assert "pageToken" not in body3


class TestJobsListStateFilter:
    """``stateFilter=done`` is normalised to ``DONE`` (P2.f).

    Real BigQuery accepts the lowercase keyword forms (``pending``,
    ``running``, ``done``); the in-memory catalog stores the
    upper-case state, so the route must uppercase before dispatching.
    A regression here silently drops every job from the list.
    """

    def test_lowercase_done_matches_uppercase_state(self, app: FastAPI) -> None:
        c = TestClient(app)
        # Create a DONE job via the sync /queries path.
        c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 1", "useLegacySql": False},
        )
        r = c.get("/bigquery/v2/projects/p/jobs?stateFilter=done")
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "bigquery#jobList"
        assert len(body["jobs"]) >= 1

    def test_uppercase_done_also_matches(self, app: FastAPI) -> None:
        """The route uppercases unconditionally — both forms succeed."""
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 1", "useLegacySql": False},
        )
        r = c.get("/bigquery/v2/projects/p/jobs?stateFilter=DONE")
        assert r.status_code == 200
        assert len(r.json()["jobs"]) >= 1

    def test_unrelated_state_filter_excludes_done_jobs(self, app: FastAPI) -> None:
        """``stateFilter=running`` excludes the DONE jobs the emulator creates."""
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 1", "useLegacySql": False},
        )
        r = c.get("/bigquery/v2/projects/p/jobs?stateFilter=running")
        assert r.status_code == 200
        assert r.json()["jobs"] == []


class TestJobsListParentJobId:
    """``parentJobId`` filter returns an empty child-job list.

    The emulator runs script statements in-process inside the parent
    ``execute_query_job``; per-statement child jobs are not emitted.
    Returning an empty list causes ``bq query`` to fall back to
    printing the parent script's result — matching real BigQuery's
    behaviour for scripts that emit no child statements.
    """

    def test_parent_job_id_returns_empty_list(self, app: FastAPI) -> None:
        c = TestClient(app)
        # Create a job so the regular list returns at least one row.
        c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 1", "useLegacySql": False},
        )
        r = c.get(
            "/bigquery/v2/projects/p/jobs?parentJobId=script-1",
        )
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "bigquery#jobList"
        assert body["jobs"] == []
        assert body["totalItems"] == 0


class TestJobsStatisticsTimestamps:
    """``Job.statistics`` always carries millisecond-epoch timestamps.

    The ``bq`` CLI sorts script child jobs by
    ``statistics.creationTime`` and crashes with ``KeyError`` if the
    field is absent. This regression-check pins the rendering at every
    endpoint that surfaces a Job resource: ``jobs.insert``, ``jobs.get``,
    ``jobs.list``.
    """

    def test_insert_response_has_creation_time(self, app: FastAPI) -> None:
        c = TestClient(app)
        body = c.post(
            "/bigquery/v2/projects/p/jobs",
            json={
                "configuration": {
                    "query": {"query": "SELECT 1", "useLegacySql": False},
                },
            },
        ).json()
        assert "creationTime" in body["statistics"]
        # Millisecond-epoch integer string.
        assert int(body["statistics"]["creationTime"]) > 1_000_000_000_000

    def test_list_response_rows_have_creation_time(self, app: FastAPI) -> None:
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 1", "useLegacySql": False},
        )
        body = c.get("/bigquery/v2/projects/p/jobs").json()
        for job in body["jobs"]:
            assert "creationTime" in job["statistics"]

    def test_get_response_has_creation_time(self, app: FastAPI) -> None:
        c = TestClient(app)
        insert_body = c.post(
            "/bigquery/v2/projects/p/jobs",
            json={
                "configuration": {
                    "query": {"query": "SELECT 1", "useLegacySql": False},
                },
            },
        ).json()
        job_id = insert_body["jobReference"]["jobId"]
        body = c.get(f"/bigquery/v2/projects/p/jobs/{job_id}").json()
        assert "creationTime" in body["statistics"]

    def test_helper_omits_missing_start_and_end_times(self) -> None:
        """``_job_stats_with_timestamps`` skips ``startTime``/``endTime`` when None.

        Real jobs always carry both timestamps, but a synthetic ``JobMeta``
        with ``start_time=None`` / ``end_time=None`` (e.g. a partially-
        constructed test fixture) should not surface placeholder keys.
        Covers the ``if ... is not None`` branches that the integration
        tests can't reach.
        """
        from datetime import UTC, datetime

        from bqemulator.api.routes.jobs import _job_stats_with_timestamps
        from bqemulator.catalog.models import JobMeta

        meta = JobMeta(
            project_id="p",
            job_id="j",
            job_type="QUERY",
            state="DONE",
            configuration={},
            statistics={},
            creation_time=datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            start_time=None,
            end_time=None,
            etag="x",
        )
        stats = _job_stats_with_timestamps(meta)
        assert "creationTime" in stats
        assert "startTime" not in stats
        assert "endTime" not in stats


class TestJobsDeleteCanonicalPath:
    """The ``/jobs/{j}/delete`` BQ-canonical path returns 200 with ``{}`` (P2.f).

    The previous emulator behaviour was ``DELETE /jobs/{j}`` returning
    ``204 No Content``. Real BigQuery uses the trailing ``/delete``
    segment and returns ``200 OK`` with an empty JSON body — surfaced
    by the P2.f HTTP corpus when the un-suffixed DELETE recorded as
    ``404 Not Found``. The legacy un-suffixed alias stays for back-
    compat.
    """

    def test_canonical_path_returns_200_with_empty_object(self, app: FastAPI) -> None:
        c = TestClient(app)
        create = c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 1", "useLegacySql": False},
        )
        job_id = create.json()["jobReference"]["jobId"]
        r = c.delete(f"/bigquery/v2/projects/p/jobs/{job_id}/delete")
        assert r.status_code == 200
        assert r.json() == {}
        # Verify the job is gone.
        r2 = c.get(f"/bigquery/v2/projects/p/jobs/{job_id}")
        assert r2.status_code == 404

    def test_legacy_alias_returns_200_with_empty_object(self, app: FastAPI) -> None:
        """The back-compat ``/jobs/{j}`` (no ``/delete``) keeps working."""
        c = TestClient(app)
        create = c.post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT 2", "useLegacySql": False},
        )
        job_id = create.json()["jobReference"]["jobId"]
        r = c.delete(f"/bigquery/v2/projects/p/jobs/{job_id}")
        assert r.status_code == 200
        assert r.json() == {}


class TestProjectsEndpoints:
    def test_list_projects(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.get("/bigquery/v2/projects")
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "bigquery#projectList"
        assert len(body["projects"]) >= 1

    def test_get_service_account(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.get("/bigquery/v2/projects/my-proj/serviceAccount")
        assert r.status_code == 200
        body = r.json()
        assert "email" in body
        assert "my-proj" in body["email"]
