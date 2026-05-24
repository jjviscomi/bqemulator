"""Integration tests: Storage Read API error paths and edge cases.

Phase 4 audit — covers every uncovered branch in read_servicer.py.
"""

from __future__ import annotations

import grpc
import pyarrow as pa
import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def _make_bq_client(bqemu_server: EmulatorServer):
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )


def _setup_data(bqemu_server: EmulatorServer) -> None:
    from google.cloud import bigquery

    client = _make_bq_client(bqemu_server)
    try:
        client.get_dataset("sr_edge")
    except Exception:  # noqa: BLE001
        client.create_dataset("sr_edge")
        schema = [
            bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("name", "STRING"),
            bigquery.SchemaField("score", "INT64"),
        ]
        table = client.create_table(
            bigquery.Table("test-project.sr_edge.data", schema=schema),
        )
        client.insert_rows_json(
            table,
            [
                {"id": 1, "name": "Alice", "score": 90},
                {"id": 2, "name": "Bob", "score": 70},
                {"id": 3, "name": "Carol", "score": 85},
            ],
        )


def _setup_tenant_data(bqemu_server: EmulatorServer) -> None:
    """Per-tenant rows for the ``SESSION_USER`` row-restriction test.

    Each row's ``owner`` column carries the email of the tenant; the
    SESSION_USER row_restriction filter (``owner = SESSION_USER()``)
    matches the calling user's row and excludes the others.
    """
    from google.cloud import bigquery

    client = _make_bq_client(bqemu_server)
    try:
        client.get_dataset("sr_edge")
    except Exception:  # noqa: BLE001
        client.create_dataset("sr_edge")
    try:
        client.get_table("test-project.sr_edge.tenants")
    except Exception:  # noqa: BLE001
        schema = [
            bigquery.SchemaField("owner", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("payload", "STRING"),
        ]
        table = client.create_table(
            bigquery.Table("test-project.sr_edge.tenants", schema=schema),
        )
        client.insert_rows_json(
            table,
            [
                {"owner": "alice@example.com", "payload": "alice-data"},
                {"owner": "bob@example.com", "payload": "bob-data"},
            ],
        )


class TestCreateReadSessionErrors:
    def test_invalid_table_path_returns_error(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Short/malformed table path should return INVALID_ARGUMENT."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)

        request = types.CreateReadSessionRequest(
            parent="projects/test-project",
            read_session=types.ReadSession(
                table="invalid/path",  # Not projects/p/datasets/d/tables/t
                data_format=types.DataFormat.ARROW,
            ),
        )
        with pytest.raises(grpc.RpcError) as exc_info:
            channel.unary_unary(
                "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
            )(types.CreateReadSessionRequest.serialize(request))

        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        channel.close()

    def test_nonexistent_table_returns_not_found(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Reading from a table that doesn't exist returns NOT_FOUND."""
        _setup_data(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)

        request = types.CreateReadSessionRequest(
            parent="projects/test-project",
            read_session=types.ReadSession(
                table="projects/test-project/datasets/sr_edge/tables/ghost",
                data_format=types.DataFormat.ARROW,
            ),
        )
        with pytest.raises(grpc.RpcError) as exc_info:
            channel.unary_unary(
                "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
            )(types.CreateReadSessionRequest.serialize(request))

        assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
        channel.close()


class TestReadRowsErrors:
    def test_unknown_stream_returns_not_found(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """ReadRows with a nonexistent stream name returns NOT_FOUND."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)

        read_req = types.ReadRowsRequest(
            read_stream="projects/test/locations/US/sessions/ghost/streams/0",
        )
        with pytest.raises(grpc.RpcError) as exc_info:
            list(
                channel.unary_stream(
                    "/google.cloud.bigquery.storage.v1.BigQueryRead/ReadRows",
                )(types.ReadRowsRequest.serialize(read_req))
            )

        assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
        channel.close()


class TestReadWithProjection:
    def test_selected_fields_returns_only_requested_columns(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """CreateReadSession with selected_fields returns projected data."""
        _setup_data(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)

        request = types.CreateReadSessionRequest(
            parent="projects/test-project",
            read_session=types.ReadSession(
                table="projects/test-project/datasets/sr_edge/tables/data",
                data_format=types.DataFormat.ARROW,
                read_options=types.ReadSession.TableReadOptions(
                    selected_fields=["name"],
                ),
            ),
            max_stream_count=1,
        )

        resp = channel.unary_unary(
            "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
        )(types.CreateReadSessionRequest.serialize(request))
        session = types.ReadSession.deserialize(resp)

        # Read and verify only 'name' column.
        read_req = types.ReadRowsRequest(read_stream=session.streams[0].name)
        responses = list(
            channel.unary_stream(
                "/google.cloud.bigquery.storage.v1.BigQueryRead/ReadRows",
            )(types.ReadRowsRequest.serialize(read_req))
        )

        schema = pa.ipc.open_stream(session.arrow_schema.serialized_schema).schema
        total_rows = 0
        for r in responses:
            rr = types.ReadRowsResponse.deserialize(r)
            if rr.arrow_record_batch.serialized_record_batch:
                batch = pa.ipc.read_record_batch(
                    rr.arrow_record_batch.serialized_record_batch,
                    schema,
                )
                assert batch.schema.names == ["name"]
                total_rows += batch.num_rows
        assert total_rows == 3

        channel.close()


class TestReadWithRowFilter:
    def test_row_restriction_filters_data(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """CreateReadSession with row_restriction returns filtered rows."""
        _setup_data(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)

        request = types.CreateReadSessionRequest(
            parent="projects/test-project",
            read_session=types.ReadSession(
                table="projects/test-project/datasets/sr_edge/tables/data",
                data_format=types.DataFormat.ARROW,
                read_options=types.ReadSession.TableReadOptions(
                    row_restriction="score >= 85",
                ),
            ),
            max_stream_count=1,
        )

        resp = channel.unary_unary(
            "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
        )(types.CreateReadSessionRequest.serialize(request))
        session = types.ReadSession.deserialize(resp)

        # Read and verify filtered rows.
        read_req = types.ReadRowsRequest(read_stream=session.streams[0].name)
        responses = list(
            channel.unary_stream(
                "/google.cloud.bigquery.storage.v1.BigQueryRead/ReadRows",
            )(types.ReadRowsRequest.serialize(read_req))
        )

        schema = pa.ipc.open_stream(session.arrow_schema.serialized_schema).schema
        total_rows = 0
        for r in responses:
            rr = types.ReadRowsResponse.deserialize(r)
            if rr.arrow_record_batch.serialized_record_batch:
                batch = pa.ipc.read_record_batch(
                    rr.arrow_record_batch.serialized_record_batch,
                    schema,
                )
                total_rows += batch.num_rows

        # Alice (90) and Carol (85) pass, Bob (70) filtered out.
        assert total_rows == 2

        channel.close()

    def test_row_restriction_with_session_user_threads_caller(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Storage Read row_restriction sees the authenticated caller (ADR 0040).

        Pre-ADR-0040 the row_restriction filter pre-pass folded
        every ``SESSION_USER()`` call to the ``"anonymous"``
        sentinel because no caller context flowed into
        ``_build_read_sql``. Hoisting the caller-resolution
        above ``_build_read_sql`` (and threading ``caller`` into
        the translator) closes that gap.

        This test seeds a per-tenant table, sets the
        ``X-Bqemu-Caller`` gRPC metadata header to a specific
        user, uses ``SESSION_USER()`` in the ``row_restriction``,
        and verifies only that user's rows come back. If the
        caller threading regresses, every row gets filtered
        (anonymous != alice@example.com).
        """
        _setup_tenant_data(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)

        # Alice's caller identity flows through ``X-Bqemu-Caller``;
        # the row_restriction's ``SESSION_USER()`` should rewrite
        # to ``'alice@example.com'`` and match only her row.
        metadata = (("x-bqemu-caller", "user:alice@example.com"),)
        request = types.CreateReadSessionRequest(
            parent="projects/test-project",
            read_session=types.ReadSession(
                table="projects/test-project/datasets/sr_edge/tables/tenants",
                data_format=types.DataFormat.ARROW,
                read_options=types.ReadSession.TableReadOptions(
                    row_restriction="owner = SESSION_USER()",
                ),
            ),
            max_stream_count=1,
        )

        resp = channel.unary_unary(
            "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
        )(types.CreateReadSessionRequest.serialize(request), metadata=metadata)
        session = types.ReadSession.deserialize(resp)

        read_req = types.ReadRowsRequest(read_stream=session.streams[0].name)
        responses = list(
            channel.unary_stream(
                "/google.cloud.bigquery.storage.v1.BigQueryRead/ReadRows",
            )(types.ReadRowsRequest.serialize(read_req), metadata=metadata)
        )

        schema = pa.ipc.open_stream(session.arrow_schema.serialized_schema).schema
        owners: list[str] = []
        for r in responses:
            rr = types.ReadRowsResponse.deserialize(r)
            if rr.arrow_record_batch.serialized_record_batch:
                batch = pa.ipc.read_record_batch(
                    rr.arrow_record_batch.serialized_record_batch,
                    schema,
                )
                owners.extend(batch["owner"].to_pylist())

        # Exactly Alice's row — Bob's row is filtered out by the
        # SESSION_USER predicate. Pre-ADR-0040 the filter would
        # produce ``owner = 'anonymous'`` and match nothing.
        assert owners == ["alice@example.com"]

        channel.close()


class TestMultiStreamRead:
    def test_all_streams_cover_all_rows(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Reading all streams returns the complete dataset."""
        _setup_data(bqemu_server)
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)

        request = types.CreateReadSessionRequest(
            parent="projects/test-project",
            read_session=types.ReadSession(
                table="projects/test-project/datasets/sr_edge/tables/data",
                data_format=types.DataFormat.ARROW,
            ),
            max_stream_count=3,
        )

        resp = channel.unary_unary(
            "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
        )(types.CreateReadSessionRequest.serialize(request))
        session = types.ReadSession.deserialize(resp)

        # Read ALL streams and count total rows.
        schema = pa.ipc.open_stream(session.arrow_schema.serialized_schema).schema
        total_rows = 0
        for stream in session.streams:
            read_req = types.ReadRowsRequest(read_stream=stream.name)
            responses = list(
                channel.unary_stream(
                    "/google.cloud.bigquery.storage.v1.BigQueryRead/ReadRows",
                )(types.ReadRowsRequest.serialize(read_req))
            )
            for r in responses:
                rr = types.ReadRowsResponse.deserialize(r)
                if rr.arrow_record_batch.serialized_record_batch:
                    batch = pa.ipc.read_record_batch(
                        rr.arrow_record_batch.serialized_record_batch,
                        schema,
                    )
                    total_rows += batch.num_rows

        assert total_rows == 3

        channel.close()
        _make_bq_client(bqemu_server).delete_dataset("sr_edge", delete_contents=True)
