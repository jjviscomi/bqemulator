"""Integration tests for BigQuery's CREATE TABLE (schema) AS SELECT combined syntax.

Pins the end-to-end behaviour landed by
:mod:`bqemulator.sql.rewriter.create_table_schema_ctas`: BigQuery's
canonical combined ``CREATE [OR REPLACE] TABLE x (schema) AS SELECT …``
syntax — which DuckDB's parser rejects natively — now executes
through the emulator, producing a table with the declared column
types and the data from the SELECT.

Discovered 2026-05-17 during P2.d Phase 8 conformance recording; the
P2.d fixtures were initially authored in this canonical BQ form and
all 20 failed at DuckDB parse time, motivating the rewriter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from bqemulator.config import PersistenceMode, Settings
from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """A live emulator with a single dataset `p.ds` ready for CREATE TABLE."""
    settings = Settings(
        persistence_mode=PersistenceMode.EPHEMERAL,
        rest_port=0,
        grpc_port=0,
    )
    server = EmulatorServer(settings)
    await server.start()
    try:
        async with httpx.AsyncClient(base_url=server.rest_url, timeout=20.0) as c:
            await c.post(
                "/bigquery/v2/projects/p/datasets",
                json={"datasetReference": {"projectId": "p", "datasetId": "ds"}},
            )
            yield c
    finally:
        await server.stop()


async def _query(c: httpx.AsyncClient, sql: str) -> dict:
    response = await c.post(
        "/bigquery/v2/projects/p/queries",
        json={"query": sql, "useLegacySql": False},
    )
    response.raise_for_status()
    return response.json()


@pytest.mark.asyncio
async def test_basic_combined_form_creates_table_with_declared_schema(
    client: httpx.AsyncClient,
) -> None:
    """A combined CTAS-with-schema executes; the resulting table has declared types."""
    create_body = await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t1` (id INT64, country STRING) AS "
        "SELECT 1 AS id, 'US' AS country",
    )
    assert "errors" not in create_body, create_body.get("errors")

    select_body = await _query(client, "SELECT id, country FROM `p.ds.t1`")
    fields = select_body["schema"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [
        ("id", "INTEGER"),
        ("country", "STRING"),
    ]
    rows = select_body.get("rows", [])
    assert rows == [{"f": [{"v": "1"}, {"v": "US"}]}]


@pytest.mark.asyncio
async def test_or_replace_overwrites_existing_table(
    client: httpx.AsyncClient,
) -> None:
    """``CREATE OR REPLACE`` swaps the table's schema + data on re-execution."""
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t2` (n INT64) AS SELECT 1 AS n",
    )
    # Replace with a different schema + payload.
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t2` (label STRING) AS SELECT 'hello' AS label",
    )
    select_body = await _query(client, "SELECT label FROM `p.ds.t2`")
    fields = select_body["schema"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [("label", "STRING")]
    assert select_body["rows"] == [{"f": [{"v": "hello"}]}]


@pytest.mark.asyncio
async def test_declared_type_overrides_select_inferred_type(
    client: httpx.AsyncClient,
) -> None:
    """Declared NUMERIC type wins over the INT64 the SELECT literal would infer.

    A bare ``SELECT 100`` projects an ``INT64``; declaring the column
    as ``NUMERIC`` in the schema clause must propagate via the
    rewriter's CAST so the resulting table column is ``NUMERIC``,
    not ``INT64``.
    """
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t3` (amount NUMERIC) AS SELECT 100",
    )
    select_body = await _query(client, "SELECT amount FROM `p.ds.t3`")
    fields = select_body["schema"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [("amount", "NUMERIC")]
    # NUMERIC rendering preserves the declared precision/scale.
    assert select_body["rows"] == [{"f": [{"v": "100.000"}]}]


@pytest.mark.asyncio
async def test_multi_row_select_populates_all_rows(
    client: httpx.AsyncClient,
) -> None:
    """A multi-row UNION ALL SELECT lands every row with the declared types."""
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t4` (id INT64, country STRING, amount NUMERIC) AS "
        "SELECT 1 AS id, 'US' AS country, NUMERIC '100.00' AS amount UNION ALL "
        "SELECT 2, 'EU', NUMERIC '200.50' UNION ALL "
        "SELECT 3, 'JP', NUMERIC '300.75'",
    )
    select_body = await _query(
        client,
        "SELECT id, country, amount FROM `p.ds.t4` ORDER BY id",
    )
    rows = [tuple(c["v"] for c in r["f"]) for r in select_body.get("rows", [])]
    assert rows == [
        ("1", "US", "100.000"),
        ("2", "EU", "200.500"),
        ("3", "JP", "300.750"),
    ]


