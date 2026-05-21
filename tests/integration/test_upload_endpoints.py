"""Integration tests: upload-host REST endpoints (G2).

Drives the real emulator (started by ``bqemu_server``) through the
``/upload/bigquery/v2`` routes with hand-crafted HTTP requests so we
exercise the multipart parser, resumable session state machine, and
cleanup contract against the production composition root — not the
unit-test fixture's stubbed AppContext.
"""

from __future__ import annotations

import io
import json
import uuid

import httpx
import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration

BOUNDARY = "===bqemu-test-boundary==="


def _build_multipart_related(
    envelope: dict,
    media: bytes,
    media_ct: str = "text/csv",
) -> tuple[str, bytes]:
    """Construct a multipart/related body the way BQ clients do."""
    lines: list[bytes] = []
    lines.append(f"--{BOUNDARY}".encode())
    lines.append(b"Content-Type: application/json; charset=UTF-8")
    lines.append(b"")
    lines.append(json.dumps(envelope).encode())
    lines.append(f"--{BOUNDARY}".encode())
    lines.append(f"Content-Type: {media_ct}".encode())
    lines.append(b"")
    lines.append(media)
    lines.append(f"--{BOUNDARY}--".encode())
    return (
        f'multipart/related; boundary="{BOUNDARY}"',
        b"\r\n".join(lines),
    )


def _make_dataset_table(server: EmulatorServer, dataset: str, table: str) -> None:
    """Provision a destination dataset + table via REST."""
    with httpx.Client(base_url=server.rest_url, timeout=10) as http:
        http.post(
            "/bigquery/v2/projects/test-project/datasets",
            json={"datasetReference": {"projectId": "test-project", "datasetId": dataset}},
        ).raise_for_status()
        http.post(
            f"/bigquery/v2/projects/test-project/datasets/{dataset}/tables",
            json={
                "tableReference": {
                    "projectId": "test-project",
                    "datasetId": dataset,
                    "tableId": table,
                },
                "schema": {
                    "fields": [
                        {"name": "id", "type": "INTEGER", "mode": "NULLABLE"},
                        {"name": "name", "type": "STRING", "mode": "NULLABLE"},
                    ]
                },
            },
        ).raise_for_status()


def _count_rows(server: EmulatorServer, dataset: str, table: str) -> int:
    """Return COUNT(*) on the table via jobs.query."""
    with httpx.Client(base_url=server.rest_url, timeout=10) as http:
        r = http.post(
            "/bigquery/v2/projects/test-project/queries",
            json={"query": f"SELECT COUNT(*) AS n FROM `test-project.{dataset}.{table}`"},
        )
        r.raise_for_status()
        return int(r.json()["rows"][0]["f"][0]["v"])


# ---------------------------------------------------------------------------
# Per-upload-type happy path
# ---------------------------------------------------------------------------


def test_multipart_load_csv(bqemu_server: EmulatorServer) -> None:
    dataset = f"upload_mp_{uuid.uuid4().hex[:8]}"
    _make_dataset_table(bqemu_server, dataset, "t")
    envelope = {
        "configuration": {
            "load": {
                "destinationTable": {
                    "projectId": "test-project",
                    "datasetId": dataset,
                    "tableId": "t",
                },
                "sourceFormat": "CSV",
                "writeDisposition": "WRITE_TRUNCATE",
                "skipLeadingRows": 1,
            }
        }
    }
    ct, body = _build_multipart_related(envelope, b"id,name\n1,alice\n2,bob\n3,carol\n")
    with httpx.Client(base_url=bqemu_server.rest_url, timeout=15) as http:
        r = http.post(
            "/upload/bigquery/v2/projects/test-project/jobs?uploadType=multipart",
            headers={"Content-Type": ct},
            content=body,
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"]["state"] == "DONE"
    assert _count_rows(bqemu_server, dataset, "t") == 3


def test_resumable_single_chunk(bqemu_server: EmulatorServer) -> None:
    dataset = f"upload_rs_{uuid.uuid4().hex[:8]}"
    _make_dataset_table(bqemu_server, dataset, "t")
    body = b"id,name\n1,alice\n2,bob\n"
    with httpx.Client(base_url=bqemu_server.rest_url, timeout=15) as http:
        initiate = http.post(
            "/upload/bigquery/v2/projects/test-project/jobs?uploadType=resumable",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {
                            "projectId": "test-project",
                            "datasetId": dataset,
                            "tableId": "t",
                        },
                        "sourceFormat": "CSV",
                        "writeDisposition": "WRITE_TRUNCATE",
                        "skipLeadingRows": 1,
                    }
                }
            },
        )
        assert initiate.status_code == 200
        upload_id = initiate.headers["X-GUploader-UploadID"]
        r = http.put(
            f"/upload/bigquery/v2/projects/test-project/jobs?upload_id={upload_id}",
            content=body,
            headers={"Content-Range": f"bytes 0-{len(body) - 1}/{len(body)}"},
        )
    assert r.status_code == 200, r.text
    assert _count_rows(bqemu_server, dataset, "t") == 2


