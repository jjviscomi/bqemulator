"""E2E: Phase 7 versioning — time travel, snapshots, clones, MVs.

Exercises the Phase 7 ship criterion in a single end-to-end pass
through the official ``google-cloud-bigquery`` Python client against a
live container. Each subtest covers one ship-criterion flow:

1. ``FOR SYSTEM_TIME AS OF TIMESTAMP '...'`` returns the historical row
   state from before a DML change.
2. ``CREATE SNAPSHOT TABLE ... CLONE ...`` captures a named immutable copy.
3. ``CREATE TABLE ... CLONE ...`` diverges lazily.
4. ``CREATE MATERIALIZED VIEW ... AS ...`` auto-refreshes when its base
   tables change.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
import time

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def bq_client(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    """BigQuery Python client bound to the emulator REST endpoint."""
    client = bigquery.Client(
        project="e2e-versioning",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_rest_url),
    )
    try:
        yield client
    finally:
        client.close()


def _create_dataset(bq_client: bigquery.Client, ds_id: str) -> None:
    dataset = bigquery.Dataset(f"{bq_client.project}.{ds_id}")
    dataset.location = "US"
    bq_client.create_dataset(dataset, exists_ok=True)


def _create_orders(
    bq_client: bigquery.Client,
    ds_id: str,
    table_id: str = "orders",
) -> None:
    table = bigquery.Table(
        f"{bq_client.project}.{ds_id}.{table_id}",
        schema=[
            bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("country", "STRING"),
            bigquery.SchemaField("amount", "INT64"),
        ],
    )
    bq_client.create_table(table, exists_ok=True)


def test_versioning_time_travel(bq_client: bigquery.Client) -> None:
    """`FOR SYSTEM_TIME AS OF` returns the pre-change rows."""
    ds_id = "versioning_tt"
    try:
        _create_dataset(bq_client, ds_id)
        _create_orders(bq_client, ds_id)

        bq_client.query(
            f"INSERT INTO `{bq_client.project}.{ds_id}.orders` VALUES (1, 'US', 10), (2, 'US', 20)",
        ).result()

        # Wait long enough that the next write produces a strictly later
        # snapshot timestamp.
        time.sleep(0.05)
        boundary = datetime.now(tz=UTC).replace(tzinfo=None)
        time.sleep(0.05)

        bq_client.query(
            f"INSERT INTO `{bq_client.project}.{ds_id}.orders` VALUES (3, 'CA', 30)",
        ).result()

        target = boundary.isoformat(sep=" ", timespec="microseconds")
        rows = list(
            bq_client.query(
                f"SELECT id FROM `{bq_client.project}.{ds_id}.orders` "
                f"FOR SYSTEM_TIME AS OF TIMESTAMP '{target}' ORDER BY id",
            ).result(),
        )
        assert [r["id"] for r in rows] == [1, 2]

        live_rows = list(
            bq_client.query(
                f"SELECT id FROM `{bq_client.project}.{ds_id}.orders` ORDER BY id",
            ).result(),
        )
        assert [r["id"] for r in live_rows] == [1, 2, 3]
    finally:
        bq_client.delete_dataset(
            f"{bq_client.project}.{ds_id}",
            delete_contents=True,
            not_found_ok=True,
        )


def test_versioning_snapshot_table(bq_client: bigquery.Client) -> None:
    """`CREATE SNAPSHOT TABLE ... CLONE ...` produces an immutable copy."""
    ds_id = "versioning_snap"
    try:
        _create_dataset(bq_client, ds_id)
        _create_orders(bq_client, ds_id)

        bq_client.query(
            f"INSERT INTO `{bq_client.project}.{ds_id}.orders` VALUES (1, 'US', 10), (2, 'US', 20)",
        ).result()
        bq_client.query(
            f"CREATE SNAPSHOT TABLE `{bq_client.project}.{ds_id}.orders_snap` "
            f"CLONE `{bq_client.project}.{ds_id}.orders`",
        ).result()
        bq_client.query(
            f"INSERT INTO `{bq_client.project}.{ds_id}.orders` VALUES (3, 'CA', 30)",
        ).result()

        snap_rows = list(
            bq_client.query(
                f"SELECT id FROM `{bq_client.project}.{ds_id}.orders_snap` ORDER BY id",
            ).result(),
        )
        assert [r["id"] for r in snap_rows] == [1, 2]

        # The snapshot table appears in tables.list with type=SNAPSHOT.
        ref = bq_client.get_table(f"{bq_client.project}.{ds_id}.orders_snap")
        assert ref.table_type == "SNAPSHOT"
    finally:
        bq_client.delete_dataset(
            f"{bq_client.project}.{ds_id}",
            delete_contents=True,
            not_found_ok=True,
        )


def test_versioning_clone(bq_client: bigquery.Client) -> None:
    """`CREATE TABLE ... CLONE` diverges from the source on subsequent DML."""
    ds_id = "versioning_clone"
    try:
        _create_dataset(bq_client, ds_id)
        _create_orders(bq_client, ds_id)

        bq_client.query(
            f"INSERT INTO `{bq_client.project}.{ds_id}.orders` VALUES (1, 'US', 10)",
        ).result()
        bq_client.query(
            f"CREATE TABLE `{bq_client.project}.{ds_id}.workcopy` "
            f"CLONE `{bq_client.project}.{ds_id}.orders`",
        ).result()
        bq_client.query(
            f"INSERT INTO `{bq_client.project}.{ds_id}.workcopy` VALUES (99, 'NZ', 999)",
        ).result()

        src_rows = list(
            bq_client.query(
                f"SELECT id FROM `{bq_client.project}.{ds_id}.orders`",
            ).result(),
        )
        clone_rows = list(
            bq_client.query(
                f"SELECT id FROM `{bq_client.project}.{ds_id}.workcopy` ORDER BY id",
            ).result(),
        )
        assert [r["id"] for r in src_rows] == [1]
        assert [r["id"] for r in clone_rows] == [1, 99]

        ref = bq_client.get_table(f"{bq_client.project}.{ds_id}.workcopy")
        assert ref.table_type == "CLONE"
    finally:
        bq_client.delete_dataset(
            f"{bq_client.project}.{ds_id}",
            delete_contents=True,
            not_found_ok=True,
        )


def test_versioning_materialized_view(bq_client: bigquery.Client) -> None:
    """`CREATE MATERIALIZED VIEW` auto-refreshes after a base-table change."""
    ds_id = "versioning_mv"
    try:
        _create_dataset(bq_client, ds_id)
        _create_orders(bq_client, ds_id)

        bq_client.query(
            f"INSERT INTO `{bq_client.project}.{ds_id}.orders` "
            "VALUES (1, 'US', 10), (2, 'US', 5), (3, 'CA', 20)",
        ).result()

        bq_client.query(
            f"CREATE MATERIALIZED VIEW `{bq_client.project}.{ds_id}.country_totals` AS "
            f"SELECT country, SUM(amount) AS total "
            f"FROM `{bq_client.project}.{ds_id}.orders` GROUP BY country",
        ).result()

        rows = sorted(
            (r["country"], r["total"])
            for r in bq_client.query(
                f"SELECT country, total FROM `{bq_client.project}.{ds_id}.country_totals`",
            ).result()
        )
        assert rows == [("CA", 20), ("US", 15)]

        # Add a row to the base table — the MV must auto-refresh.
        bq_client.query(
            f"INSERT INTO `{bq_client.project}.{ds_id}.orders` VALUES (4, 'US', 100)",
        ).result()
        rows_after = sorted(
            (r["country"], r["total"])
            for r in bq_client.query(
                f"SELECT country, total FROM `{bq_client.project}.{ds_id}.country_totals`",
            ).result()
        )
        assert rows_after == [("CA", 20), ("US", 115)]

        # INFORMATION_SCHEMA.MATERIALIZED_VIEWS lists the view.
        info_rows = list(
            bq_client.query(
                f"SELECT table_name FROM "
                f"`{bq_client.project}.{ds_id}`.INFORMATION_SCHEMA.MATERIALIZED_VIEWS",
            ).result(),
        )
        assert any(r["table_name"] == "country_totals" for r in info_rows)
    finally:
        bq_client.delete_dataset(
            f"{bq_client.project}.{ds_id}",
            delete_contents=True,
            not_found_ok=True,
        )
