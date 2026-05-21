"""E2E: Phase 9 GEOGRAPHY / RANGE / INTERVAL against a live container.

Ship criterion (verbatim from
``docs/roadmap/phase-9-specialized-types.md``):

    Queries using ``ST_DWITHIN``, ``ST_INTERSECTS``, ``RANGE_CONTAINS``,
    and ``INTERVAL`` arithmetic return correct results against a live
    bqemulator container in all four client languages, matching
    real-BigQuery output in the conformance corpus.

Each test below targets one of the four ship-criterion functions and
uses the official ``google-cloud-bigquery`` client against
``ghcr.io/jjviscomi/bqemulator:dev``.
"""

from __future__ import annotations

from collections.abc import Iterator
import datetime as dt

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
import httpx
import pytest

pytestmark = pytest.mark.e2e

_PROJECT = "e2e-specialized_types"


@pytest.fixture
def client(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    c = bigquery.Client(
        project=_PROJECT,
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_rest_url),
    )
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def fresh_dataset(client: bigquery.Client) -> Iterator[str]:
    """Per-test dataset to avoid cross-contamination on a long-lived container."""
    import uuid

    ds_id = f"ds_{uuid.uuid4().hex[:8]}"
    client.create_dataset(ds_id)
    try:
        yield ds_id
    finally:
        client.delete_dataset(ds_id, delete_contents=True, not_found_ok=True)


def test_st_dwithin(client: bigquery.Client, fresh_dataset: str) -> None:
    """Ship-criterion: ``ST_DWITHIN`` filters points inside the radius.

    BigQuery's ``ST_DWITHIN(g1, g2, d)`` uses **spheroidal great-circle
    distance in metres** — not planar Euclidean degrees. The fixture
    coordinates and ``600000`` (600 km) threshold are tuned so this test
    passes against both real BigQuery and the emulator's
    ``bqemu_st_distance_spheroidal`` rewrite. POINT(3°, 4°) lies
    ~553 km from POINT(0°, 0°) under WGS-84; POINT(10°, 10°) lies
    ~1571 km away.
    """
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("loc", "GEOGRAPHY"),
    ]
    table_id = f"{_PROJECT}.{fresh_dataset}.places"
    client.create_table(bigquery.Table(table_id, schema=schema))
    errors = client.insert_rows_json(
        table_id,
        [
            {"id": 1, "loc": "POINT(0 0)"},
            {"id": 2, "loc": "POINT(3 4)"},  # ~553 km from origin
            {"id": 3, "loc": "POINT(10 10)"},  # ~1571 km from origin
        ],
    )
    assert errors == []

    job = client.query(
        f"SELECT id FROM `{table_id}` WHERE ST_DWITHIN("
        "loc, ST_GEOGFROMTEXT('POINT(0 0)'), 600000) ORDER BY id",
    )
    assert [r.id for r in job.result()] == [1, 2]


def test_st_intersects(client: bigquery.Client, fresh_dataset: str) -> None:
    """Ship-criterion: ``ST_INTERSECTS`` detects geometric crossings."""
    schema = [
        bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("shape", "GEOGRAPHY"),
    ]
    table_id = f"{_PROJECT}.{fresh_dataset}.shapes"
    client.create_table(bigquery.Table(table_id, schema=schema))
    client.insert_rows_json(
        table_id,
        [
            {"name": "horizontal", "shape": "LINESTRING(0 1, 5 1)"},
            {"name": "vertical", "shape": "LINESTRING(2 0, 2 5)"},
            {"name": "far_away", "shape": "LINESTRING(100 100, 200 200)"},
        ],
    )

    job = client.query(
        f"SELECT name FROM `{table_id}` "
        "WHERE ST_INTERSECTS(shape, ST_GEOGFROMTEXT('LINESTRING(0 0, 5 5)')) "
        "ORDER BY name",
    )
    names = [r.name for r in job.result()]
    # ``horizontal`` crosses at (1,1); ``vertical`` crosses at (2,2);
    # ``far_away`` does not.
    assert names == ["horizontal", "vertical"]


def test_range_contains(client: bigquery.Client) -> None:
    """Ship-criterion: ``RANGE_CONTAINS`` evaluates half-open membership."""
    job = client.query(
        "SELECT "
        "RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), "
        "  DATE '2024-06-15') AS mid, "
        "RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), "
        "  DATE '2024-01-01') AS at_start, "
        "RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), "
        "  DATE '2024-12-31') AS at_end_exclusive",
    )
    row = next(iter(job.result()))
    assert row.mid is True
    assert row.at_start is True  # [start, end) — start IS contained
    assert row.at_end_exclusive is False  # end is NOT contained


def test_interval_arithmetic(client: bigquery.Client) -> None:
    """Ship-criterion: INTERVAL arithmetic on DATE/TIMESTAMP types."""
    job = client.query(
        "SELECT "
        "DATE '2024-01-15' + INTERVAL 1 DAY AS d_next, "
        "TIMESTAMP '2024-01-15 12:00:00 UTC' - INTERVAL 1 HOUR AS ts_prev",
    )
    row = next(iter(job.result()))
    out_date = row.d_next
    if isinstance(out_date, dt.datetime):
        out_date = out_date.date()
    assert out_date == dt.date(2024, 1, 16)
    expected_ts = dt.datetime(2024, 1, 15, 11, 0, tzinfo=dt.UTC)
    assert row.ts_prev.replace(tzinfo=dt.UTC) == expected_ts


def test_schema_round_trip(bqemu_rest_url: str) -> None:
    """REST schema round-trips GEOGRAPHY / RANGE / INTERVAL."""
    import uuid

    ds_id = f"ds_{uuid.uuid4().hex[:8]}"
    httpx.post(
        f"{bqemu_rest_url}/bigquery/v2/projects/{_PROJECT}/datasets",
        json={"datasetReference": {"projectId": _PROJECT, "datasetId": ds_id}},
        timeout=10.0,
    ).raise_for_status()
    try:
        payload = {
            "schema": {
                "fields": [
                    {"name": "g", "type": "GEOGRAPHY"},
                    {"name": "i", "type": "INTERVAL"},
                    {
                        "name": "r",
                        "type": "RANGE",
                        "rangeElementType": {"type": "DATE"},
                    },
                ],
            },
            "tableReference": {
                "projectId": _PROJECT,
                "datasetId": ds_id,
                "tableId": "specialized_types",
            },
        }
        httpx.post(
            f"{bqemu_rest_url}/bigquery/v2/projects/{_PROJECT}/datasets/{ds_id}/tables",
            json=payload,
            timeout=10.0,
        ).raise_for_status()
        response = httpx.get(
            f"{bqemu_rest_url}/bigquery/v2/projects/{_PROJECT}/datasets/{ds_id}/tables/specialized_types",
            timeout=10.0,
        )
        response.raise_for_status()
        fields = response.json()["schema"]["fields"]
        types = {f["name"]: f["type"] for f in fields}
        assert types == {"g": "GEOGRAPHY", "i": "INTERVAL", "r": "RANGE"}
        r_field = next(f for f in fields if f["name"] == "r")
        assert r_field["rangeElementType"]["type"] == "DATE"
    finally:
        httpx.delete(
            f"{bqemu_rest_url}/bigquery/v2/projects/{_PROJECT}/datasets/{ds_id}"
            "?deleteContents=true",
            timeout=10.0,
        )
