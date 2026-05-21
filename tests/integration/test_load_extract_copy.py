"""Integration tests: load, extract, and copy jobs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def test_load_csv_and_query(bqemu_server: EmulatorServer, tmp_path: Path) -> None:
    """Load a CSV file into a table, then query it."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    # Create dataset + table.
    client.create_dataset("load_test")
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("name", "STRING"),
    ]
    client.create_table(bigquery.Table("test-project.load_test.items", schema=schema))

    # Write a CSV file.
    csv_path = tmp_path / "items.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name"])
        writer.writerow([1, "Alpha"])
        writer.writerow([2, "Beta"])
        writer.writerow([3, "Gamma"])

    # Load via jobs.insert with load configuration.
    import httpx

    load_response = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "load": {
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "load_test",
                        "tableId": "items",
                    },
                    "sourceUris": [str(csv_path)],
                    "sourceFormat": "CSV",
                    "writeDisposition": "WRITE_APPEND",
                },
            },
        },
        timeout=30,
    )
    assert load_response.status_code == 200
    load_body = load_response.json()
    assert load_body["status"]["state"] == "DONE"

    # Query the loaded data.
    rows = list(client.query("SELECT id, name FROM load_test.items ORDER BY id").result())
    assert len(rows) == 3
    assert rows[0].id == 1
    assert rows[0].name == "Alpha"
    assert rows[2].name == "Gamma"

    client.delete_dataset("load_test", delete_contents=True)


def test_load_json_and_query(bqemu_server: EmulatorServer, tmp_path: Path) -> None:
    """Load a newline-delimited JSON file."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    client.create_dataset("load_json")
    schema = [
        bigquery.SchemaField("x", "INT64"),
        bigquery.SchemaField("y", "STRING"),
    ]
    client.create_table(bigquery.Table("test-project.load_json.data", schema=schema))

    # Write NDJSON.
    json_path = tmp_path / "data.json"
    with json_path.open("w") as f:
        for i in range(5):
            f.write(json.dumps({"x": i, "y": f"val_{i}"}) + "\n")

    import httpx

    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "load": {
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "load_json",
                        "tableId": "data",
                    },
                    "sourceUris": [str(json_path)],
                    "sourceFormat": "NEWLINE_DELIMITED_JSON",
                },
            },
        },
        timeout=30,
    )
    assert r.status_code == 200

    rows = list(client.query("SELECT COUNT(*) AS n FROM load_json.data").result())
    assert rows[0].n == 5

    client.delete_dataset("load_json", delete_contents=True)


def test_jobs_list_and_cancel(bqemu_server: EmulatorServer) -> None:
    """Verify jobs.list and jobs.cancel work."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    # Run a query to produce a job.
    client.query("SELECT 1").result()

    # List jobs.
    import httpx

    r = httpx.get(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "bigquery#jobList"
    assert len(body["jobs"]) >= 1

    # Cancel the first job (already DONE — should be a no-op).
    job_id = body["jobs"][0]["jobReference"]["jobId"]
    r2 = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs/{job_id}/cancel",
        timeout=10,
    )
    assert r2.status_code == 200
    assert r2.json()["job"]["status"]["state"] == "DONE"


def test_copy_job_source_tables_array_create_if_needed(
    bqemu_server: EmulatorServer,
) -> None:
    """``copy`` job with ``sourceTables`` (plural array, used by ``bq cp``).

    Verifies four behaviours together:

    * ``sourceTables[0]`` is used when ``sourceTable`` is absent.
    * Default ``createDisposition=CREATE_IF_NEEDED`` materialises a
      missing destination table with the source's schema.
    * The new destination is registered in the catalog.
    * Row data is copied.
    """
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery
    import httpx

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    client.create_dataset("copy_src")
    client.create_table(
        bigquery.Table(
            "test-project.copy_src.src",
            schema=[bigquery.SchemaField("id", "INT64")],
        ),
    )
    client.query(
        "INSERT INTO `test-project.copy_src.src` (id) VALUES (7), (11)",
    ).result()
    # Destination does not exist yet — CREATE_IF_NEEDED materialises it.
    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "copy": {
                    "sourceTables": [
                        {
                            "projectId": "test-project",
                            "datasetId": "copy_src",
                            "tableId": "src",
                        },
                    ],
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_src",
                        "tableId": "dst",
                    },
                },
            },
        },
        timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"]["state"] == "DONE"
    rows = list(
        client.query("SELECT id FROM copy_src.dst ORDER BY id").result(),
    )
    assert [r.id for r in rows] == [7, 11]
    client.delete_dataset("copy_src", delete_contents=True)


