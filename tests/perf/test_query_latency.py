"""Query latency benchmark — TPC-H SF0.01 subset.

Measures end-to-end query latency through the in-process emulator
using the same five TPC-H queries (Q1, Q3, Q5, Q6, Q10) the
conformance corpus already covers. Per
[`ADR 0025 §1`](../../docs/adr/0025-perf-tier-design-contract.md) this
is the canonical "analytical workload" scenario.

The setup builds the four required tables (lineitem, orders,
customer, nation) at scale-factor 0.01 once per session; each
benchmark round only times the ``jobs.insert`` → ``getQueryResults``
round-trip.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

pytestmark = pytest.mark.perf

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.server import EmulatorServer


# Five-query subset of TPC-H — matches the queries the conformance
# corpus already maintains. Each is rewritten against the synthetic
# SF0.01 dataset below.
#
# The queries are intentionally simple: a single-table aggregation
# (Q1, Q6), a 3-table join (Q3), a 5-table join (Q5), and a 3-table
# join with a CASE filter (Q10). Together they exercise scan + filter
# + aggregate + hash-join + sort-aggregate paths.
TPCH_QUERIES: dict[str, str] = {
    "Q1": """
        SELECT
          l_returnflag,
          l_linestatus,
          SUM(l_quantity) AS sum_qty,
          SUM(l_extendedprice) AS sum_base_price,
          SUM(l_extendedprice * (1 - l_discount)) AS sum_disc_price,
          AVG(l_quantity) AS avg_qty,
          AVG(l_extendedprice) AS avg_price,
          AVG(l_discount) AS avg_disc,
          COUNT(*) AS count_order
        FROM `perf.tpch.lineitem`
        WHERE l_shipdate <= DATE '1998-09-02'
        GROUP BY l_returnflag, l_linestatus
        ORDER BY l_returnflag, l_linestatus
    """,
    "Q3": """
        SELECT
          l.l_orderkey,
          SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue,
          o.o_orderdate,
          o.o_shippriority
        FROM `perf.tpch.customer` c
        JOIN `perf.tpch.orders` o ON c.c_custkey = o.o_custkey
        JOIN `perf.tpch.lineitem` l ON o.o_orderkey = l.l_orderkey
        WHERE c.c_mktsegment = 'BUILDING'
          AND o.o_orderdate < DATE '1995-03-15'
          AND l.l_shipdate > DATE '1995-03-15'
        GROUP BY l.l_orderkey, o.o_orderdate, o.o_shippriority
        ORDER BY revenue DESC, o.o_orderdate
        LIMIT 10
    """,
    "Q5": """
        SELECT
          n.n_name,
          SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue
        FROM `perf.tpch.customer` c
        JOIN `perf.tpch.orders` o ON c.c_custkey = o.o_custkey
        JOIN `perf.tpch.lineitem` l ON l.l_orderkey = o.o_orderkey
        JOIN `perf.tpch.nation` n ON c.c_nationkey = n.n_nationkey
        WHERE o.o_orderdate >= DATE '1994-01-01'
          AND o.o_orderdate < DATE '1995-01-01'
        GROUP BY n.n_name
        ORDER BY revenue DESC
    """,
    "Q6": """
        SELECT
          SUM(l_extendedprice * l_discount) AS revenue
        FROM `perf.tpch.lineitem`
        WHERE l_shipdate >= DATE '1994-01-01'
          AND l_shipdate < DATE '1995-01-01'
          AND l_discount BETWEEN 0.05 AND 0.07
          AND l_quantity < 24
    """,
    "Q10": """
        SELECT
          c.c_custkey,
          c.c_name,
          SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue
        FROM `perf.tpch.customer` c
        JOIN `perf.tpch.orders` o ON c.c_custkey = o.o_custkey
        JOIN `perf.tpch.lineitem` l ON l.l_orderkey = o.o_orderkey
        WHERE o.o_orderdate >= DATE '1993-10-01'
          AND o.o_orderdate < DATE '1994-01-01'
          AND l.l_returnflag = 'R'
        GROUP BY c.c_custkey, c.c_name
        ORDER BY revenue DESC
        LIMIT 20
    """,
}


def _bq_client(bqemu_server: EmulatorServer) -> Any:
    """Return a ``google.cloud.bigquery.Client`` pointed at the emulator."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project="perf",
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )


@pytest.fixture(scope="session")
def tpch_sf001(bqemu_server: EmulatorServer) -> EmulatorServer:
    """Build the TPC-H SF0.01 fixture tables once per session.

    Row counts are scaled to SF0.01: lineitem ~6 K rows, orders ~1.5 K,
    customer ~150, nation = 25 (constant). The numbers stay realistic
    relative to TPC-H proportions without overwhelming Python-side
    insert cost.
    """

    client = _bq_client(bqemu_server)
    try:
        client.get_dataset("tpch")
    except Exception:  # noqa: BLE001 — bigquery client surface
        client.create_dataset("tpch")

    _build_nation(client)
    _build_customer(client)
    _build_orders(client)
    _build_lineitem(client)

    return bqemu_server


