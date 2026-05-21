"""Unit tests for dataset REST routes via FastAPI TestClient."""

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


class TestListDatasets:
    def test_empty_project(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.get("/bigquery/v2/projects/p/datasets")
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "bigquery#datasetList"
        assert body["datasets"] == []
        assert body["totalItems"] == 0


class TestInsertDataset:
    def test_creates_dataset(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/datasets",
            json={"datasetReference": {"datasetId": "sales"}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "bigquery#dataset"
        assert body["datasetReference"]["datasetId"] == "sales"
        assert body["etag"]

    def test_with_labels_and_description(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post(
            "/bigquery/v2/projects/p/datasets",
            json={
                "datasetReference": {"datasetId": "labeled"},
                "description": "My dataset",
                "labels": {"team": "data"},
                "friendlyName": "Labeled DS",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["description"] == "My dataset"
        assert body["labels"] == {"team": "data"}
        assert body["friendlyName"] == "Labeled DS"

    def test_duplicate_returns_409(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        c.post("/bigquery/v2/projects/p/datasets", json={"datasetReference": {"datasetId": "dup"}})
        r = c.post(
            "/bigquery/v2/projects/p/datasets", json={"datasetReference": {"datasetId": "dup"}}
        )
        assert r.status_code == 409


class TestGetDataset:
    def test_not_found_returns_404(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/bigquery/v2/projects/p/datasets/missing")
        assert r.status_code == 404
        assert r.json()["error"]["status"] == "NOT_FOUND"

    def test_get_existing(self, app: FastAPI) -> None:
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/datasets", json={"datasetReference": {"datasetId": "existing"}}
        )
        r = c.get("/bigquery/v2/projects/p/datasets/existing")
        assert r.status_code == 200
        assert r.json()["datasetReference"]["datasetId"] == "existing"


class TestPatchDataset:
    def test_patch_description(self, app: FastAPI) -> None:
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/datasets", json={"datasetReference": {"datasetId": "patch_me"}}
        )
        r = c.patch(
            "/bigquery/v2/projects/p/datasets/patch_me",
            json={"description": "updated"},
        )
        assert r.status_code == 200
        assert r.json()["description"] == "updated"


class TestPutDataset:
    def test_put_replaces(self, app: FastAPI) -> None:
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/datasets", json={"datasetReference": {"datasetId": "put_me"}}
        )
        r = c.put(
            "/bigquery/v2/projects/p/datasets/put_me",
            json={"datasetReference": {"datasetId": "put_me"}, "description": "replaced"},
        )
        assert r.status_code == 200
        assert r.json()["description"] == "replaced"


class TestDeleteDataset:
    def test_delete_existing(self, app: FastAPI) -> None:
        c = TestClient(app)
        c.post(
            "/bigquery/v2/projects/p/datasets", json={"datasetReference": {"datasetId": "del_me"}}
        )
        r = c.delete("/bigquery/v2/projects/p/datasets/del_me")
        assert r.status_code == 204

    def test_delete_not_found_returns_404(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.delete("/bigquery/v2/projects/p/datasets/ghost")
        assert r.status_code == 404


class TestAccessEntryToRest:
    """``_access_entry_to_rest`` round-trips each AccessEntry shape.

    Direct unit test of the private helper so each conditional branch is
    exercised — the routes-level integration tests don't easily construct
    AccessEntry shapes with every field populated.
    """

    def test_user_by_email_entry(self) -> None:
        from bqemulator.api.routes.datasets import _access_entry_to_rest
        from bqemulator.catalog.models import AccessEntry

        out = _access_entry_to_rest(
            AccessEntry(role="READER", user_by_email="alice@example.test"),
        )
        assert out == {"role": "READER", "userByEmail": "alice@example.test"}

    def test_group_by_email_entry(self) -> None:
        from bqemulator.api.routes.datasets import _access_entry_to_rest
        from bqemulator.catalog.models import AccessEntry

        out = _access_entry_to_rest(
            AccessEntry(role="WRITER", group_by_email="data@example.test"),
        )
        assert out["groupByEmail"] == "data@example.test"
        assert out["role"] == "WRITER"

    def test_domain_entry(self) -> None:
        from bqemulator.api.routes.datasets import _access_entry_to_rest
        from bqemulator.catalog.models import AccessEntry

        out = _access_entry_to_rest(AccessEntry(role="READER", domain="example.test"))
        assert out["domain"] == "example.test"

    def test_special_group_entry(self) -> None:
        from bqemulator.api.routes.datasets import _access_entry_to_rest
        from bqemulator.catalog.models import AccessEntry

        out = _access_entry_to_rest(
            AccessEntry(role="READER", special_group="projectOwners"),
        )
        assert out["specialGroup"] == "projectOwners"

    def test_iam_member_entry(self) -> None:
        from bqemulator.api.routes.datasets import _access_entry_to_rest
        from bqemulator.catalog.models import AccessEntry

        out = _access_entry_to_rest(
            AccessEntry(role="READER", iam_member="user:alice@example.test"),
        )
        assert out["iamMember"] == "user:alice@example.test"

    def test_view_entry(self) -> None:
        from bqemulator.api.routes.datasets import _access_entry_to_rest
        from bqemulator.catalog.models import AccessEntry

        out = _access_entry_to_rest(AccessEntry(view=("p", "ds", "v")))
        assert out["view"] == {"projectId": "p", "datasetId": "ds", "tableId": "v"}

    def test_routine_entry(self) -> None:
        from bqemulator.api.routes.datasets import _access_entry_to_rest
        from bqemulator.catalog.models import AccessEntry

        out = _access_entry_to_rest(AccessEntry(routine=("p", "ds", "r")))
        assert out["routine"] == {"projectId": "p", "datasetId": "ds", "routineId": "r"}

    def test_dataset_entry(self) -> None:
        from bqemulator.api.routes.datasets import _access_entry_to_rest
        from bqemulator.catalog.models import AccessEntry

        out = _access_entry_to_rest(AccessEntry(dataset=("p", "ds")))
        assert out["dataset"] == {"projectId": "p", "datasetId": "ds"}

    def test_empty_entry_returns_empty_dict(self) -> None:
        from bqemulator.api.routes.datasets import _access_entry_to_rest
        from bqemulator.catalog.models import AccessEntry

        assert _access_entry_to_rest(AccessEntry()) == {}


class TestDatasetExpirationRoundTrip:
    """``defaultTableExpirationMs`` / ``defaultPartitionExpirationMs``
    survive a POST→GET round-trip on the dataset metadata.

    Exercises the optional-field branches in ``_dataset_meta_to_rest``
    that convert in-memory ints into string-typed REST fields.
    """

    def test_round_trip_table_expiration(self, app: FastAPI) -> None:
        c = TestClient(app)
        r_post = c.post(
            "/bigquery/v2/projects/p/datasets",
            json={
                "datasetReference": {"datasetId": "ds_exp"},
                "defaultTableExpirationMs": "3600000",
            },
        )
        assert r_post.status_code == 200
        r_get = c.get("/bigquery/v2/projects/p/datasets/ds_exp")
        assert r_get.status_code == 200
        assert r_get.json()["defaultTableExpirationMs"] == "3600000"

    def test_round_trip_partition_expiration(self, app: FastAPI) -> None:
        c = TestClient(app)
        r_post = c.post(
            "/bigquery/v2/projects/p/datasets",
            json={
                "datasetReference": {"datasetId": "ds_part"},
                "defaultPartitionExpirationMs": "7200000",
            },
        )
        assert r_post.status_code == 200
        r_get = c.get("/bigquery/v2/projects/p/datasets/ds_part")
        assert r_get.status_code == 200
        assert r_get.json()["defaultPartitionExpirationMs"] == "7200000"
