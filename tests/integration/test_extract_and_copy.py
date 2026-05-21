"""Integration tests: extract and copy jobs.

These test the executor's extract and copy paths which had ZERO
coverage before this audit.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def _make_client(bqemu_server: EmulatorServer):
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )


def _setup_table(client, ds_name: str, table_name: str, rows: list) -> None:
    from google.cloud import bigquery

    client.create_dataset(ds_name)
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("val", "STRING"),
    ]
    table = client.create_table(
        bigquery.Table(f"test-project.{ds_name}.{table_name}", schema=schema),
    )
    if rows:
        client.insert_rows_json(table, rows)


class TestExtractJob:
    def test_extract_to_csv(
        self,
        bqemu_server: EmulatorServer,
        tmp_path: Path,
    ) -> None:
        client = _make_client(bqemu_server)
        _setup_table(
            client,
            "ext_csv",
            "data",
            [
                {"id": 1, "val": "alpha"},
                {"id": 2, "val": "beta"},
            ],
        )

        dest_path = tmp_path / "output.csv"
        r = httpx.post(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
            json={
                "configuration": {
                    "extract": {
                        "sourceTable": {
                            "projectId": "test-project",
                            "datasetId": "ext_csv",
                            "tableId": "data",
                        },
                        "destinationUris": [str(dest_path)],
                        "destinationFormat": "CSV",
                    },
                },
            },
            timeout=30,
        )
        assert r.status_code == 200
        assert r.json()["status"]["state"] == "DONE"

        # Verify the CSV file was written.
        assert dest_path.exists()
        with dest_path.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["val"] == "alpha"

        client.delete_dataset("ext_csv", delete_contents=True)

    def test_extract_to_json(
        self,
        bqemu_server: EmulatorServer,
        tmp_path: Path,
    ) -> None:
        client = _make_client(bqemu_server)
        _setup_table(client, "ext_json", "data", [{"id": 1, "val": "x"}])

        dest_path = tmp_path / "output.json"
        r = httpx.post(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
            json={
                "configuration": {
                    "extract": {
                        "sourceTable": {
                            "projectId": "test-project",
                            "datasetId": "ext_json",
                            "tableId": "data",
                        },
                        "destinationUris": [str(dest_path)],
                        "destinationFormat": "NEWLINE_DELIMITED_JSON",
                    },
                },
            },
            timeout=30,
        )
        assert r.status_code == 200
        assert dest_path.exists()
        lines = dest_path.read_text().strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0])["val"] == "x"

        client.delete_dataset("ext_json", delete_contents=True)

    def test_extract_to_parquet(
        self,
        bqemu_server: EmulatorServer,
        tmp_path: Path,
    ) -> None:
        client = _make_client(bqemu_server)
        _setup_table(client, "ext_pq", "data", [{"id": 1, "val": "p"}])

        dest_path = tmp_path / "output.parquet"
        r = httpx.post(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
            json={
                "configuration": {
                    "extract": {
                        "sourceTable": {
                            "projectId": "test-project",
                            "datasetId": "ext_pq",
                            "tableId": "data",
                        },
                        "destinationUris": [str(dest_path)],
                        "destinationFormat": "PARQUET",
                    },
                },
            },
            timeout=30,
        )
        assert r.status_code == 200
        assert dest_path.exists()
        assert dest_path.stat().st_size > 0

        client.delete_dataset("ext_pq", delete_contents=True)


class TestExtractUnknownFormat:
    def test_unknown_destination_format_rejected(
        self,
        bqemu_server: EmulatorServer,
        tmp_path: Path,
    ) -> None:
        """``extract`` rejects unknown ``destinationFormat`` with HTTP 400."""
        from google.cloud import bigquery

        client = _make_client(bqemu_server)
        client.create_dataset("ext_bad")
        client.create_table(
            bigquery.Table(
                "test-project.ext_bad.t",
                schema=[bigquery.SchemaField("id", "INT64")],
            ),
        )
        out = tmp_path / "out.bin"
        r = httpx.post(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
            json={
                "configuration": {
                    "extract": {
                        "sourceTable": {
                            "projectId": "test-project",
                            "datasetId": "ext_bad",
                            "tableId": "t",
                        },
                        "destinationUris": [f"file://{out}"],
                        "destinationFormat": "UNKNOWN_FORMAT",
                    },
                },
            },
            timeout=10,
        )
        assert r.status_code == 400
        client.delete_dataset("ext_bad", delete_contents=True)


class TestCopyJob:
    def test_copy_between_tables(self, bqemu_server: EmulatorServer) -> None:
        client = _make_client(bqemu_server)
        from google.cloud import bigquery

        # Setup source.
        _setup_table(
            client,
            "copy_src",
            "original",
            [
                {"id": 1, "val": "a"},
                {"id": 2, "val": "b"},
            ],
        )

        # Create destination table with same schema.
        client.create_dataset("copy_dst")
        schema = [
            bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("val", "STRING"),
        ]
        client.create_table(
            bigquery.Table("test-project.copy_dst.replica", schema=schema),
        )

        # Execute copy job.
        r = httpx.post(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
            json={
                "configuration": {
                    "copy": {
                        "sourceTable": {
                            "projectId": "test-project",
                            "datasetId": "copy_src",
                            "tableId": "original",
                        },
                        "destinationTable": {
                            "projectId": "test-project",
                            "datasetId": "copy_dst",
                            "tableId": "replica",
                        },
                    },
                },
            },
            timeout=30,
        )
        assert r.status_code == 200
        assert r.json()["status"]["state"] == "DONE"

        # Verify the copy.
        rows = list(
            client.query("SELECT id, val FROM copy_dst.replica ORDER BY id").result(),
        )
        assert len(rows) == 2
        assert rows[0].val == "a"
        assert rows[1].val == "b"

        client.delete_dataset("copy_src", delete_contents=True)
        client.delete_dataset("copy_dst", delete_contents=True)


class TestWriteDisposition:
    def test_write_truncate_replaces_data(
        self,
        bqemu_server: EmulatorServer,
        tmp_path: Path,
    ) -> None:
        client = _make_client(bqemu_server)
        _setup_table(client, "wd_trunc", "data", [{"id": 1, "val": "old"}])

        # Write a CSV with new data.
        csv_path = tmp_path / "new.csv"
        with csv_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "val"])
            w.writerow([99, "new"])

        r = httpx.post(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {
                            "projectId": "test-project",
                            "datasetId": "wd_trunc",
                            "tableId": "data",
                        },
                        "sourceUris": [str(csv_path)],
                        "sourceFormat": "CSV",
                        "writeDisposition": "WRITE_TRUNCATE",
                    },
                },
            },
            timeout=30,
        )
        assert r.status_code == 200

        rows = list(client.query("SELECT id FROM wd_trunc.data").result())
        assert len(rows) == 1
        assert rows[0].id == 99

        client.delete_dataset("wd_trunc", delete_contents=True)

    def test_write_empty_rejects_nonempty_table(
        self,
        bqemu_server: EmulatorServer,
        tmp_path: Path,
    ) -> None:
        client = _make_client(bqemu_server)
        _setup_table(client, "wd_empty", "data", [{"id": 1, "val": "x"}])

        csv_path = tmp_path / "more.csv"
        with csv_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "val"])
            w.writerow([2, "y"])

        r = httpx.post(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
            json={
                "configuration": {
                    "load": {
                        "destinationTable": {
                            "projectId": "test-project",
                            "datasetId": "wd_empty",
                            "tableId": "data",
                        },
                        "sourceUris": [str(csv_path)],
                        "sourceFormat": "CSV",
                        "writeDisposition": "WRITE_EMPTY",
                    },
                },
            },
            timeout=30,
        )
        # Should fail because table is not empty.
        assert r.status_code == 400

        client.delete_dataset("wd_empty", delete_contents=True)


class TestJobsDeleteAndErrors:
    def test_delete_job(self, bqemu_server: EmulatorServer) -> None:
        client = _make_client(bqemu_server)

        # Create a job.
        client.query("SELECT 1").result()

        # List and pick one.
        r = httpx.get(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
            timeout=10,
        )
        job_id = r.json()["jobs"][0]["jobReference"]["jobId"]

        # Delete it. Real BigQuery returns 200 with an empty JSON body on
        # the canonical ``/jobs/{id}/delete`` path (P2.f). The emulator's
        # legacy un-suffixed alias matches that wire shape.
        r2 = httpx.delete(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs/{job_id}/delete",
            timeout=10,
        )
        assert r2.status_code == 200
        assert r2.json() == {}

        # Verify it's gone.
        r3 = httpx.get(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs/{job_id}",
            timeout=10,
        )
        assert r3.status_code == 404

    def test_insert_job_missing_config_returns_400(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        r = httpx.post(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
            json={"configuration": {}},
            timeout=10,
        )
        assert r.status_code == 400

    def test_cancel_nonexistent_job_returns_404(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        r = httpx.post(
            f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs/ghost/cancel",
            timeout=10,
        )
        assert r.status_code == 404
