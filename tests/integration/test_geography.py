"""Integration tests for GEOGRAPHY queries against the in-process emulator.

Exercises the Phase 9 spatial workflow end-to-end:

1. Create a dataset + table with a GEOGRAPHY column.
2. Insert rows via insertAll using WKT strings.
3. Run spatial queries (``ST_DWITHIN``, ``ST_INTERSECTS``) and verify
   results.
4. Read back the GEOGRAPHY column and verify WKT round-trip.
"""

from __future__ import annotations

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


def test_geography_round_trip_via_insert_all(client) -> None:
    """Insert WKT strings via insertAll, read back, query spatially."""
    from google.cloud import bigquery

    client.create_dataset("ds")
    schema = [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("location", "GEOGRAPHY"),
    ]
    table = client.create_table(bigquery.Table("p.ds.places", schema=schema))
    assert table.schema[1].field_type == "GEOGRAPHY"

    # insertAll with WKT — converted to hex-WKB by the emulator and
    # cast back through ST_GeomFromHEXWKB on INSERT.
    errors = client.insert_rows_json(
        "p.ds.places",
        [
            {"id": 1, "location": "POINT(0 0)"},
            {"id": 2, "location": "POINT(3 4)"},
            {"id": 3, "location": "POINT(10 10)"},
        ],
    )
    assert errors == []

    # Ship-criterion spatial query. ``ST_DWITHIN``'s threshold is in
    # spheroidal metres on the S2 sphere (P2.g closure) — (3, 4) is
    # ~556 km from (0, 0) and (10, 10) is ~1.56 million m, so a
    # 1 000 000 m threshold returns the first two ids.
    query_job = client.query(
        "SELECT id FROM `p.ds.places` "
        "WHERE ST_DWITHIN(location, ST_GEOGFROMTEXT('POINT(0 0)'), 1000000) "
        "ORDER BY id",
    )
    rows = [row.id for row in query_job.result()]
    assert rows == [1, 2]


def test_geography_intersects(client) -> None:
    """``ST_INTERSECTS`` returns correct results for crossing geometries."""
    from google.cloud import bigquery

    client.create_dataset("ds2")
    schema = [
        bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("shape", "GEOGRAPHY"),
    ]
    client.create_table(bigquery.Table("p.ds2.shapes", schema=schema))

    client.insert_rows_json(
        "p.ds2.shapes",
        [
            {"name": "horizontal", "shape": "LINESTRING(0 1, 5 1)"},
            {"name": "vertical", "shape": "LINESTRING(2 0, 2 5)"},
            {"name": "parallel", "shape": "LINESTRING(0 3, 5 3)"},
        ],
    )

    query_job = client.query(
        "SELECT name FROM `p.ds2.shapes` "
        "WHERE ST_INTERSECTS(shape, ST_GEOGFROMTEXT('LINESTRING(2 0, 2 5)')) "
        "ORDER BY name",
    )
    names = [row.name for row in query_job.result()]
    # ``horizontal`` crosses the vertical line and ``vertical`` IS the
    # vertical line — both intersect; ``parallel`` does too at (2,3).
    assert names == ["horizontal", "parallel", "vertical"]


def test_geography_as_text_returns_wkt(client) -> None:
    """``ST_AsText`` emits the canonical WKT string."""
    from google.cloud import bigquery

    client.create_dataset("ds3")
    schema = [bigquery.SchemaField("loc", "GEOGRAPHY")]
    client.create_table(bigquery.Table("p.ds3.t", schema=schema))
    client.insert_rows_json("p.ds3.t", [{"loc": "POINT(1 2)"}])
    job = client.query("SELECT ST_ASTEXT(loc) AS wkt FROM `p.ds3.t`")
    row = next(iter(job.result()))
    assert "POINT" in row.wkt
    assert "1" in row.wkt and "2" in row.wkt
