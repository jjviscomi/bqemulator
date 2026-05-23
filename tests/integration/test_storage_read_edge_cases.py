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
