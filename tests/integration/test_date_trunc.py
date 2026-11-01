"""Integration tests for ``DATE_TRUNC`` over table columns via the REST API.

Exercises the production query path end-to-end: the catalog schema
snapshot feeds the translator's type annotation, the translated SQL
runs on DuckDB, and the REST schema reports each column's BigQuery
type to the official Python client.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

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


@pytest.fixture
def events(client) -> str:
    """Create ``p.trunc_ds.events`` holding one Wednesday 2024-05-15 row."""
    client.create_dataset("trunc_ds", exists_ok=True)
    client.query(
        "CREATE OR REPLACE TABLE trunc_ds.events (d DATE, dt DATETIME, ts TIMESTAMP)",
    ).result()
    client.query(
        "INSERT INTO trunc_ds.events VALUES "
        "(DATE '2024-05-15', DATETIME '2024-05-15 13:45:07', "
        "TIMESTAMP '2024-05-15 13:45:07 UTC')",
    ).result()
    return "trunc_ds.events"


def test_week_granularities_over_date_column(client, events: str) -> None:
    """Each week granularity starts on its BigQuery weekday and stays DATE."""
    job = client.query(
        "SELECT DATE_TRUNC(d, WEEK) AS week_sunday, "
        "DATE_TRUNC(d, WEEK(MONDAY)) AS week_monday, "
        "DATE_TRUNC(d, WEEK(WEDNESDAY)) AS week_wednesday, "
        "DATE_TRUNC(d, WEEK(SATURDAY)) AS week_saturday, "
        "DATE_TRUNC(d, ISOWEEK) AS isoweek "
        f"FROM {events}",
    )
    row = next(iter(job.result()))
    assert {field.name: field.field_type for field in job.result().schema} == {
        "week_sunday": "DATE",
        "week_monday": "DATE",
        "week_wednesday": "DATE",
        "week_saturday": "DATE",
        "isoweek": "DATE",
    }
    assert row.week_sunday == dt.date(2024, 5, 12)
    assert row.week_monday == dt.date(2024, 5, 13)
    assert row.week_wednesday == dt.date(2024, 5, 15)
    assert row.week_saturday == dt.date(2024, 5, 11)
    assert row.isoweek == dt.date(2024, 5, 13)


def test_calendar_granularities_over_date_column(client, events: str) -> None:
    """``DAY`` / ``MONTH`` / ``QUARTER`` / ``YEAR`` / ``ISOYEAR`` return DATE."""
    job = client.query(
        "SELECT DATE_TRUNC(d, DAY) AS day, DATE_TRUNC(d, MONTH) AS month, "
        "DATE_TRUNC(d, QUARTER) AS quarter, DATE_TRUNC(d, YEAR) AS year, "
        f"DATE_TRUNC(d, ISOYEAR) AS isoyear FROM {events}",
    )
    row = next(iter(job.result()))
    assert {field.field_type for field in job.result().schema} == {"DATE"}
    assert (row.day, row.month, row.quarter, row.year, row.isoyear) == (
        dt.date(2024, 5, 15),
        dt.date(2024, 5, 1),
        dt.date(2024, 4, 1),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 1),
    )


def test_week_over_datetime_and_timestamp_keeps_operand_type(client, events: str) -> None:
    """``DATE_TRUNC`` returns the operand's type for DATETIME and TIMESTAMP inputs."""
    job = client.query(
        "SELECT DATE_TRUNC(dt, WEEK) AS dt_week, DATE_TRUNC(ts, WEEK(MONDAY)) AS ts_week "
        f"FROM {events}",
    )
    row = next(iter(job.result()))
    assert {field.name: field.field_type for field in job.result().schema} == {
        "dt_week": "DATETIME",
        "ts_week": "TIMESTAMP",
    }
    assert row.dt_week == dt.datetime(2024, 5, 12)  # noqa: DTZ001 — DATETIME is naive
    assert row.ts_week == dt.datetime(2024, 5, 13, tzinfo=dt.UTC)


def test_date_plus_interval_operand_is_datetime(client, events: str) -> None:
    """``date + INTERVAL`` is DATETIME in BigQuery, so its truncation is too."""
    job = client.query(f"SELECT DATE_TRUNC(d + INTERVAL 1 DAY, MONTH) AS m FROM {events}")
    row = next(iter(job.result()))
    assert [field.field_type for field in job.result().schema] == ["DATETIME"]
    assert row.m == dt.datetime(2024, 5, 1)  # noqa: DTZ001 — DATETIME is naive


def test_timestamp_and_datetime_trunc_over_date_column_are_datetime(client, events: str) -> None:
    """A DATE operand coerces to DATETIME, so these truncations do not return DATE."""
    job = client.query(
        "SELECT TIMESTAMP_TRUNC(d, MONTH) AS ts_month, DATETIME_TRUNC(d, MONTH) AS dt_month "
        f"FROM {events}",
    )
    row = next(iter(job.result()))
    assert {field.name: field.field_type for field in job.result().schema} == {
        "ts_month": "DATETIME",
        "dt_month": "DATETIME",
    }
    assert row.ts_month == row.dt_month == dt.datetime(2024, 5, 1)  # noqa: DTZ001


def test_date_trunc_leaves_the_rest_of_the_query_intact(client, events: str) -> None:
    """A NUMERIC literal beside ``DATE_TRUNC`` keeps its full precision."""
    job = client.query(
        f"SELECT NUMERIC '1.123456789' AS n, DATE_TRUNC(d, MONTH) AS m FROM {events}",
    )
    row = next(iter(job.result()))
    assert [field.field_type for field in job.result().schema] == ["NUMERIC", "DATE"]
    assert (row.n, row.m) == (Decimal("1.123456789"), dt.date(2024, 5, 1))


def test_date_trunc_over_information_schema(client, events: str) -> None:
    """``DATE_TRUNC`` works over an ``INFORMATION_SCHEMA`` view's columns."""
    job = client.query(
        "SELECT table_name, DATE(creation_time) AS created, "
        "DATE_TRUNC(DATE(creation_time), YEAR) AS created_year "
        "FROM trunc_ds.INFORMATION_SCHEMA.TABLES WHERE table_name = 'events'",
    )
    (row,) = job.result()
    assert [field.field_type for field in job.result().schema] == ["STRING", "DATE", "DATE"]
    assert row.table_name == "events"
    assert row.created_year == dt.date(row.created.year, 1, 1)
