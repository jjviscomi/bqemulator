"""Unit tests for the ``/admin/*`` endpoints.

The admin router is opt-in. These tests stand up a FastAPI app with the
admin flag enabled and a memory catalog, exercise each endpoint through
the test client, and assert the JSON shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
import pytest

from bqemulator.api.app import create_app
from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import (
    DatasetMeta,
    JobMeta,
    RoutineMeta,
    TableFieldSchema,
    TableMeta,
    TableSchema,
    TimePartitioning,
)
from bqemulator.config import Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.events import EventBus
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.streaming.write_stream import WriteStreamManager, WriteStreamType

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 14, tzinfo=UTC)


@pytest.fixture
def admin_client() -> Iterator[TestClient]:
    """Build a FastAPI test client with admin enabled and a populated catalog."""
    from bqemulator.row_access.policy import RowAccessPolicyManager
    from bqemulator.udf.runtime import UDFRegistry
    from bqemulator.versioning.snapshots import SnapshotManager

    settings = Settings(admin_enabled=True, rest_port=0, grpc_port=0)
    catalog = MemoryCatalogRepository()
    catalog.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="d",
            creation_time=_NOW,
            last_modified_time=_NOW,
            etag="e",
            labels={"team": "data"},
        ),
    )
    catalog.create_table(
        TableMeta(
            project_id="p",
            dataset_id="d",
            table_id="orders",
            schema=TableSchema(  # type: ignore[call-arg]
                fields=(
                    TableFieldSchema(name="id", type="INT64"),
                    TableFieldSchema(name="amount", type="FLOAT64"),
                ),
            ),
            time_partitioning=TimePartitioning(),
            creation_time=_NOW,
            last_modified_time=_NOW,
            etag="te",
        ),
    )
    catalog.create_routine(
        RoutineMeta(
            project_id="p",
            dataset_id="d",
            routine_id="inc",
            routine_type="SCALAR_FUNCTION",
            language="SQL",
            definition_body="SELECT 1",
            creation_time=_NOW,
            last_modified_time=_NOW,
            etag="re",
        ),
    )
    catalog.upsert_job(
        JobMeta(
            project_id="p",
            job_id="job-1",
            job_type="QUERY",
            state="DONE",
            configuration={},
            creation_time=_NOW,
            start_time=_NOW,
            end_time=_NOW,
            etag="je",
        ),
    )
    write_streams = WriteStreamManager()
    write_streams.create("p", "d", "orders", "abc", WriteStreamType.COMMITTED)

    clock = FrozenClock(_NOW)
    context = AppContext(
        settings=settings,
        clock=clock,
        engine=None,  # type: ignore[arg-type]
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=EventBus(),
        udf_registry=UDFRegistry(settings),
        snapshots=SnapshotManager(
            engine=None,  # type: ignore[arg-type]
            catalog=catalog,
            clock=clock,
            events=EventBus(),
            retention_days=7,
        ),
        row_access=RowAccessPolicyManager(catalog=catalog, clock=clock),
        write_streams=write_streams,
    )
    app = create_app(context)
    with TestClient(app) as client:
        yield client


def test_admin_jobs_returns_every_job(admin_client: TestClient) -> None:
    resp = admin_client.get("/admin/jobs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "bqemu#adminJobList"
    assert body["totalItems"] == 1
    job = body["jobs"][0]
    assert job["projectId"] == "p"
    assert job["jobId"] == "job-1"
    assert job["state"] == "DONE"


def test_admin_jobs_filters_by_project(admin_client: TestClient) -> None:
    resp = admin_client.get("/admin/jobs?projectId=does-not-exist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["totalItems"] == 0


def test_admin_jobs_filters_by_state(admin_client: TestClient) -> None:
    resp = admin_client.get("/admin/jobs?state=RUNNING")
    assert resp.status_code == 200
    body = resp.json()
    assert body["totalItems"] == 0


def test_admin_catalog_returns_project_grouped_view(admin_client: TestClient) -> None:
    resp = admin_client.get("/admin/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "bqemu#adminCatalog"
    assert body["totalProjects"] == 1
    assert body["totalDatasets"] == 1
    project = body["projects"][0]
    assert project["projectId"] == "p"
    dataset = project["datasets"][0]
    assert dataset["datasetId"] == "d"
    assert dataset["labels"] == {"team": "data"}
    assert dataset["tables"][0]["tableId"] == "orders"
    assert dataset["tables"][0]["schemaFields"] == ["id", "amount"]
    assert dataset["tables"][0]["partitioned"] is True
    assert dataset["routines"][0]["routineId"] == "inc"


def test_admin_catalog_filters_by_project(admin_client: TestClient) -> None:
    resp = admin_client.get("/admin/catalog?projectId=other")
    assert resp.status_code == 200
    body = resp.json()
    assert body["totalProjects"] == 0


def test_admin_streams_returns_active_write_streams(admin_client: TestClient) -> None:
    resp = admin_client.get("/admin/streams")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "bqemu#adminStreamList"
    assert body["writeStreamCount"] == 1
    stream = body["writeStreams"][0]
    assert stream["projectId"] == "p"
    assert stream["streamType"] == "COMMITTED"
    assert stream["state"] == "OPEN"


def test_admin_config_dumps_settings(admin_client: TestClient) -> None:
    resp = admin_client.get("/admin/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "bqemu#adminConfig"
    settings = body["settings"]
    assert settings["admin_enabled"] is True
    # Settings field shape: enum values render as their str representation.
    assert settings["persistence_mode"] == "ephemeral"
    # Anything in _REDACTED_FIELDS would show "[REDACTED]"; today the set
    # is empty, so the response carries the raw value.
    assert "default_project_id" in settings


def test_admin_off_returns_404(tmp_path: object) -> None:
    """When admin_enabled is False, the routes must not exist."""
    from bqemulator.row_access.policy import RowAccessPolicyManager
    from bqemulator.udf.runtime import UDFRegistry
    from bqemulator.versioning.snapshots import SnapshotManager

    settings = Settings(admin_enabled=False, rest_port=0, grpc_port=0)
    clock = FrozenClock(_NOW)
    catalog = MemoryCatalogRepository()
    context = AppContext(
        settings=settings,
        clock=clock,
        engine=None,  # type: ignore[arg-type]
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=EventBus(),
        udf_registry=UDFRegistry(settings),
        snapshots=SnapshotManager(
            engine=None,  # type: ignore[arg-type]
            catalog=catalog,
            clock=clock,
            events=EventBus(),
            retention_days=7,
        ),
        row_access=RowAccessPolicyManager(catalog=catalog, clock=clock),
    )
    app = create_app(context)
    with TestClient(app) as client:
        assert client.get("/admin/jobs").status_code == 404
        assert client.get("/admin/catalog").status_code == 404
        assert client.get("/admin/streams").status_code == 404
        assert client.get("/admin/config").status_code == 404


def test_admin_streams_includes_read_sessions(admin_client: TestClient) -> None:
    """When a read session is created, /admin/streams reports it."""
    import pyarrow as pa

    from bqemulator.streaming import read_session

    # Wipe any state from prior tests so the count is deterministic.
    read_session._SESSIONS.clear()
    table = pa.table({"a": [1, 2, 3]})
    read_session.create_read_session("p", "p.d.t", table, max_streams=2)
    resp = admin_client.get("/admin/streams")
    body = resp.json()
    assert body["readSessionCount"] == 1
    assert body["readSessions"][0]["numRows"] == 3
    # Clean up so subsequent tests don't see this session.
    read_session._SESSIONS.clear()
