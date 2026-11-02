"""Integration tests for NUMERIC / BIGNUMERIC result types via the REST API.

``AVG`` and ``/`` over decimal operands run through the translator's
type-aware rules; the REST schema reports each column's BigQuery type to
the official Python client. BigQuery returns NUMERIC for NUMERIC
operands and BIGNUMERIC when any operand is BIGNUMERIC, whether or not
the statement reads a catalog table.
"""

from __future__ import annotations

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


@pytest.mark.parametrize(
    ("sql", "field_type", "expected"),
    [
        (
            "SELECT AVG(x) AS v FROM UNNEST([NUMERIC '1.5', NUMERIC '2.5']) AS x",
            "NUMERIC",
            Decimal(2),
        ),
        (
            (
                "SELECT AVG(x) AS v FROM UNNEST([BIGNUMERIC '0.0000000001', "
                "BIGNUMERIC '0.0000000003']) AS x"
            ),
            "BIGNUMERIC",
            Decimal("2E-10"),
        ),
        ("SELECT BIGNUMERIC '0.0000000001' / 1 AS v", "BIGNUMERIC", Decimal("1E-10")),
        ("SELECT BIGNUMERIC '1' / 3 AS v", "BIGNUMERIC", Decimal("0.3333333333")),
        ("SELECT PARSE_BIGNUMERIC('0.0000000001') / 1 AS v", "BIGNUMERIC", Decimal("1E-10")),
        (
            "SELECT x / 1 AS v FROM UNNEST([BIGNUMERIC '0.0000000001']) AS x",
            "BIGNUMERIC",
            Decimal("1E-10"),
        ),
    ],
)
def test_decimal_result_type_without_a_table(
    client, sql: str, field_type: str, expected: Decimal
) -> None:
    """A BIGNUMERIC operand keeps its type and its tenth fractional digit."""
    job = client.query(sql)
    row = next(iter(job.result()))
    assert [field.field_type for field in job.result().schema] == [field_type]
    assert row.v == expected