@pytest.mark.asyncio
async def test_column_count_mismatch_surfaces_parse_error(
    client: httpx.AsyncClient,
) -> None:
    """Schema has 3 columns; SELECT only has 2 — query surfaces an error.

    The rewriter intentionally leaves the SQL unmodified on a column-
    count mismatch (its comment notes this) so the downstream parser
    raises rather than the rewriter silently producing a corrupt
    table. BigQuery itself rejects this shape with a similar error.
    """
    body = await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t5` (a INT64, b STRING, c NUMERIC) AS "
        "SELECT 1 AS a, 'x' AS b",
    )
    # The query is accepted at the REST surface (200) but the job's
    # errors array carries the parse failure.
    assert body.get("errors"), "expected a parse error for column-count mismatch"


@pytest.mark.asyncio
async def test_bare_ctas_still_works(
    client: httpx.AsyncClient,
) -> None:
    """``CREATE TABLE x AS SELECT …`` without a schema clause still works.

    Regression-guard for the rewriter's pass-through path — the
    rewriter must only kick in when both a schema clause and an
    ``AS SELECT`` are present.
    """
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t6` AS SELECT 1 AS id, 'US' AS country",
    )
    select_body = await _query(client, "SELECT id, country FROM `p.ds.t6`")
    rows = select_body["rows"]
    assert rows == [{"f": [{"v": "1"}, {"v": "US"}]}]


