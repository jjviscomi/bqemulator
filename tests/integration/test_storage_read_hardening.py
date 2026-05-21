"""Integration tests: Storage Read API hardening.

Pairs with Phase 4 + audit: injection via table path, column names, or
row-restriction strings must never reach DuckDB as raw SQL.
"""

from __future__ import annotations

import grpc
import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration

_READ = "/google.cloud.bigquery.storage.v1.BigQueryRead"


class TestInjectionDefense:
    def test_malformed_table_path_returns_invalid_argument(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """An injection attempt in the table path is rejected."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            req = types.CreateReadSessionRequest(
                parent="projects/x",
                read_session=types.ReadSession(
                    table='projects/p"; DROP TABLE x; --/datasets/d/tables/t',
                    data_format=types.DataFormat.ARROW,
                ),
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_READ}/CreateReadSession")(
                    types.CreateReadSessionRequest.serialize(req),
                )
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            channel.close()

    def test_malformed_selected_field_returns_invalid_argument(
        self,
        bqemu_server: EmulatorServer,
    ) -> None:
        """Column-name injection via selected_fields is rejected."""
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            req = types.CreateReadSessionRequest(
                parent="projects/x",
                read_session=types.ReadSession(
                    table="projects/proj/datasets/ds/tables/t",
                    data_format=types.DataFormat.ARROW,
                    read_options=types.ReadSession.TableReadOptions(
                        selected_fields=['id"; DROP TABLE x; --'],
                    ),
                ),
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_READ}/CreateReadSession")(
                    types.CreateReadSessionRequest.serialize(req),
                )
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            channel.close()

    @pytest.mark.parametrize(
        "bad_filter",
        [
            "1=1; DROP TABLE secrets",
            "x > 0 UNION SELECT * FROM passwords",
            "x < 10 OR (SELECT COUNT(*) FROM admins) > 0",
            "x = 1 -- comment",
            "x = /* hack */ 1",
        ],
    )
    def test_dangerous_row_restriction_rejected(
        self,
        bqemu_server: EmulatorServer,
        bad_filter: str,
    ) -> None:
        """Row filters containing statements/subqueries are rejected.

        Real BigQuery's row_restriction is a simple-predicate language;
        we block anything that could open a new statement, subquery, or
        SQL comment.
        """
        from google.cloud.bigquery_storage_v1 import types

        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            req = types.CreateReadSessionRequest(
                parent="projects/x",
                read_session=types.ReadSession(
                    table="projects/proj/datasets/ds/tables/t",
                    data_format=types.DataFormat.ARROW,
                    read_options=types.ReadSession.TableReadOptions(
                        row_restriction=bad_filter,
                    ),
                ),
            )
            with pytest.raises(grpc.RpcError) as exc:
                channel.unary_unary(f"{_READ}/CreateReadSession")(
                    types.CreateReadSessionRequest.serialize(req),
                )
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            channel.close()
