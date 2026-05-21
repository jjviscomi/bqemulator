"""Unit tests for the upload-host REST routes (G2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from pathlib import Path

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
from bqemulator.jobs.upload_session_manager import UploadSessionManager
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Multipart helpers
# ---------------------------------------------------------------------------


def _build_multipart_related(
    json_body: dict[str, object],
    media: bytes,
    media_ct: str = "text/csv",
) -> tuple[str, bytes]:
    """Construct a ``multipart/related`` body the way BQ clients do.

    Returns ``(content_type_header, body_bytes)``. The Content-Type
    header includes the boundary that the body uses.
    """
    boundary = "===boundary===bqemu"
    lines: list[bytes] = []
    lines.append(f"--{boundary}".encode())
    lines.append(b"Content-Type: application/json; charset=UTF-8")
    lines.append(b"")
    lines.append(json.dumps(json_body).encode())
    lines.append(f"--{boundary}".encode())
    lines.append(f"Content-Type: {media_ct}".encode())
    lines.append(b"")
    lines.append(media)
    lines.append(f"--{boundary}--".encode())
    body = b"\r\n".join(lines)
    ct = f'multipart/related; boundary="{boundary}"'
    return ct, body


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app(ephemeral_settings: Settings, tmp_path: Path) -> AsyncIterator[FastAPI]:
    """Build an app with a session-manager wired into the AppContext."""
    engine = DuckDBEngine(ephemeral_settings)
    await engine.start()
    catalog = MemoryCatalogRepository()
    events = EventBus()
    clock = FrozenClock()
    manager = UploadSessionManager(
        staging_dir=tmp_path / "uploads",
        max_bytes=10 * 1024,  # 10 KiB cap keeps the size-cap test cheap
        ttl_seconds=3600,
        clock=clock,
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
        upload_sessions=manager,
    )
    fastapi_app = create_app(ctx)
    # Seed destination dataset + table via the same REST surface client
    # libraries use. Keeps the fixture decoupled from internal catalog
    # APIs.
    seed_client = TestClient(fastapi_app)
    seed_client.post(
        "/bigquery/v2/projects/p/datasets",
        json={"datasetReference": {"projectId": "p", "datasetId": "d"}},
    )
    seed_client.post(
        "/bigquery/v2/projects/p/datasets/d/tables",
        json={
            "tableReference": {"projectId": "p", "datasetId": "d", "tableId": "t"},
            "schema": {
                "fields": [
                    {"name": "id", "type": "INTEGER", "mode": "NULLABLE"},
                    {"name": "name", "type": "STRING", "mode": "NULLABLE"},
                ]
            },
        },
    )
    try:
        yield fastapi_app
    finally:
        await engine.stop()


# ---------------------------------------------------------------------------
# uploadType validation
# ---------------------------------------------------------------------------


class TestUploadTypeValidation:
    def test_missing_upload_type_returns_400(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post("/upload/bigquery/v2/projects/p/jobs", json={})
        assert r.status_code == 400
        assert "uploadType" in r.json()["error"]["message"]

    def test_unknown_upload_type_returns_400(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post("/upload/bigquery/v2/projects/p/jobs?uploadType=bogus", json={})
        assert r.status_code == 400

    def test_media_upload_type_rejected_for_jobs_insert(self, app: FastAPI) -> None:
        c = TestClient(app)
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=media",
            content=b"data",
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Multipart upload
# ---------------------------------------------------------------------------


class TestMultipartUpload:
    def test_csv_multipart_loads_table(self, app: FastAPI) -> None:
        c = TestClient(app)
        envelope = {
            "configuration": {
                "load": {
                    "destinationTable": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                    "sourceFormat": "CSV",
                    "writeDisposition": "WRITE_TRUNCATE",
                }
            }
        }
        ct, body = _build_multipart_related(envelope, b"id,name\n1,alice\n2,bob\n")
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=multipart",
            headers={"Content-Type": ct},
            content=body,
        )
        assert r.status_code == 200, r.text
        job = r.json()
        assert job["kind"] == "bigquery#job"
        assert job["status"]["state"] == "DONE"
        assert "errorResult" not in job["status"]
        # Verify the rows landed.
        query = TestClient(app).post(
            "/bigquery/v2/projects/p/queries",
            json={"query": "SELECT COUNT(*) AS n FROM `p.d.t`", "useLegacySql": False},
        )
        assert query.json()["rows"][0]["f"][0]["v"] == "2"

    def test_ndjson_multipart_loads_table(self, app: FastAPI) -> None:
        c = TestClient(app)
        envelope = {
            "configuration": {
                "load": {
                    "destinationTable": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                    "sourceFormat": "NEWLINE_DELIMITED_JSON",
                    "writeDisposition": "WRITE_TRUNCATE",
                }
            }
        }
        ndjson = b'{"id":1,"name":"a"}\n{"id":2,"name":"b"}\n'
        ct, body = _build_multipart_related(envelope, ndjson, media_ct="application/json")
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=multipart",
            headers={"Content-Type": ct},
            content=body,
        )
        assert r.status_code == 200

    def test_multipart_with_wrong_content_type_returns_400(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=multipart",
            headers={"Content-Type": "application/octet-stream"},
            content=b"hi",
        )
        assert r.status_code == 400

    def test_multipart_with_invalid_json_first_part_returns_400(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        boundary = "===bogus==="
        body = (
            f"--{boundary}\r\nContent-Type: application/json\r\n\r\n"
            "{not-json}\r\n"
            f"--{boundary}\r\nContent-Type: text/csv\r\n\r\n"
            "id,name\n1,a\n"
            f"\r\n--{boundary}--"
        ).encode()
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=multipart",
            headers={"Content-Type": f'multipart/related; boundary="{boundary}"'},
            content=body,
        )
        assert r.status_code == 400

    def test_multipart_with_three_parts_rejected(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        boundary = "===three==="
        body = (
            f"--{boundary}\r\nContent-Type: application/json\r\n\r\n{{}}\r\n"
            f"--{boundary}\r\nContent-Type: text/csv\r\n\r\nA\r\n"
            f"--{boundary}\r\nContent-Type: text/csv\r\n\r\nB\r\n"
            f"--{boundary}--"
        ).encode()
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=multipart",
            headers={"Content-Type": f'multipart/related; boundary="{boundary}"'},
            content=body,
        )
        assert r.status_code == 400

    def test_multipart_with_unsupported_media_type_rejected(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        envelope = {
            "configuration": {
                "load": {
                    "destinationTable": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                    "sourceFormat": "CSV",
                }
            }
        }
        ct, body = _build_multipart_related(envelope, b"hello", media_ct="image/jpeg")
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=multipart",
            headers={"Content-Type": ct},
            content=body,
        )
        assert r.status_code == 400

    def test_multipart_missing_configuration_load_returns_400(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        envelope = {"configuration": {"query": {"query": "SELECT 1"}}}
        ct, body = _build_multipart_related(envelope, b"id,name\n1,a\n")
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=multipart",
            headers={"Content-Type": ct},
            content=body,
        )
        assert r.status_code == 400

    def test_oversized_multipart_rejected_with_413_class(self, app: FastAPI) -> None:
        # ``app`` fixture pins max_bytes=10 KiB; send 20 KiB.
        c = TestClient(app, raise_server_exceptions=False)
        envelope = {
            "configuration": {
                "load": {
                    "destinationTable": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                    "sourceFormat": "CSV",
                }
            }
        }
        big = b"a,b\n" + b"1,x\n" * 6000  # well past 10 KiB
        ct, body = _build_multipart_related(envelope, big)
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=multipart",
            headers={"Content-Type": ct},
            content=body,
        )
        # Size cap is enforced as InvalidQueryError → 400.
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Resumable upload
# ---------------------------------------------------------------------------


class TestResumableUpload:
    def test_initiate_returns_location_header(self, app: FastAPI) -> None:
        c = TestClient(app)
        body = {
            "configuration": {
                "load": {
                    "destinationTable": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                    "sourceFormat": "CSV",
                    "writeDisposition": "WRITE_TRUNCATE",
                }
            }
        }
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=resumable",
            json=body,
        )
        assert r.status_code == 200
        assert "Location" in r.headers
        assert "upload_id=" in r.headers["Location"]
        assert r.headers["X-GUploader-UploadID"]

    def test_initiate_with_invalid_json_returns_400(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=resumable",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_initiate_with_non_object_body_returns_400(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=resumable",
            content=b"[1, 2, 3]",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_single_chunk_completes_load(self, app: FastAPI) -> None:
        c = TestClient(app)
        initiate = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=resumable",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                        "sourceFormat": "CSV",
                        "writeDisposition": "WRITE_TRUNCATE",
                    }
                }
            },
        )
        upload_id = initiate.headers["X-GUploader-UploadID"]
        csv_body = b"id,name\n1,alice\n"
        r = c.put(
            f"/upload/bigquery/v2/projects/p/jobs?upload_id={upload_id}",
            content=csv_body,
            headers={"Content-Range": f"bytes 0-{len(csv_body) - 1}/{len(csv_body)}"},
        )
        assert r.status_code == 200
        assert r.json()["kind"] == "bigquery#job"

    def test_two_chunks_completes_load(self, app: FastAPI) -> None:
        c = TestClient(app)
        initiate = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=resumable",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                        "sourceFormat": "CSV",
                        "writeDisposition": "WRITE_TRUNCATE",
                    }
                }
            },
        )
        upload_id = initiate.headers["X-GUploader-UploadID"]
        full = b"id,name\n1,alice\n2,bob\n"
        first = full[:8]
        second = full[8:]
        total = len(full)
        r1 = c.put(
            f"/upload/bigquery/v2/projects/p/jobs?upload_id={upload_id}",
            content=first,
            headers={"Content-Range": f"bytes 0-{len(first) - 1}/{total}"},
        )
        assert r1.status_code == 308
        assert r1.headers.get("Range") == f"bytes=0-{len(first) - 1}"
        r2 = c.put(
            f"/upload/bigquery/v2/projects/p/jobs?upload_id={upload_id}",
            content=second,
            headers={"Content-Range": f"bytes {len(first)}-{total - 1}/{total}"},
        )
        assert r2.status_code == 200

    def test_chunk_without_upload_id_returns_400(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.put("/upload/bigquery/v2/projects/p/jobs", content=b"x")
        assert r.status_code == 400

    def test_chunk_with_unknown_upload_id_returns_404(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.put(
            "/upload/bigquery/v2/projects/p/jobs?upload_id=" + "a" * 32,
            content=b"x",
        )
        assert r.status_code == 404

    def test_chunk_with_traversal_id_returns_404(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.put(
            "/upload/bigquery/v2/projects/p/jobs?upload_id=../etc/passwd",
            content=b"x",
        )
        assert r.status_code == 404

    def test_out_of_order_chunk_returns_400(self, app: FastAPI) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        initiate = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=resumable",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                        "sourceFormat": "CSV",
                    }
                }
            },
        )
        upload_id = initiate.headers["X-GUploader-UploadID"]
        r = c.put(
            f"/upload/bigquery/v2/projects/p/jobs?upload_id={upload_id}",
            content=b"x" * 5,
            headers={"Content-Range": "bytes 100-104/200"},
        )
        assert r.status_code == 400

    def test_status_probe_returns_308_with_range(self, app: FastAPI) -> None:
        c = TestClient(app)
        initiate = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=resumable",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                        "sourceFormat": "CSV",
                    }
                }
            },
        )
        upload_id = initiate.headers["X-GUploader-UploadID"]
        # Send a partial chunk first so the probe has something to report.
        c.put(
            f"/upload/bigquery/v2/projects/p/jobs?upload_id={upload_id}",
            content=b"hello",
            headers={"Content-Range": "bytes 0-4/20"},
        )
        probe = c.put(
            f"/upload/bigquery/v2/projects/p/jobs?upload_id={upload_id}",
            content=b"",
            headers={"Content-Range": "bytes */20"},
        )
        assert probe.status_code == 308
        assert probe.headers.get("Range") == "bytes=0-4"

    def test_initial_status_probe_omits_range_header(self, app: FastAPI) -> None:
        c = TestClient(app)
        initiate = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=resumable",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                        "sourceFormat": "CSV",
                    }
                }
            },
        )
        upload_id = initiate.headers["X-GUploader-UploadID"]
        probe = c.put(
            f"/upload/bigquery/v2/projects/p/jobs?upload_id={upload_id}",
            content=b"",
            headers={"Content-Range": "bytes */20"},
        )
        assert probe.status_code == 308
        assert "Range" not in probe.headers

    def test_temp_file_cleaned_up_on_completion(self, app: FastAPI, tmp_path: Path) -> None:
        c = TestClient(app)
        initiate = c.post(
            "/upload/bigquery/v2/projects/p/jobs?uploadType=resumable",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {"projectId": "p", "datasetId": "d", "tableId": "t"},
                        "sourceFormat": "CSV",
                        "writeDisposition": "WRITE_TRUNCATE",
                    }
                }
            },
        )
        upload_id = initiate.headers["X-GUploader-UploadID"]
        body = b"id,name\n1,a\n"
        c.put(
            f"/upload/bigquery/v2/projects/p/jobs?upload_id={upload_id}",
            content=body,
            headers={"Content-Range": f"bytes 0-{len(body) - 1}/{len(body)}"},
        )
        # Staging dir should contain no leftover files.
        staging = tmp_path / "uploads"
        leftover = list(staging.iterdir()) if staging.exists() else []
        assert leftover == []
