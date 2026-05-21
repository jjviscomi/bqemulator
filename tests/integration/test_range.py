"""Integration tests for RANGE<T> queries against the in-process emulator."""

from __future__ import annotations

import datetime as dt

import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


@pytest.fixture
def client(bqemu_server: EmulatorServer):
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project="p",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )


def test_range_date_field_round_trip(client, bqemu_server: EmulatorServer) -> None:
    """A RANGE<DATE> field round-trips through tables.insert and tables.get."""
    import httpx

    client.create_dataset("rds")

    # Use a direct REST POST since the BigQuery Python client doesn't
    # always serialise ``rangeElementType`` faithfully on tables.insert.
    payload = {
        "schema": {
            "fields": [
                {"name": "id", "type": "INT64", "mode": "REQUIRED"},
                {
                    "name": "duration",
                    "type": "RANGE",
                    "mode": "NULLABLE",
                    "rangeElementType": {"type": "DATE"},
                },
            ],
        },
        "tableReference": {
            "projectId": "p",
            "datasetId": "rds",
            "tableId": "subs",
        },
    }
    response = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/p/datasets/rds/tables",
        json=payload,
        timeout=10.0,
    )
    response.raise_for_status()

    # tables.get returns the same shape.
    response = httpx.get(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/p/datasets/rds/tables/subs",
        timeout=10.0,
    )
    response.raise_for_status()
    fields = response.json()["schema"]["fields"]
    duration = next(f for f in fields if f["name"] == "duration")
    assert duration["type"] == "RANGE"
    assert duration["rangeElementType"]["type"] == "DATE"


def test_range_contains_query(client) -> None:
    """``RANGE_CONTAINS`` evaluates correctly in a query."""
    from google.cloud import bigquery

    client.create_dataset("rds2")
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("amount", "NUMERIC"),
    ]
    client.create_table(bigquery.Table("p.rds2.evts", schema=schema))
    client.insert_rows_json(
        "p.rds2.evts",
        [{"id": i, "amount": str(i * 10)} for i in range(1, 6)],
    )

    # Run a RANGE_CONTAINS predicate as a SELECT expression — the
    # query itself constructs the range inline.
    job = client.query(
        "SELECT id, RANGE_CONTAINS("
        "RANGE(DATE '2024-01-01', DATE '2024-12-31'), DATE '2024-06-15'"
        ") AS in_range FROM `p.rds2.evts` ORDER BY id",
    )
    rows = list(job.result())
    assert all(row.in_range for row in rows)
    assert [row.id for row in rows] == [1, 2, 3, 4, 5]


def test_range_overlaps_and_intersect(client) -> None:
    """``RANGE_OVERLAPS`` and ``RANGE_INTERSECT`` evaluate correctly.

    We project each component as its own field so the BQ Python client
    can decode them with primitive types — STRUCT projection through
    the client is brittle because the emulator does not embed the
    rangeElementType metadata for ad-hoc expressions.
    """
    job = client.query(
        "SELECT "
        "RANGE_OVERLAPS("
        "  RANGE(DATE '2024-01-01', DATE '2024-06-30'),"
        "  RANGE(DATE '2024-04-01', DATE '2024-09-30')"
        ") AS overlaps, "
        "RANGE_INTERSECT("
        "  RANGE(DATE '2024-01-01', DATE '2024-06-30'),"
        "  RANGE(DATE '2024-04-01', DATE '2024-09-30')"
        ").start AS isect_start, "
        "RANGE_INTERSECT("
        "  RANGE(DATE '2024-01-01', DATE '2024-06-30'),"
        "  RANGE(DATE '2024-04-01', DATE '2024-09-30')"
        ").end AS isect_end",
    )
    row = next(iter(job.result()))
    assert row.overlaps is True
    assert row.isect_start == dt.date(2024, 4, 1)
    assert row.isect_end == dt.date(2024, 6, 30)
