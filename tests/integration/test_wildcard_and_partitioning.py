"""Integration tests: wildcard tables and partitioning.

Phase 3 ship-criterion tests.
"""

from __future__ import annotations

import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def _make_client(bqemu_server: EmulatorServer):
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )


class TestWildcardTables:
    def test_wildcard_query_returns_union_of_matching_tables(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """FROM events_* should expand to UNION ALL of events_20260101 etc."""
        from google.cloud import bigquery

        client = _make_client(bqemu_server)
        client.create_dataset("wc_test")

        # Create three tables with a common prefix.
        schema = [bigquery.SchemaField("event_id", "INT64")]
        for suffix in ["20260101", "20260115", "20260201"]:
            t = client.create_table(
                bigquery.Table(f"test-project.wc_test.events_{suffix}", schema=schema),
            )
            client.insert_rows_json(t, [{"event_id": int(suffix)}])

        # Wildcard query.
        rows = list(
            client.query(
                "SELECT event_id, _TABLE_SUFFIX FROM wc_test.events_* ORDER BY event_id",
            ).result(),
        )
        assert len(rows) == 3
        # Verify _TABLE_SUFFIX is present.
        suffixes = {r._TABLE_SUFFIX for r in rows}
        assert suffixes == {"20260101", "20260115", "20260201"}

        client.delete_dataset("wc_test", delete_contents=True)

    def test_wildcard_with_suffix_filter(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """WHERE _TABLE_SUFFIX filters which tables are included."""
        from google.cloud import bigquery

        client = _make_client(bqemu_server)
        client.create_dataset("wc_filter")

        schema = [bigquery.SchemaField("x", "INT64")]
        for suffix in ["a", "b", "c"]:
            t = client.create_table(
                bigquery.Table(f"test-project.wc_filter.tbl_{suffix}", schema=schema),
            )
            client.insert_rows_json(t, [{"x": ord(suffix)}])

        # Query with suffix filter — should only get 'a' and 'b'.
        rows = list(
            client.query(
                "SELECT x, _TABLE_SUFFIX FROM wc_filter.tbl_* "
                "WHERE _TABLE_SUFFIX IN ('a', 'b') ORDER BY x",
            ).result(),
        )
        assert len(rows) == 2
        assert {r._TABLE_SUFFIX for r in rows} == {"a", "b"}

        client.delete_dataset("wc_filter", delete_contents=True)


class TestPartitionedTable:
    def test_create_partitioned_table(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Create a table with time partitioning and verify metadata."""
        from google.cloud import bigquery

        client = _make_client(bqemu_server)
        client.create_dataset("part_test")

        table = bigquery.Table("test-project.part_test.events")
        table.schema = [
            bigquery.SchemaField("event_id", "INT64"),
            bigquery.SchemaField("event_date", "DATE"),
        ]
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="event_date",
        )
        created = client.create_table(table)

        # Verify the partitioning metadata is returned.
        fetched = client.get_table(created)
        assert fetched.time_partitioning is not None
        assert fetched.time_partitioning.type_ == "DAY"
        assert fetched.time_partitioning.field == "event_date"

        client.delete_dataset("part_test", delete_contents=True)

    def test_create_clustered_table(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Create a table with clustering and verify metadata."""
        from google.cloud import bigquery

        client = _make_client(bqemu_server)
        client.create_dataset("clust_test")

        table = bigquery.Table("test-project.clust_test.logs")
        table.schema = [
            bigquery.SchemaField("ts", "TIMESTAMP"),
            bigquery.SchemaField("level", "STRING"),
            bigquery.SchemaField("message", "STRING"),
        ]
        table.clustering_fields = ["level"]
        created = client.create_table(table)

        fetched = client.get_table(created)
        assert fetched.clustering_fields is not None
        assert "level" in fetched.clustering_fields

        client.delete_dataset("clust_test", delete_contents=True)

    def test_partitioned_table_insert_and_query(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Insert into a partitioned table and query it."""
        from google.cloud import bigquery

        client = _make_client(bqemu_server)
        client.create_dataset("part_query")

        table = bigquery.Table("test-project.part_query.events")
        table.schema = [
            bigquery.SchemaField("event_id", "INT64"),
            bigquery.SchemaField("event_date", "DATE"),
            bigquery.SchemaField("data", "STRING"),
        ]
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="event_date",
        )
        created = client.create_table(table)

        # Insert rows.
        client.insert_rows_json(
            created,
            [
                {"event_id": 1, "event_date": "2026-04-15", "data": "a"},
                {"event_id": 2, "event_date": "2026-04-16", "data": "b"},
                {"event_id": 3, "event_date": "2026-04-15", "data": "c"},
            ],
        )

        # Query with partition filter.
        rows = list(
            client.query(
                "SELECT event_id FROM part_query.events "
                "WHERE event_date = DATE '2026-04-15' ORDER BY event_id",
            ).result(),
        )
        assert len(rows) == 2
        assert rows[0].event_id == 1
        assert rows[1].event_id == 3

        client.delete_dataset("part_query", delete_contents=True)
