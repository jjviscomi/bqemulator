"""Cross-feature integration test.

A single comprehensive test that exercises the core CRUD + query +
streaming-insert + partitioning surfaces together in a realistic
workflow, verifying they compose as a cohesive system rather than
just working in isolation.

This is the "extraordinarily high confidence" smoke test for the
emulator's primary API surface.
"""

from __future__ import annotations

import csv
from pathlib import Path

import httpx
import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def test_full_cross_workflow(  # noqa: PLR0915
    bqemu_server: EmulatorServer,
    tmp_path: Path,
) -> None:
    """End-to-end workflow covering health, CRUD, query, load/extract, wildcards, partitioning.

    Scenario: An analytics team manages event data with daily tables,
    loads CSV data, queries with parameters, uses wildcard tables,
    creates partitioned tables, and verifies everything works.
    """
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    # Health ──────────────────────────────────────────────
    r = httpx.get(f"{bqemu_server.rest_url}/healthz", timeout=5)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    r = httpx.get(f"{bqemu_server.rest_url}/readyz", timeout=5)
    assert r.status_code == 200

    # Dataset + Table CRUD ────────────────────────────────
    ds = client.create_dataset("analytics")
    assert ds.dataset_id == "analytics"

    # Create a regular table.
    orders_schema = [
        bigquery.SchemaField("order_id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("customer", "STRING"),
        bigquery.SchemaField("amount", "NUMERIC"),
        bigquery.SchemaField("order_date", "DATE"),
    ]
    orders = client.create_table(
        bigquery.Table("test-project.analytics.orders", schema=orders_schema),
    )

    # Insert rows via tabledata.insertAll.
    errors = client.insert_rows_json(
        orders,
        [
            {"order_id": 1, "customer": "Alice", "amount": "100.00", "order_date": "2026-04-15"},
            {"order_id": 2, "customer": "Bob", "amount": "250.50", "order_date": "2026-04-15"},
            {"order_id": 3, "customer": "Alice", "amount": "75.00", "order_date": "2026-04-16"},
        ],
    )
    assert errors == []

    # Simple query.
    rows = list(
        client.query(
            "SELECT COUNT(*) AS n FROM analytics.orders",
        ).result()
    )
    assert rows[0].n == 3

    # Aggregate query.
    rows = list(
        client.query(
            "SELECT customer, SUM(amount) AS total "
            "FROM analytics.orders GROUP BY customer ORDER BY total DESC",
        ).result()
    )
    assert rows[0].customer == "Bob"
    assert abs(float(rows[0].total) - 250.50) < 0.01

    # WHERE filter.
    rows = list(
        client.query(
            "SELECT order_id FROM analytics.orders WHERE customer = 'Alice' ORDER BY order_id",
        ).result()
    )
    assert [r.order_id for r in rows] == [1, 3]

    # Parameterized query.
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("min_amt", "NUMERIC", "100")],
    )
    rows = list(
        client.query(
            "SELECT customer FROM analytics.orders WHERE amount >= @min_amt ORDER BY customer",
            job_config=cfg,
        ).result()
    )
    assert [r.customer for r in rows] == ["Alice", "Bob"]

    # Table metadata update.
    orders_meta = client.get_table(orders)
    orders_meta.description = "Customer orders"
    client.update_table(orders_meta, ["description"])
    refreshed = client.get_table(orders)
    assert refreshed.description == "Customer orders"

    # Load job ────────────────────────────────────────────
    # Write more data as CSV and load it.
    csv_path = tmp_path / "extra_orders.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer", "amount", "order_date"])
        w.writerow([4, "Carol", "500.00", "2026-04-16"])
        w.writerow([5, "Dave", "30.00", "2026-04-17"])

    load_r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "load": {
                    "destinationTable": {
                        "projectId": "test-project",
                        "datasetId": "analytics",
                        "tableId": "orders",
                    },
                    "sourceUris": [str(csv_path)],
                    "sourceFormat": "CSV",
                    "writeDisposition": "WRITE_APPEND",
                },
            },
        },
        timeout=30,
    )
    assert load_r.status_code == 200

    # Verify load added rows.
    rows = list(client.query("SELECT COUNT(*) AS n FROM analytics.orders").result())
    assert rows[0].n == 5

    # Extract to CSV.
    extract_path = tmp_path / "exported.csv"
    extract_r = httpx.post(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        json={
            "configuration": {
                "extract": {
                    "sourceTable": {
                        "projectId": "test-project",
                        "datasetId": "analytics",
                        "tableId": "orders",
                    },
                    "destinationUris": [str(extract_path)],
                    "destinationFormat": "CSV",
                },
            },
        },
        timeout=30,
    )
    assert extract_r.status_code == 200
    assert extract_path.exists()
    with extract_path.open() as f:
        reader = csv.DictReader(f)
        exported_rows = list(reader)
    assert len(exported_rows) == 5

    # Jobs list — should have multiple jobs now.
    jobs_r = httpx.get(
        f"{bqemu_server.rest_url}/bigquery/v2/projects/test-project/jobs",
        timeout=10,
    )
    assert jobs_r.status_code == 200
    assert jobs_r.json()["totalItems"] >= 3

    # Wildcard tables ─────────────────────────────────────
    # Create daily event tables.
    event_schema = [bigquery.SchemaField("event_id", "INT64")]
    for day in ["20260415", "20260416"]:
        t = client.create_table(
            bigquery.Table(f"test-project.analytics.events_{day}", schema=event_schema),
        )
        client.insert_rows_json(t, [{"event_id": int(day[-2:])}])

    # Wildcard query.
    rows = list(
        client.query(
            "SELECT event_id, _TABLE_SUFFIX FROM analytics.events_* ORDER BY event_id",
        ).result()
    )
    assert len(rows) == 2
    assert {r._TABLE_SUFFIX for r in rows} == {"20260415", "20260416"}

    # Wildcard with suffix filter.
    rows = list(
        client.query(
            "SELECT event_id FROM analytics.events_* WHERE _TABLE_SUFFIX = '20260415'",
        ).result()
    )
    assert len(rows) == 1
    assert rows[0].event_id == 15

    # Partitioned table ───────────────────────────────────
    part_table = bigquery.Table("test-project.analytics.daily_metrics")
    part_table.schema = [
        bigquery.SchemaField("metric_date", "DATE"),
        bigquery.SchemaField("value", "INT64"),
    ]
    part_table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="metric_date",
    )
    part_table.clustering_fields = ["value"]
    created_part = client.create_table(part_table)

    # Verify metadata round-trip.
    fetched = client.get_table(created_part)
    assert fetched.time_partitioning is not None
    assert fetched.time_partitioning.field == "metric_date"
    assert fetched.clustering_fields is not None

    # Insert and query.
    client.insert_rows_json(
        created_part,
        [
            {"metric_date": "2026-04-15", "value": 100},
            {"metric_date": "2026-04-16", "value": 200},
        ],
    )
    rows = list(
        client.query(
            "SELECT value FROM analytics.daily_metrics WHERE metric_date = DATE '2026-04-15'",
        ).result()
    )
    assert rows[0].value == 100

    # Storage Read API via gRPC ─────────────────────────
    from google.cloud.bigquery_storage_v1 import types as storage_types
    import grpc

    channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)

    # CreateReadSession on the orders table.
    create_req = storage_types.CreateReadSessionRequest(
        parent="projects/test-project",
        read_session=storage_types.ReadSession(
            table="projects/test-project/datasets/analytics/tables/orders",
            data_format=storage_types.DataFormat.ARROW,
        ),
        max_stream_count=2,
    )
    session_resp = channel.unary_unary(
        "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
    )(storage_types.CreateReadSessionRequest.serialize(create_req))
    session = storage_types.ReadSession.deserialize(session_resp)
    assert session.name.startswith("projects/test-project/")
    assert len(session.streams) >= 1

    # ReadRows — read data back via Arrow IPC.
    import pyarrow as pa

    schema = pa.ipc.open_stream(session.arrow_schema.serialized_schema).schema
    total_storage_rows = 0
    for stream in session.streams:
        read_req = storage_types.ReadRowsRequest(read_stream=stream.name)
        responses = list(
            channel.unary_stream(
                "/google.cloud.bigquery.storage.v1.BigQueryRead/ReadRows",
            )(storage_types.ReadRowsRequest.serialize(read_req))
        )
        for r in responses:
            rr = storage_types.ReadRowsResponse.deserialize(r)
            if rr.arrow_record_batch.serialized_record_batch:
                batch = pa.ipc.read_record_batch(
                    rr.arrow_record_batch.serialized_record_batch,
                    schema,
                )
                total_storage_rows += batch.num_rows
    # Should match the 5 rows we loaded (3 insertAll + 2 CSV load).
    assert total_storage_rows == 5

    # Storage Read with column projection.
    proj_req = storage_types.CreateReadSessionRequest(
        parent="projects/test-project",
        read_session=storage_types.ReadSession(
            table="projects/test-project/datasets/analytics/tables/orders",
            data_format=storage_types.DataFormat.ARROW,
            read_options=storage_types.ReadSession.TableReadOptions(
                selected_fields=["customer"],
            ),
        ),
        max_stream_count=1,
    )
    proj_resp = channel.unary_unary(
        "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
    )(storage_types.CreateReadSessionRequest.serialize(proj_req))
    proj_session = storage_types.ReadSession.deserialize(proj_resp)
    proj_read = storage_types.ReadRowsRequest(
        read_stream=proj_session.streams[0].name,
    )
    proj_responses = list(
        channel.unary_stream(
            "/google.cloud.bigquery.storage.v1.BigQueryRead/ReadRows",
        )(storage_types.ReadRowsRequest.serialize(proj_read))
    )
    proj_schema = pa.ipc.open_stream(proj_session.arrow_schema.serialized_schema).schema
    for r in proj_responses:
        rr = storage_types.ReadRowsResponse.deserialize(r)
        if rr.arrow_record_batch.serialized_record_batch:
            batch = pa.ipc.read_record_batch(
                rr.arrow_record_batch.serialized_record_batch,
                proj_schema,
            )
            assert batch.schema.names == ["customer"]

    channel.close()

    # ── Error handling ───────────────────────────────────────────────
    # Invalid SQL.
    from google.api_core.exceptions import BadRequest

    with pytest.raises(BadRequest):
        client.query("SELECT FROM WHERE INVALID").result()

    # Query nonexistent table.
    with pytest.raises(Exception):
        client.query("SELECT * FROM analytics.ghost_table").result()

    # ── Cleanup ──────────────────────────────────────────────────────
    client.delete_dataset("analytics", delete_contents=True)

    # Verify cleanup.
    datasets = list(client.list_datasets())
    assert all(d.dataset_id != "analytics" for d in datasets)
