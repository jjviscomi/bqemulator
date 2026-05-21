"""Integration test: query parameters via the real Python client."""

from __future__ import annotations

import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def test_named_parameter_query(bqemu_server: EmulatorServer) -> None:
    """Named @param in a WHERE clause."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    client.create_dataset("param_test")
    schema = [bigquery.SchemaField("name", "STRING"), bigquery.SchemaField("age", "INT64")]
    table = client.create_table(bigquery.Table("test-project.param_test.people", schema=schema))
    client.insert_rows_json(
        table,
        [
            {"name": "Alice", "age": "30"},
            {"name": "Bob", "age": "25"},
        ],
    )

    # Query with named parameter.
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("min_age", "INT64", 28)],
    )
    rows = list(
        client.query(
            "SELECT name FROM param_test.people WHERE age >= @min_age",
            job_config=job_config,
        ).result()
    )

    assert len(rows) == 1
    assert rows[0].name == "Alice"

    client.delete_dataset("param_test", delete_contents=True)


def test_error_on_invalid_sql(bqemu_server: EmulatorServer) -> None:
    """Invalid SQL returns a BigQuery-shaped error, not an HTTP 500."""
    from google.api_core.client_options import ClientOptions
    from google.api_core.exceptions import BadRequest
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    with pytest.raises(BadRequest):
        client.query("SELECT FROM WHERE INVALID").result()


def test_query_nonexistent_table(bqemu_server: EmulatorServer) -> None:
    """Query against a table that doesn't exist returns an error."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    with pytest.raises(Exception):
        client.query("SELECT * FROM ghost_dataset.ghost_table").result()