def test_copy_job_operation_type_snapshot(bqemu_server: EmulatorServer) -> None:
    """``copy`` job with ``operationType=SNAPSHOT`` routes to the snapshot manager."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery
    import httpx

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    client.create_dataset("copy_snap")
    client.create_table(
        bigquery.Table(
            "test-project.copy_snap.src",
            schema=[bigquery.SchemaField("id", "INT64")],
        ),
    )
    client.query("INSERT INTO `test-project.copy_snap.src` (id) VALUES (1)").result()
    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "copy": {
                    "operationType": "SNAPSHOT",
                    "sourceTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_snap",
                        "tableId": "src",
                    },
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_snap",
                        "tableId": "snap",
                    },
                },
            },
        },
        timeout=10,
    )
    assert r.status_code == 200
    snap = client.get_table("test-project.copy_snap.snap")
    assert snap.table_type == "SNAPSHOT"
    client.delete_dataset("copy_snap", delete_contents=True)


def test_copy_job_operation_type_clone(bqemu_server: EmulatorServer) -> None:
    """``copy`` job with ``operationType=CLONE`` routes to the clone manager."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery
    import httpx

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    client.create_dataset("copy_clone")
    client.create_table(
        bigquery.Table(
            "test-project.copy_clone.src",
            schema=[bigquery.SchemaField("id", "INT64")],
        ),
    )
    client.query("INSERT INTO `test-project.copy_clone.src` (id) VALUES (2)").result()
    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "copy": {
                    "operationType": "CLONE",
                    "sourceTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_clone",
                        "tableId": "src",
                    },
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_clone",
                        "tableId": "clone",
                    },
                },
            },
        },
        timeout=10,
    )
    assert r.status_code == 200
    clone = client.get_table("test-project.copy_clone.clone")
    assert clone.table_type == "CLONE"
    client.delete_dataset("copy_clone", delete_contents=True)


def test_copy_job_operation_type_restore(bqemu_server: EmulatorServer) -> None:
    """``copy`` job with ``operationType=RESTORE`` materialises a fresh table."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery
    import httpx

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    client.create_dataset("copy_restore")
    client.create_table(
        bigquery.Table(
            "test-project.copy_restore.snap",
            schema=[bigquery.SchemaField("id", "INT64")],
        ),
    )
    client.query("INSERT INTO `test-project.copy_restore.snap` (id) VALUES (99)").result()
    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "copy": {
                    "operationType": "RESTORE",
                    "sourceTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_restore",
                        "tableId": "snap",
                    },
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_restore",
                        "tableId": "restored",
                    },
                },
            },
        },
        timeout=10,
    )
    assert r.status_code == 200
    rows = list(
        client.query("SELECT id FROM copy_restore.restored").result(),
    )
    assert [r.id for r in rows] == [99]
    client.delete_dataset("copy_restore", delete_contents=True)


def test_load_job_create_if_needed(
    bqemu_server: EmulatorServer,
    tmp_path: Path,
) -> None:
    """``load`` with ``createDisposition=CREATE_IF_NEEDED`` materialises dest."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery
    import httpx

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    client.create_dataset("load_cif")
    csv_path = tmp_path / "x.csv"
    csv_path.write_text("id,name\n1,a\n2,b\n", encoding="utf-8")

    # Destination doesn't exist yet — load must create it with the
    # explicit ``load.schema.fields`` payload.
    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "load": {
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "load_cif",
                        "tableId": "items",
                    },
                    "sourceUris": [str(csv_path)],
                    "sourceFormat": "CSV",
                    "schema": {
                        "fields": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "name", "type": "STRING"},
                        ],
                    },
                    "createDisposition": "CREATE_IF_NEEDED",
                },
            },
        },
        timeout=15,
    )
    assert r.status_code == 200
    assert r.json()["status"]["state"] == "DONE"
    rows = list(client.query("SELECT id, name FROM load_cif.items ORDER BY id").result())
    assert [(r.id, r.name) for r in rows] == [(1, "a"), (2, "b")]
    client.delete_dataset("load_cif", delete_contents=True)