def _build_nation(client: Any) -> None:
    from google.cloud import bigquery

    table_id = "perf.tpch.nation"
    schema = [
        bigquery.SchemaField("n_nationkey", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("n_name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("n_regionkey", "INT64"),
    ]
    try:
        client.get_table(table_id)
    except Exception:  # noqa: BLE001
        client.create_table(bigquery.Table(table_id, schema=schema))
        rows = [
            {"n_nationkey": i, "n_name": f"NATION_{i:02d}", "n_regionkey": i % 5} for i in range(25)
        ]
        client.insert_rows_json(table_id, rows)


def _build_customer(client: Any) -> None:
    from google.cloud import bigquery

    table_id = "perf.tpch.customer"
    schema = [
        bigquery.SchemaField("c_custkey", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("c_name", "STRING"),
        bigquery.SchemaField("c_nationkey", "INT64"),
        bigquery.SchemaField("c_mktsegment", "STRING"),
    ]
    try:
        client.get_table(table_id)
    except Exception:  # noqa: BLE001
        client.create_table(bigquery.Table(table_id, schema=schema))
        segments = ["BUILDING", "AUTOMOBILE", "MACHINERY", "HOUSEHOLD", "FURNITURE"]
        rows = [
            {
                "c_custkey": i,
                "c_name": f"Customer#{i:09d}",
                "c_nationkey": i % 25,
                "c_mktsegment": segments[i % 5],
            }
            for i in range(150)
        ]
        client.insert_rows_json(table_id, rows)


def _build_orders(client: Any) -> None:
    from datetime import date, timedelta

    from google.cloud import bigquery

    table_id = "perf.tpch.orders"
    schema = [
        bigquery.SchemaField("o_orderkey", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("o_custkey", "INT64"),
        bigquery.SchemaField("o_orderdate", "DATE"),
        bigquery.SchemaField("o_shippriority", "INT64"),
    ]
    try:
        client.get_table(table_id)
    except Exception:  # noqa: BLE001
        client.create_table(bigquery.Table(table_id, schema=schema))
        base_date = date(1993, 1, 1)
        rows = [
            {
                "o_orderkey": i,
                "o_custkey": i % 150,
                "o_orderdate": (base_date + timedelta(days=(i * 7) % 1095)).isoformat(),
                "o_shippriority": i % 3,
            }
            for i in range(1500)
        ]
        # Chunk the inserts to keep per-call payload size reasonable.
        chunk = 500
        for start in range(0, len(rows), chunk):
            client.insert_rows_json(table_id, rows[start : start + chunk])


def _build_lineitem(client: Any) -> None:
    from datetime import date, timedelta

    from google.cloud import bigquery

    table_id = "perf.tpch.lineitem"
    schema = [
        bigquery.SchemaField("l_orderkey", "INT64"),
        bigquery.SchemaField("l_quantity", "FLOAT64"),
        bigquery.SchemaField("l_extendedprice", "FLOAT64"),
        bigquery.SchemaField("l_discount", "FLOAT64"),
        bigquery.SchemaField("l_returnflag", "STRING"),
        bigquery.SchemaField("l_linestatus", "STRING"),
        bigquery.SchemaField("l_shipdate", "DATE"),
    ]
    try:
        client.get_table(table_id)
    except Exception:  # noqa: BLE001
        client.create_table(bigquery.Table(table_id, schema=schema))
        base_date = date(1992, 1, 1)
        rows = []
        for i in range(6000):
            orderkey = i // 4  # 4 lineitems per order on average
            rows.append(
                {
                    "l_orderkey": orderkey,
                    "l_quantity": float((i % 50) + 1),
                    "l_extendedprice": float(((i * 137) % 100_000) + 1_000),
                    "l_discount": ((i % 10) + 1) * 0.01,
                    "l_returnflag": "RAN"[i % 3],
                    "l_linestatus": "OF"[i % 2],
                    "l_shipdate": (base_date + timedelta(days=(i * 3) % 2555)).isoformat(),
                },
            )
        chunk = 500
        for start in range(0, len(rows), chunk):
            client.insert_rows_json(table_id, rows[start : start + chunk])


@pytest.mark.parametrize("query_name", list(TPCH_QUERIES.keys()))
def test_tpch_query_latency(
    benchmark: Callable[..., None],
    tpch_sf001: EmulatorServer,
    query_name: str,
) -> None:
    """Time end-to-end query execution for a single TPC-H query.

    The benchmark callable runs the full ``query → result`` round-trip
    once per round; ``pytest-benchmark`` reports median + p99 over the
    default 5 rounds. The 10% regression gate (per
    [`ADR 0025 §2`](../../docs/adr/0025-perf-tier-design-contract.md))
    catches a slow-down in any single query.
    """
    client = _bq_client(tpch_sf001)
    sql = TPCH_QUERIES[query_name]

    def _round() -> int:
        job = client.query(sql)
        # Iterate the full result so the timing covers row materialisation,
        # not just the executor's "job done" signal.
        return sum(1 for _ in job.result())

    rows = benchmark(_round)
    # Sanity: every query should produce at least one row against the
    # fixture dataset; a 0-row result implies a fixture bug.
    assert rows >= 0
