"""Integration tests for INTERVAL queries against the in-process emulator."""

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


def test_date_plus_interval(client) -> None:
    """``DATE '2024-01-15' + INTERVAL 1 DAY`` → ``2024-01-16``."""
    job = client.query("SELECT DATE '2024-01-15' + INTERVAL 1 DAY AS d")
    row = next(iter(job.result()))
    # DuckDB widens DATE + INTERVAL to TIMESTAMP. The calendar date
    # must match.
    out = row.d
    if isinstance(out, dt.datetime):
        out = out.date()
    assert out == dt.date(2024, 1, 16)


def test_justify_hours(client) -> None:
    """``JUSTIFY_HOURS(INTERVAL 36 HOUR)`` → 1 day + 12 hours.

    ADR 0023 §1.G (2026-05-16): the wire format reports the column as
    ``type=INTERVAL``, so the BigQuery Python client parses the
    canonical ``0-0 1 12:0:0`` string into a :class:`dateutil.relativedelta`.
    """
    from dateutil.relativedelta import relativedelta

    job = client.query("SELECT JUSTIFY_HOURS(INTERVAL 36 HOUR) AS i")
    row = next(iter(job.result()))
    val = row.i
    assert isinstance(val, relativedelta)
    assert val == relativedelta(days=1, hours=12)


def test_compound_interval_literal(client) -> None:
    """``INTERVAL '1-2 3 4:5:6' YEAR TO SECOND`` parses and evaluates."""
    # Build a TIMESTAMP arithmetic expression so we end up with a
    # concrete shifted timestamp we can compare.
    job = client.query(
        "SELECT TIMESTAMP '2024-01-01 00:00:00 UTC' + "
        "INTERVAL '0-2 3 4:5:6' YEAR TO SECOND AS shifted",
    )
    row = next(iter(job.result()))
    # +0y2mo3d4h5m6s on 2024-01-01 → 2024-03-04 04:05:06 UTC
    expected = dt.datetime(2024, 3, 4, 4, 5, 6, tzinfo=dt.UTC)
    assert row.shifted.replace(tzinfo=dt.UTC) == expected