def test_resumable_multi_chunk(bqemu_server: EmulatorServer) -> None:
    dataset = f"upload_mc_{uuid.uuid4().hex[:8]}"
    _make_dataset_table(bqemu_server, dataset, "t")
    full = b"id,name\n" + b"".join(f"{i},name{i}\n".encode() for i in range(50))
    with httpx.Client(base_url=bqemu_server.rest_url, timeout=15) as http:
        initiate = http.post(
            "/upload/bigquery/v2/projects/test-project/jobs?uploadType=resumable",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {
                            "projectId": "test-project",
                            "datasetId": dataset,
                            "tableId": "t",
                        },
                        "sourceFormat": "CSV",
                        "writeDisposition": "WRITE_TRUNCATE",
                        "skipLeadingRows": 1,
                    }
                }
            },
        )
        upload_id = initiate.headers["X-GUploader-UploadID"]
        # Split into 3 chunks
        total = len(full)
        chunks = [full[:200], full[200:400], full[400:]]
        offset = 0
        for idx, chunk in enumerate(chunks):
            end = offset + len(chunk) - 1
            r = http.put(
                f"/upload/bigquery/v2/projects/test-project/jobs?upload_id={upload_id}",
                content=chunk,
                headers={"Content-Range": f"bytes {offset}-{end}/{total}"},
            )
            expected_status = 200 if idx == len(chunks) - 1 else 308
            assert r.status_code == expected_status, f"chunk {idx}: {r.text}"
            if r.status_code == 308:
                assert r.headers.get("Range") == f"bytes=0-{end}"
            offset = end + 1
    assert _count_rows(bqemu_server, dataset, "t") == 50