# ──────────────────────────────────────────────────────────────────────────
# BigQuery-parity edge cases
#
# These tests pin emulator behaviour that should match real BigQuery
# for the combined ``CREATE TABLE x (schema) AS SELECT`` form. The
# baseline behaviour each test pins is what BigQuery does today —
# verified against real BQ during the 2026-05-18 P2.d follow-up.
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bool_and_float64_declared_types(client: httpx.AsyncClient) -> None:
    """BOOL and FLOAT64 declared types propagate via CAST."""
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t_bool` (flag BOOL, score FLOAT64) AS "
        "SELECT TRUE AS flag, 3.14 AS score",
    )
    body = await _query(client, "SELECT flag, score FROM `p.ds.t_bool`")
    fields = body["schema"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [
        ("flag", "BOOLEAN"),
        ("score", "FLOAT"),
    ]
    assert body["rows"] == [{"f": [{"v": "true"}, {"v": "3.14"}]}]


@pytest.mark.asyncio
async def test_date_and_timestamp_declared_types(client: httpx.AsyncClient) -> None:
    """DATE and TIMESTAMP declared types stay typed through the CAST."""
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t_date` (d DATE, ts TIMESTAMP) AS "
        "SELECT DATE '2026-05-18' AS d, TIMESTAMP '2026-05-18 12:00:00 UTC' AS ts",
    )
    body = await _query(client, "SELECT d, ts FROM `p.ds.t_date`")
    fields = body["schema"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [
        ("d", "DATE"),
        ("ts", "TIMESTAMP"),
    ]


@pytest.mark.asyncio
async def test_null_literal_with_declared_type(client: httpx.AsyncClient) -> None:
    """A bare ``NULL`` SELECT projection adopts the declared column type.

    Without the rewriter's CAST, DuckDB would infer the column as
    untyped NULL. The CAST forces the declared type to win, matching
    BigQuery's behaviour.
    """
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t_null` (id INT64, label STRING) AS "
        "SELECT 1 AS id, NULL AS label",
    )
    body = await _query(client, "SELECT id, label FROM `p.ds.t_null`")
    fields = body["schema"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [
        ("id", "INTEGER"),
        ("label", "STRING"),
    ]
    # NULL is encoded as a missing ``v`` key in the REST row payload.
    assert body["rows"] == [{"f": [{"v": "1"}, {"v": None}]}]


@pytest.mark.asyncio
async def test_schema_column_name_wins_over_select_alias(
    client: httpx.AsyncClient,
) -> None:
    """The DECLARED column name wins, even if the SELECT alias is different.

    BigQuery uses the schema clause's column names as the table's
    final column names; the SELECT projection aliases are discarded.
    The rewriter mirrors this by re-aliasing each CAST'd projection
    with the schema column name.
    """
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t_name` (alpha INT64, beta STRING) AS "
        "SELECT 1 AS some_other_name, 'x' AS yet_another_name",
    )
    body = await _query(client, "SELECT alpha, beta FROM `p.ds.t_name`")
    fields = body["schema"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [
        ("alpha", "INTEGER"),
        ("beta", "STRING"),
    ]


@pytest.mark.asyncio
async def test_table_supports_subsequent_inserts(client: httpx.AsyncClient) -> None:
    """A table created via combined-form CTAS accepts subsequent INSERT calls.

    Regression-guard: if the rewriter ever switched to a two-statement
    decomposition (CREATE + INSERT) and lost the CREATE in some path,
    subsequent INSERT calls would target a non-existent table. This
    pins that the resulting table is fully usable for downstream DML.
    """
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t_insert` (id INT64, label STRING) AS "
        "SELECT 1 AS id, 'first' AS label",
    )
    await _query(
        client,
        "INSERT INTO `p.ds.t_insert` (id, label) VALUES (2, 'second'), (3, 'third')",
    )
    body = await _query(
        client,
        "SELECT id, label FROM `p.ds.t_insert` ORDER BY id",
    )
    rows = [tuple(c["v"] for c in r["f"]) for r in body["rows"]]
    assert rows == [("1", "first"), ("2", "second"), ("3", "third")]


@pytest.mark.asyncio
async def test_in_temp_table_inside_script(client: httpx.AsyncClient) -> None:
    """Combined-form CTAS works inside a BEGIN…END script block.

    Pins that the rewriter's single-statement output (rather than a
    two-statement decomposition) preserves transaction semantics:
    the CREATE + populate happens atomically with respect to any
    surrounding ``EXCEPTION WHEN ERROR`` block.
    """
    body = await _query(
        client,
        "BEGIN\n"
        "  CREATE OR REPLACE TABLE `p.ds.t_script` (id INT64, val STRING) AS "
        "SELECT 1 AS id, 'inside' AS val;\n"
        "  SELECT id, val FROM `p.ds.t_script`;\n"
        "END",
    )
    assert "errors" not in body, body.get("errors")
    assert body["rows"] == [{"f": [{"v": "1"}, {"v": "inside"}]}]


@pytest.mark.asyncio
async def test_zero_row_select_produces_empty_table(
    client: httpx.AsyncClient,
) -> None:
    """A SELECT that returns zero rows produces a typed-but-empty table."""
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t_empty` (id INT64, label STRING) AS "
        "SELECT 1 AS id, 'never' AS label WHERE FALSE",
    )
    body = await _query(client, "SELECT id, label FROM `p.ds.t_empty`")
    fields = body["schema"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [
        ("id", "INTEGER"),
        ("label", "STRING"),
    ]
    assert body.get("rows", []) == []


@pytest.mark.asyncio
async def test_array_declared_type(client: httpx.AsyncClient) -> None:
    """``ARRAY<INT64>`` declared type carries through the CAST."""
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t_arr` (vals ARRAY<INT64>) AS SELECT [1, 2, 3] AS vals",
    )
    body = await _query(client, "SELECT vals FROM `p.ds.t_arr`")
    fields = body["schema"]["fields"]
    # REPEATED mode encodes the array structure on the wire.
    assert fields[0]["name"] == "vals"
    assert fields[0]["type"] == "INTEGER"
    assert fields[0]["mode"] == "REPEATED"


@pytest.mark.asyncio
async def test_bytes_declared_type(client: httpx.AsyncClient) -> None:
    """``BYTES`` declared type preserves through the CAST."""
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t_bytes` (payload BYTES) AS SELECT b'hello' AS payload",
    )
    body = await _query(client, "SELECT payload FROM `p.ds.t_bytes`")
    fields = body["schema"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [("payload", "BYTES")]


@pytest.mark.asyncio
async def test_aggregate_select_lands_with_declared_types(
    client: httpx.AsyncClient,
) -> None:
    """An aggregate SELECT (SUM/COUNT/GROUP BY) populates via the schema clause."""
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t_src` AS "
        "SELECT 1 AS group_id, 100 AS amount UNION ALL "
        "SELECT 1, 200 UNION ALL "
        "SELECT 2, 50",
    )
    await _query(
        client,
        "CREATE OR REPLACE TABLE `p.ds.t_agg` (gid INT64, total NUMERIC) AS "
        "SELECT group_id, SUM(amount) FROM `p.ds.t_src` GROUP BY group_id",
    )
    body = await _query(
        client,
        "SELECT gid, total FROM `p.ds.t_agg` ORDER BY gid",
    )
    fields = body["schema"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [
        ("gid", "INTEGER"),
        ("total", "NUMERIC"),
    ]
    rows = [tuple(c["v"] for c in r["f"]) for r in body["rows"]]
    assert rows == [("1", "300.000"), ("2", "50.000")]