def test_copy_job_write_truncate_into_existing(
    bqemu_server: EmulatorServer,
) -> None:
    """``copy`` with ``writeDisposition=WRITE_TRUNCATE`` overwrites the destination."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery
    import httpx

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    client.create_dataset("copy_trunc")
    schema = [bigquery.SchemaField("id", "INT64")]
    client.create_table(bigquery.Table("test-project.copy_trunc.src", schema=schema))
    client.create_table(bigquery.Table("test-project.copy_trunc.dst", schema=schema))
    client.query(
        "INSERT INTO `test-project.copy_trunc.src` (id) VALUES (1), (2)",
    ).result()
    client.query("INSERT INTO `test-project.copy_trunc.dst` (id) VALUES (99)").result()

    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "copy": {
                    "writeDisposition": "WRITE_TRUNCATE",
                    "sourceTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_trunc",
                        "tableId": "src",
                    },
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_trunc",
                        "tableId": "dst",
                    },
                },
            },
        },
        timeout=10,
    )
    assert r.status_code == 200
    rows = list(client.query("SELECT id FROM copy_trunc.dst ORDER BY id").result())
    assert [r.id for r in rows] == [1, 2]  # 99 was truncated; src copied in
    client.delete_dataset("copy_trunc", delete_contents=True)


def test_copy_job_write_empty_rejects_nonempty_destination(
    bqemu_server: EmulatorServer,
) -> None:
    """``writeDisposition=WRITE_EMPTY`` rejects a non-empty destination."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery
    import httpx

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    client.create_dataset("copy_we")
    schema = [bigquery.SchemaField("id", "INT64")]
    client.create_table(bigquery.Table("test-project.copy_we.src", schema=schema))
    client.create_table(bigquery.Table("test-project.copy_we.dst", schema=schema))
    client.query("INSERT INTO `test-project.copy_we.dst` (id) VALUES (5)").result()

    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "copy": {
                    "writeDisposition": "WRITE_EMPTY",
                    "sourceTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_we",
                        "tableId": "src",
                    },
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_we",
                        "tableId": "dst",
                    },
                },
            },
        },
        timeout=10,
    )
    # WRITE_EMPTY into non-empty destination → 400 (InvalidQueryError).
    assert r.status_code == 400
    client.delete_dataset("copy_we", delete_contents=True)


def test_copy_job_create_never_missing_dest_fails(
    bqemu_server: EmulatorServer,
) -> None:
    """``createDisposition=CREATE_NEVER`` rejects a missing destination."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery
    import httpx

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    client.create_dataset("copy_cn")
    client.create_table(
        bigquery.Table(
            "test-project.copy_cn.src",
            schema=[bigquery.SchemaField("id", "INT64")],
        ),
    )
    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "copy": {
                    "createDisposition": "CREATE_NEVER",
                    "sourceTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_cn",
                        "tableId": "src",
                    },
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "copy_cn",
                        "tableId": "missing",
                    },
                },
            },
        },
        timeout=10,
    )
    assert r.status_code == 404
    client.delete_dataset("copy_cn", delete_contents=True)


def test_row_access_policy_ddl_create_and_drop(
    bqemu_server: EmulatorServer,
) -> None:
    """``CREATE / DROP ROW ACCESS POLICY`` DDL through ``jobs.insert`` (bq CLI path)."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery
    import httpx

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    client.create_dataset("rap_ddl")
    client.create_table(
        bigquery.Table(
            "test-project.rap_ddl.orders",
            schema=[
                bigquery.SchemaField("id", "INT64"),
                bigquery.SchemaField("region", "STRING"),
            ],
        ),
    )
    create_sql = (
        "CREATE ROW ACCESS POLICY eu_only ON `test-project.rap_ddl.orders` "
        "GRANT TO ('user:eu@example.com') FILTER USING (region = 'EU')"
    )
    r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "query": {"query": create_sql, "useLegacySql": False},
            },
        },
        timeout=10,
    )
    assert r.status_code == 200
    rows = list(
        client.query(
            "SELECT policy_name FROM rap_ddl.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
        ).result(),
    )
    assert [r.policy_name for r in rows] == ["eu_only"]
    r2 = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "query": {
                    "query": ("DROP ROW ACCESS POLICY eu_only ON `test-project.rap_ddl.orders`"),
                    "useLegacySql": False,
                },
            },
        },
        timeout=10,
    )
    assert r2.status_code == 200
    count_rows = list(
        client.query(
            "SELECT COUNT(*) AS n FROM rap_ddl.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES",
        ).result(),
    )
    assert count_rows[0].n == 0
    client.delete_dataset("rap_ddl", delete_contents=True)
