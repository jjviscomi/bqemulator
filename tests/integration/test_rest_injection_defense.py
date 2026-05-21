"""Integration tests: REST-API injection defense across every SQL boundary.

Every REST handler that interpolates a project/dataset/table id into a
DuckDB SQL string must reject injection payloads before they reach the
engine. These tests pair with
:mod:`bqemulator.storage.sql_identifiers`.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
import requests

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


INJECTION_PAYLOADS = [
    'p"; DROP TABLE x; --',
    "p' OR 1=1 --",
    "p;DELETE",
    "p\x00null",
    "p/*comment*/",
]


def _url(server: EmulatorServer, path: str) -> str:
    return f"{server.rest_url}{path}"


class TestDatasetRouteInjection:
    @pytest.mark.parametrize("bad", INJECTION_PAYLOADS)
    def test_create_dataset_rejects_injection_in_dataset_id(
        self,
        bqemu_server: EmulatorServer,
        bad: str,
    ) -> None:
        """A malicious datasetId is rejected by the DuckDB boundary."""
        # POST /datasets — body carries datasetId.
        body = {"datasetReference": {"datasetId": bad, "projectId": "proj"}}
        resp = requests.post(
            _url(bqemu_server, "/bigquery/v2/projects/proj/datasets"),
            json=body,
            timeout=5,
        )
        assert resp.status_code in {400, 422, 500}, (
            f"Injection payload {bad!r} returned {resp.status_code}"
        )


class TestTableRouteInjection:
    @pytest.mark.parametrize("bad", INJECTION_PAYLOADS)
    def test_create_table_rejects_injection_in_table_id(
        self,
        bqemu_server: EmulatorServer,
        bad: str,
    ) -> None:
        """A malicious tableId is rejected by the DuckDB boundary."""
        # Ensure dataset exists first.
        requests.post(
            _url(bqemu_server, "/bigquery/v2/projects/proj/datasets"),
            json={"datasetReference": {"datasetId": "inj_ds", "projectId": "proj"}},
            timeout=5,
        )
        body = {"tableReference": {"tableId": bad}, "schema": {"fields": []}}
        resp = requests.post(
            _url(
                bqemu_server,
                "/bigquery/v2/projects/proj/datasets/inj_ds/tables",
            ),
            json=body,
            timeout=5,
        )
        assert resp.status_code in {400, 422, 500}, (
            f"Injection payload {bad!r} returned {resp.status_code}"
        )
        # Cleanup.
        requests.delete(
            _url(
                bqemu_server,
                "/bigquery/v2/projects/proj/datasets/inj_ds?deleteContents=true",
            ),
            timeout=5,
        )


class TestTableDataRouteInjection:
    def test_insertall_rejects_injection_in_path(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """insertAll with a path-injected table id gets 400/404.

        FastAPI percent-decodes path params, so the injection bytes
        reach our handler, but our sql_identifiers guard raises before
        any SQL is built.
        """
        bad = quote('t";DROP--', safe="")
        resp = requests.post(
            _url(
                bqemu_server,
                f"/bigquery/v2/projects/proj/datasets/ds/tables/{bad}/insertAll",
            ),
            json={"rows": [{"json": {"x": 1}}]},
            timeout=5,
        )
        # Either NOT_FOUND (table lookup fails) or BAD_REQUEST (SQL
        # validator fires). Anything 5xx would mean we crashed.
        assert resp.status_code in {400, 404, 422}, resp.text
