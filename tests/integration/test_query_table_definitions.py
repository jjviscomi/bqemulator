"""Integration test: query-scoped external tables (``tableDefinitions``).

Mirrors how Estuary's materialize-bigquery connector stages a transaction:
gzipped NDJSON files exposed as an external table, then MERGE / SELECT ... JOIN.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def test_merge_and_join_from_table_definitions(
    bqemu_server: EmulatorServer,
    tmp_path: Path,
) -> None:
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    client.create_dataset("ext")
    client.query(
        "CREATE TABLE ext.target (id INT64, name STRING, flow_document JSON)",
    ).result()
    client.query(
        "INSERT INTO ext.target VALUES "
        "(1, 'old', JSON '{\"id\":1}'), (2, 'gone', JSON '{\"id\":2}')",
    ).result()

    staged = tmp_path / "staged.json.gz"
    with gzip.open(staged, "wt") as f:
        for row in (
            {"c0": 1, "c1": "new", "c2": {"id": 1, "v": 2}, "_flow_delete": False},
            {"c0": 2, "_flow_delete": True},
            {"c0": 3, "c1": "added", "c2": {"id": 3}, "_flow_delete": False},
        ):
            f.write(json.dumps(row) + "\n")

    external = bigquery.ExternalConfig("NEWLINE_DELIMITED_JSON")
    external.source_uris = [str(staged)]
    external.compression = "GZIP"
    external.schema = [
        bigquery.SchemaField("c0", "INTEGER"),
        bigquery.SchemaField("c1", "STRING"),
        bigquery.SchemaField("c2", "JSON"),
        bigquery.SchemaField("_flow_delete", "BOOLEAN"),
    ]
    job_config = bigquery.QueryJobConfig(table_definitions={"flow_temp_table_0": external})

    client.query(
        """
        MERGE INTO ext.target AS l
        USING flow_temp_table_0 AS r
        ON l.id = r.c0
        WHEN MATCHED AND r._flow_delete THEN DELETE
        WHEN MATCHED THEN UPDATE SET name = r.c1, flow_document = r.c2
        WHEN NOT MATCHED AND NOT r._flow_delete THEN
            INSERT (id, name, flow_document) VALUES (r.c0, r.c1, r.c2)
        """,
        job_config=job_config,
    ).result()

    rows = list(client.query("SELECT id, name FROM ext.target ORDER BY id").result())
    assert [(r.id, r.name) for r in rows] == [(1, "new"), (3, "added")]

    dst = bigquery.Table("test-project.ext.load_results")
    client.create_table(dst)
    load_config = bigquery.QueryJobConfig(
        table_definitions={"flow_temp_table_0": external},
        destination=dst,
        write_disposition="WRITE_TRUNCATE",
    )
    # The connector reads load results back via jobs.getQueryResults.
    loaded = list(
        client.query(
            "SELECT 0, l.flow_document FROM ext.target AS l "
            "JOIN flow_temp_table_0 AS r ON l.id = r.c0;",
            job_config=load_config,
        ).result(),
    )
    assert len(loaded) == 2

    # The per-job temp views are dropped once the job finishes.
    leftovers = bqemu_server._engine.fetch_arrow(
        "SELECT view_name FROM duckdb_views() WHERE view_name LIKE '_bqemu_ext_%'",
    )
    assert leftovers.num_rows == 0

    client.delete_dataset("ext", delete_contents=True)


def test_missing_external_source_fails_job(bqemu_server: EmulatorServer, tmp_path: Path) -> None:
    from google.api_core.client_options import ClientOptions
    from google.api_core.exceptions import BadRequest
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    present = tmp_path / "present.json"
    present.write_text('{"c0": 1}\n')
    definitions = {}
    for name, path in (("ok", present), ("gone", tmp_path / "missing.json.gz")):
        external = bigquery.ExternalConfig("NEWLINE_DELIMITED_JSON")
        external.source_uris = [str(path)]
        external.schema = [bigquery.SchemaField("c0", "INTEGER")]
        definitions[name] = external
    job_config = bigquery.QueryJobConfig(table_definitions=definitions)

    # A job error, not a 500 the client would keep retrying.
    with pytest.raises(BadRequest, match="External table source"):
        client.query("SELECT c0 FROM ok, gone", job_config=job_config).result(timeout=30)

    # The view already created for "ok" is dropped too.
    leftovers = bqemu_server._engine.fetch_arrow(
        "SELECT view_name FROM duckdb_views() WHERE view_name LIKE '_bqemu_ext_%'",
    )
    assert leftovers.num_rows == 0


@pytest.mark.parametrize(
    ("name", "source_format", "match"),
    [
        ("t", "CSV", "External table format CSV is not supported"),
        ("bad-name", "NEWLINE_DELIMITED_JSON", "Invalid external table name"),
    ],
)
def test_rejects_unsupported_table_definitions(
    bqemu_server: EmulatorServer,
    tmp_path: Path,
    name: str,
    source_format: str,
    match: str,
) -> None:
    from google.api_core.client_options import ClientOptions
    from google.api_core.exceptions import GoogleAPICallError
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )
    source = tmp_path / "data.json"
    source.write_text('{"c0": 1}\n')
    external = bigquery.ExternalConfig(source_format)
    external.source_uris = [str(source)]
    external.schema = [bigquery.SchemaField("c0", "INTEGER")]
    job_config = bigquery.QueryJobConfig(table_definitions={name: external})

    with pytest.raises(GoogleAPICallError, match=match):
        client.query("SELECT 1", job_config=job_config).result(timeout=30)
