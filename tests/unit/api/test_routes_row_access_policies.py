"""Unit tests for the rowAccessPolicies REST routes."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import pytest_asyncio

from bqemulator.api.app import create_app
from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, TableMeta
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
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    # Pre-seed a dataset + table so policy CRUD has a target.
    now = clock.now()
    catalog.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="ds",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag("p", "ds", str(now)),
        ),
    )
    catalog.create_table(
        TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag("p", "ds", "t", str(now)),
        ),
    )
    ctx = AppContext(
        settings=ephemeral_settings,
        clock=clock,
        engine=engine,
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=events,
        udf_registry=UDFRegistry(ephemeral_settings),
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
        yield create_app(ctx)
    finally:
        await engine.stop()


def _insert_payload(policy_id: str = "eu_only") -> dict[str, object]:
    return {
        "rowAccessPolicyReference": {
            "projectId": "p",
            "datasetId": "ds",
            "tableId": "t",
            "policyId": policy_id,
        },
        "filterPredicate": "region = 'EU'",
        "grantees": ["user:eu@example.com"],
    }


class TestInsertGet:
    def test_insert_and_get(self, app: FastAPI) -> None:
        client = TestClient(app)
        r = client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
            json=_insert_payload(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rowAccessPolicyReference"]["policyId"] == "eu_only"
        assert body["filterPredicate"] == "region = 'EU'"
        assert body["grantees"] == ["user:eu@example.com"]
        assert "creationTime" in body and "etag" in body

        r2 = client.get(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies/eu_only",
        )
        assert r2.status_code == 200
        assert r2.json()["filterPredicate"] == "region = 'EU'"

    def test_insert_rejects_mismatched_reference(self, app: FastAPI) -> None:
        client = TestClient(app)
        bad = _insert_payload()
        bad["rowAccessPolicyReference"]["datasetId"] = "WRONG"  # type: ignore[index]
        r = client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
            json=bad,
        )
        assert r.status_code == 400

    def test_insert_rejects_empty_filter(self, app: FastAPI) -> None:
        client = TestClient(app)
        body = _insert_payload()
        body["filterPredicate"] = ""
        r = client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
            json=body,
        )
        assert r.status_code == 400

    def test_get_404_for_missing_policy(self, app: FastAPI) -> None:
        client = TestClient(app)
        r = client.get(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies/missing",
        )
        assert r.status_code == 404


class TestList:
    def test_list_empty(self, app: FastAPI) -> None:
        client = TestClient(app)
        r = client.get(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
        )
        assert r.status_code == 200
        assert r.json() == {"rowAccessPolicies": []}

    def test_list_after_insert(self, app: FastAPI) -> None:
        client = TestClient(app)
        client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
            json=_insert_payload("p1"),
        )
        client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
            json=_insert_payload("p2"),
        )
        r = client.get(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
        )
        assert r.status_code == 200
        items = r.json()["rowAccessPolicies"]
        ids = [p["rowAccessPolicyReference"]["policyId"] for p in items]
        assert ids == ["p1", "p2"]


class TestUpdate:
    def test_put_replaces_filter_and_grantees(self, app: FastAPI) -> None:
        client = TestClient(app)
        client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
            json=_insert_payload("p1"),
        )
        new_body = {
            "rowAccessPolicyReference": {
                "projectId": "p",
                "datasetId": "ds",
                "tableId": "t",
                "policyId": "p1",
            },
            "filterPredicate": "region = 'US'",
            "grantees": ["user:us@example.com"],
        }
        r = client.put(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies/p1",
            json=new_body,
        )
        assert r.status_code == 200
        assert r.json()["filterPredicate"] == "region = 'US'"
        assert r.json()["grantees"] == ["user:us@example.com"]

    def test_put_404_for_missing(self, app: FastAPI) -> None:
        client = TestClient(app)
        body = _insert_payload("missing")
        r = client.put(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies/missing",
            json=body,
        )
        assert r.status_code == 404

    def test_put_rejects_url_id_mismatch(self, app: FastAPI) -> None:
        client = TestClient(app)
        client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
            json=_insert_payload("p1"),
        )
        body = _insert_payload("OTHER")
        r = client.put(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies/p1",
            json=body,
        )
        assert r.status_code == 400


class TestDelete:
    def test_delete_succeeds(self, app: FastAPI) -> None:
        client = TestClient(app)
        client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
            json=_insert_payload("p1"),
        )
        r = client.delete(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies/p1",
        )
        assert r.status_code == 204

    def test_delete_404(self, app: FastAPI) -> None:
        client = TestClient(app)
        r = client.delete(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies/missing",
        )
        assert r.status_code == 404


class TestBatchDelete:
    def test_batch_delete(self, app: FastAPI) -> None:
        client = TestClient(app)
        for pid in ("a", "b"):
            client.post(
                "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
                json=_insert_payload(pid),
            )
        r = client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies:batchDelete",
            json={"policyIds": ["a", "b"]},
        )
        assert r.status_code == 204
        r2 = client.get(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
        )
        assert r2.json() == {"rowAccessPolicies": []}

    def test_batch_delete_rejects_empty(self, app: FastAPI) -> None:
        client = TestClient(app)
        r = client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies:batchDelete",
            json={"policyIds": []},
        )
        assert r.status_code == 400


class TestIamShape:
    def test_get_iam_policy(self, app: FastAPI) -> None:
        client = TestClient(app)
        client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
            json=_insert_payload("p1"),
        )
        r = client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies/p1:getIamPolicy",
            json={},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == 1
        assert body["bindings"] == [
            {
                "role": "roles/bigquery.filteredDataViewer",
                "members": ["user:eu@example.com"],
            },
        ]
        # The etag is base64-encoded.
        decoded = base64.b64decode(body["etag"]).decode("ascii")
        assert decoded.startswith('"')

    def test_get_iam_policy_404(self, app: FastAPI) -> None:
        client = TestClient(app)
        r = client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies/missing:getIamPolicy",
            json={},
        )
        assert r.status_code == 404

    def test_test_iam_permissions_echoes(self, app: FastAPI) -> None:
        client = TestClient(app)
        client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies",
            json=_insert_payload("p1"),
        )
        r = client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies/p1:testIamPermissions",
            json={"permissions": ["bigquery.rowAccessPolicies.getIamPolicy"]},
        )
        assert r.status_code == 200
        assert r.json() == {
            "permissions": ["bigquery.rowAccessPolicies.getIamPolicy"],
        }

    def test_test_iam_permissions_404(self, app: FastAPI) -> None:
        client = TestClient(app)
        r = client.post(
            "/bigquery/v2/projects/p/datasets/ds/tables/t/rowAccessPolicies/missing:testIamPermissions",
            json={"permissions": ["x"]},
        )
        assert r.status_code == 404