def test_resumable_status_probe_and_resume(bqemu_server: EmulatorServer) -> None:
    """Simulate a network drop and resume via Content-Range: bytes */N."""
    dataset = f"upload_rp_{uuid.uuid4().hex[:8]}"
    _make_dataset_table(bqemu_server, dataset, "t")
    full = b"id,name\n" + b"".join(f"{i},name{i}\n".encode() for i in range(20))
    with httpx.Client(base_url=bqemu_server.rest_url, timeout=15) as http:
        initiate = http.post(
            "/upload/bigquery/v2/projects/test-project/jobs?uploadType=resumable",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {
                            "projectId": "test-project",
                            "datasetId": dataset,
                            "tableId": "t",
                        },
                        "sourceFormat": "CSV",
                        "writeDisposition": "WRITE_TRUNCATE",
                        "skipLeadingRows": 1,
                    }
                }
            },
        )
        upload_id = initiate.headers["X-GUploader-UploadID"]
        total = len(full)
        first_half = full[: total // 2]
        end1 = len(first_half) - 1
        r1 = http.put(
            f"/upload/bigquery/v2/projects/test-project/jobs?upload_id={upload_id}",
            content=first_half,
            headers={"Content-Range": f"bytes 0-{end1}/{total}"},
        )
        assert r1.status_code == 308
        # Status probe — client recovers session state.
        probe = http.put(
            f"/upload/bigquery/v2/projects/test-project/jobs?upload_id={upload_id}",
            content=b"",
            headers={"Content-Range": f"bytes */{total}"},
        )
        assert probe.status_code == 308
        assert probe.headers.get("Range") == f"bytes=0-{end1}"
        # Resume from the recovered offset.
        second_half = full[total // 2 :]
        r2 = http.put(
            f"/upload/bigquery/v2/projects/test-project/jobs?upload_id={upload_id}",
            content=second_half,
            headers={"Content-Range": f"bytes {total // 2}-{total - 1}/{total}"},
        )
        assert r2.status_code == 200
    assert _count_rows(bqemu_server, dataset, "t") == 20


# ---------------------------------------------------------------------------
# Failure + cleanup paths
# ---------------------------------------------------------------------------


def test_unknown_upload_id_returns_404(bqemu_server: EmulatorServer) -> None:
    with httpx.Client(base_url=bqemu_server.rest_url, timeout=10) as http:
        r = http.put(
            "/upload/bigquery/v2/projects/test-project/jobs?upload_id=" + "a" * 32,
            content=b"x",
        )
    assert r.status_code == 404


def test_temp_file_cleaned_up_on_load_failure(bqemu_server: EmulatorServer) -> None:
    """Drive a multipart load against a non-existent table — verify no new leftover."""
    dataset = f"upload_fail_{uuid.uuid4().hex[:8]}"
    # Snapshot the staging dir BEFORE the call so the assertion is
    # order-independent. The default staging dir is the system tempdir
    # which may contain leftovers from prior pytest sessions; we only
    # care that *this* call doesn't leak a new file.
    ctx = bqemu_server._context
    staging = ctx.upload_sessions.staging_dir
    before = {p.name for p in staging.iterdir()} if staging.exists() else set()

    # Intentionally do not create the table; load will fail.
    envelope = {
        "configuration": {
            "load": {
                "destinationTable": {
                    "projectId": "test-project",
                    "datasetId": dataset,
                    "tableId": "nonexistent",
                },
                "sourceFormat": "CSV",
            }
        }
    }
    ct, body = _build_multipart_related(envelope, b"id,name\n1,a\n")
    with httpx.Client(base_url=bqemu_server.rest_url, timeout=15) as http:
        r = http.post(
            "/upload/bigquery/v2/projects/test-project/jobs?uploadType=multipart",
            headers={"Content-Type": ct},
            content=body,
        )
    # The load itself surfaces an async error envelope (status 200 +
    # errorResult) OR a direct 4xx — both prove the server didn't crash.
    assert r.status_code in (200, 400, 404)
    after = {p.name for p in staging.iterdir()} if staging.exists() else set()
    # No NEW files leaked by this call.
    assert after - before == set(), f"leaked files: {after - before}"


def test_concurrent_resumable_sessions(bqemu_server: EmulatorServer) -> None:
    """Two interleaved resumable sessions don't crosstalk."""
    ds1 = f"upload_c1_{uuid.uuid4().hex[:8]}"
    ds2 = f"upload_c2_{uuid.uuid4().hex[:8]}"
    _make_dataset_table(bqemu_server, ds1, "t")
    _make_dataset_table(bqemu_server, ds2, "t")

    def initiate(ds: str) -> str:
        with httpx.Client(base_url=bqemu_server.rest_url, timeout=10) as http:
            r = http.post(
                "/upload/bigquery/v2/projects/test-project/jobs?uploadType=resumable",
                json={
                    "configuration": {
                        "load": {
                            "destinationTable": {
                                "projectId": "test-project",
                                "datasetId": ds,
                                "tableId": "t",
                            },
                            "sourceFormat": "CSV",
                            "writeDisposition": "WRITE_TRUNCATE",
                            "skipLeadingRows": 1,
                        }
                    }
                },
            )
        return r.headers["X-GUploader-UploadID"]

    sid1 = initiate(ds1)
    sid2 = initiate(ds2)
    body1 = b"id,name\n1,alice\n2,bob\n"
    body2 = b"id,name\n10,xavier\n20,yvonne\n30,zara\n"

    with httpx.Client(base_url=bqemu_server.rest_url, timeout=10) as http:
        # Interleave: half of body1, then half of body2, then finish both.
        r1a = http.put(
            f"/upload/bigquery/v2/projects/test-project/jobs?upload_id={sid1}",
            content=body1[:10],
            headers={"Content-Range": f"bytes 0-9/{len(body1)}"},
        )
        assert r1a.status_code == 308
        r2a = http.put(
            f"/upload/bigquery/v2/projects/test-project/jobs?upload_id={sid2}",
            content=body2[:12],
            headers={"Content-Range": f"bytes 0-11/{len(body2)}"},
        )
        assert r2a.status_code == 308
        r1b = http.put(
            f"/upload/bigquery/v2/projects/test-project/jobs?upload_id={sid1}",
            content=body1[10:],
            headers={"Content-Range": f"bytes 10-{len(body1) - 1}/{len(body1)}"},
        )
        assert r1b.status_code == 200
        r2b = http.put(
            f"/upload/bigquery/v2/projects/test-project/jobs?upload_id={sid2}",
            content=body2[12:],
            headers={"Content-Range": f"bytes 12-{len(body2) - 1}/{len(body2)}"},
        )
        assert r2b.status_code == 200

    assert _count_rows(bqemu_server, ds1, "t") == 2
    assert _count_rows(bqemu_server, ds2, "t") == 3


def test_session_eviction_after_ttl(bqemu_server: EmulatorServer) -> None:
    """A session past TTL is not findable; the staging file is cleaned up."""
    # Reach inside the manager to force eviction without waiting an hour.
    ctx = bqemu_server._context
    manager = ctx.upload_sessions
    assert manager is not None
    session = manager.create("test-project", {"load": {"sourceFormat": "CSV"}})
    staging = session.staging_path
    assert staging.exists()
    # Backdate last_active_at past the TTL.
    from datetime import timedelta

    session.last_active_at = ctx.clock.now() - (manager._ttl + timedelta(seconds=1))
    # Trigger eviction sweep via a get() call against a different session.
    from bqemulator.domain.errors import NotFoundError

    with pytest.raises(NotFoundError):
        manager.get(session.session_id)
    assert not staging.exists()


def test_load_from_local_file_via_python_client(bqemu_server: EmulatorServer) -> None:
    """End-to-end via the BQ Python client's ``load_table_from_file``."""
    dataset = f"upload_py_{uuid.uuid4().hex[:8]}"
    _make_dataset_table(bqemu_server, dataset, "t")
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        schema=[
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("name", "STRING"),
        ],
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    csv_bytes = b"id,name\n1,alice\n2,bob\n3,carol\n4,dan\n"
    job = client.load_table_from_file(
        io.BytesIO(csv_bytes),
        f"test-project.{dataset}.t",
        job_config=job_config,
    )
    job.result(timeout=30)
    assert _count_rows(bqemu_server, dataset, "t") == 4
