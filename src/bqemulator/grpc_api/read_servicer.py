"""BigQueryRead gRPC service implementation.

Implements the Storage Read API v1 using a generic gRPC handler (since
we don't vendor the googleapis proto stubs). The client sends serialized
proto messages; we deserialize using the ``google-cloud-bigquery-storage``
package's proto-plus types and return serialized responses.

Methods:
    CreateReadSession — materialize table data, split into streams.
    ReadRows — server-streaming; send Arrow IPC batches per stream.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import grpc
import pyarrow as pa

from bqemulator.domain.errors import ValidationError
from bqemulator.observability.logging_ import get_logger
from bqemulator.row_access.identity import (
    CallerIdentity,
    resolve_caller_from_metadata,
)
from bqemulator.sql.rewriter.row_access_filter import rewrite_for_row_access
from bqemulator.storage.sql_identifiers import quoted_table_ref
from bqemulator.streaming.read_session import (
    FORMAT_ARROW,
    FORMAT_AVRO,
    create_read_session,
    get_stream_data,
    serialize_arrow_record_batch,
)

_COLUMN_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,299}$")


def _validate_column_names(names: list[str]) -> None:
    """Ensure every column name fits BigQuery's identifier rules."""
    for name in names:
        if not _COLUMN_NAME_RE.match(name):
            raise ValidationError(f"Invalid column name: {name!r}")


def _parse_table_path(table_path: str) -> tuple[str, str, str] | None:
    """Parse ``projects/{p}/datasets/{d}/tables/{t}`` into a triple."""
    parts = table_path.split("/")
    expected_parts = 6
    if len(parts) < expected_parts:
        return None
    return parts[1], parts[3], parts[5]


def _selected_fields(read_session: Any) -> list[str] | None:
    """Extract + validate ``selected_fields`` from a ReadSession."""
    if not (read_session.read_options and read_session.read_options.selected_fields):
        return None
    raw_fields = list(read_session.read_options.selected_fields)
    _validate_column_names(raw_fields)
    return raw_fields


_FORBIDDEN_FILTER_KEYWORDS = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "CREATE",
        "ALTER",
        "ATTACH",
        "COPY",
        "EXEC",
        "EXECUTE",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "UNION",
        "INTERSECT",
        "EXCEPT",
        "CALL",
        ";",
        "--",
        "/*",
        "*/",
    },
)


def _reject_dangerous_filter(row_filter: str) -> None:
    """Block row_restriction strings that smuggle in statements or subqueries.

    Real BigQuery limits Storage Read API filters to simple column
    predicates. We approximate that by rejecting any token that could
    open a subquery or a second statement — subqueries might leak data
    from tables the caller couldn't otherwise read.
    """
    tokens = re.findall(r"[A-Za-z_][A-Za-z_0-9]*|\-\-|/\*|\*/|;", row_filter)
    for token in tokens:
        if token.upper() in _FORBIDDEN_FILTER_KEYWORDS:
            raise ValidationError(
                f"row_restriction keyword not allowed in filter: {token!r}",
            )
    if ";" in row_filter or "--" in row_filter or "/*" in row_filter:
        raise ValidationError("row_restriction contains statement-terminator bytes")


def _build_read_sql(
    target_ref: str,
    selected_fields: list[str] | None,
    read_session: Any,
    caller: CallerIdentity | None = None,
) -> str:
    """Assemble the SELECT ... FROM ... [WHERE ...] SQL string.

    ``caller`` is threaded into the row_restriction's translation
    pass so caller-identity functions (``SESSION_USER()``,
    ``CURRENT_USER()``, ``@@session.user``) fold to the
    authenticated principal's email instead of the ``ANONYMOUS_CALLER``
    sentinel. Before the closure documented in ADR 0040, the row
    restriction's filter pre-pass received no caller context and
    every caller-identity call folded to ``"anonymous"`` regardless
    of the actual ``X-Bqemu-Caller`` header. The default ``None``
    preserves the previous behaviour for the (deprecated) call
    sites that haven't been migrated.
    """
    cols = ", ".join(f'"{c}"' for c in selected_fields) if selected_fields else "*"
    sql = f"SELECT {cols} FROM {target_ref}"
    row_filter = read_session.read_options.row_restriction if read_session.read_options else ""
    if row_filter:
        _reject_dangerous_filter(row_filter)
        from bqemulator.sql.translator import SQLTranslator

        translator = SQLTranslator()
        filter_result = translator.translate(f"SELECT 1 WHERE {row_filter}", caller=caller)
        if hasattr(filter_result, "value"):
            translated = filter_result.value
            if "WHERE" in translated.upper():
                where_part = translated[translated.upper().index("WHERE") :]
                sql += f" {where_part}"
    return sql


def _resolve_read_target(
    read_session: Any,
    context: grpc.ServicerContext,
) -> tuple[str, str, str, str, list[str] | None] | None:
    """Parse + validate the read session's table reference.

    Returns ``(project_id, dataset_id, table_id, target_ref,
    selected_fields)`` or ``None`` on validation failure (the caller
    short-circuits with an empty response; ``context`` status is
    already set with INVALID_ARGUMENT).
    """
    parsed = _parse_table_path(read_session.table)
    if parsed is None:
        context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
        context.set_details(f"Invalid table path: {read_session.table}")
        return None
    project_id, dataset_id, table_id = parsed

    try:
        target_ref = quoted_table_ref(project_id, dataset_id, table_id)
        selected_fields = _selected_fields(read_session)
    except ValidationError as exc:
        context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
        context.set_details(str(exc))
        return None
    return project_id, dataset_id, table_id, target_ref, selected_fields


def _resolve_data_format(
    read_session: Any,
    context: grpc.ServicerContext,
) -> tuple[str, Any] | None:
    """Map the raw ``data_format`` proto enum to (session_format, wire_format).

    The Java BQ Storage Read client defaults to AVRO; Python / Go /
    Node default to ARROW. The proto3 default for an unset
    ``DataFormat`` is 0 (``DATA_FORMAT_UNSPECIFIED``) — match real
    BQ's behaviour and treat that as ARROW. Any other value (a
    hypothetical future PROTO format) gets INVALID_ARGUMENT.

    Read the raw underlying-pb int rather than the proto-plus
    property so an unknown enum value doesn't trip proto-plus's
    warnings-as-errors path under the test runner.

    Returns ``None`` on an unsupported format with ``context`` already
    set to INVALID_ARGUMENT.
    """
    from google.cloud.bigquery_storage_v1 import types

    raw_format = read_session._pb.data_format  # noqa: SLF001
    if raw_format in (
        int(types.DataFormat.DATA_FORMAT_UNSPECIFIED),
        int(types.DataFormat.ARROW),
    ):
        return FORMAT_ARROW, types.DataFormat.ARROW
    if raw_format == int(types.DataFormat.AVRO):
        return FORMAT_AVRO, types.DataFormat.AVRO
    context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
    context.set_details(
        f"Unsupported data_format: {raw_format!r}; supported: ARROW, AVRO",
    )
    return None


def _build_read_session_response(
    *,
    state: Any,
    read_session: Any,
    session_format: str,
    wire_format: Any,
    arrow_table: pa.Table,
) -> Any:
    """Build the ReadSession response proto.

    Real BigQuery echoes ``read_options`` back on the session so
    clients can confirm the projection / filter / compression they
    asked for; the wire-format conformance suite asserts that shape.
    The schema field carried on the session depends on the chosen
    wire format — Arrow sessions carry ``arrow_schema``, Avro
    sessions carry ``avro_schema`` (proto oneof).
    """
    from google.cloud.bigquery_storage_v1 import types

    session_kwargs: dict[str, Any] = {
        "name": state.session_name,
        "table": read_session.table,
        "data_format": wire_format,
        "streams": [types.ReadStream(name=s.name) for s in state.streams],
        "estimated_total_bytes_scanned": arrow_table.nbytes,
        "read_options": read_session.read_options or None,
    }
    if session_format == FORMAT_AVRO:
        session_kwargs["avro_schema"] = types.AvroSchema(
            schema=state.avro_schema_json,
        )
    else:
        session_kwargs["arrow_schema"] = types.ArrowSchema(
            serialized_schema=state.arrow_schema_bytes,
        )
    return types.ReadSession(**session_kwargs)


if TYPE_CHECKING:
    from bqemulator.api.dependencies import AppContext

_log = get_logger(__name__)

# gRPC method paths.
_SERVICE = "/google.cloud.bigquery.storage.v1.BigQueryRead"
_CREATE_READ_SESSION = f"{_SERVICE}/CreateReadSession"
_READ_ROWS = f"{_SERVICE}/ReadRows"
_SPLIT_READ_STREAM = f"{_SERVICE}/SplitReadStream"


class BigQueryReadHandler(grpc.GenericRpcHandler):
    """Generic gRPC handler for the BigQuery Storage Read API.

    Registered with the gRPC server to handle CreateReadSession and
    ReadRows RPCs without requiring vendored proto stubs.
    """

    def __init__(self, context: AppContext) -> None:
        self._ctx = context

    @staticmethod
    def _build_bq_read_sql(
        project_id: str,
        dataset_id: str,
        table_id: str,
        selected_fields: list[str] | None,
        read_session: Any,
    ) -> str:
        """Build a BigQuery-shaped SELECT for the rewriter to consume.

        Mirrors :func:`_build_read_sql` but uses dotted BigQuery
        identifiers (``project.dataset.table``) so the row-access
        rewriter's SQLGlot pass can recognise the table reference.
        """
        cols = ", ".join(f"`{c}`" for c in selected_fields) if selected_fields else "*"
        bq_table_ref = f"`{project_id}`.`{dataset_id}`.`{table_id}`"
        sql = f"SELECT {cols} FROM {bq_table_ref}"
        row_filter = read_session.read_options.row_restriction if read_session.read_options else ""
        if row_filter:
            sql += f" WHERE {row_filter}"
        return sql

    def service(
        self, handler_call_details: grpc.HandlerCallDetails
    ) -> grpc.RpcMethodHandler | None:
        """Route incoming RPCs to the correct handler."""
        method = handler_call_details.method
        if method == _CREATE_READ_SESSION:
            return grpc.unary_unary_rpc_method_handler(
                self._handle_create_read_session,
            )
        if method == _READ_ROWS:
            return grpc.unary_stream_rpc_method_handler(
                self._handle_read_rows,
            )
        if method == _SPLIT_READ_STREAM:
            return grpc.unary_unary_rpc_method_handler(
                self._handle_split_read_stream,
            )
        return None

    def _handle_create_read_session(
        self,
        request_bytes: bytes,
        context: grpc.ServicerContext,
    ) -> bytes:
        """Handle CreateReadSession RPC."""
        from google.cloud.bigquery_storage_v1 import types

        request = types.CreateReadSessionRequest.deserialize(request_bytes)
        read_session = request.read_session

        target = _resolve_read_target(read_session, context)
        if target is None:
            return b""
        project_id, dataset_id, table_id, target_ref, selected_fields = target

        # Resolve the caller from gRPC metadata up-front so both the
        # row_restriction filter pre-pass (inside ``_build_read_sql``)
        # and the row-access policy rewrite below see the same
        # ``CallerIdentity``. Caller-identity functions
        # (``SESSION_USER()``, ``CURRENT_USER()``, ``@@session.user``)
        # inside a ``row_restriction`` must fold to the value provided
        # by the ``X-Bqemu-Caller`` header rather than the
        # ``ANONYMOUS_CALLER`` sentinel; resolving here guarantees that.
        caller = resolve_caller_from_metadata(
            list(context.invocation_metadata())
            if hasattr(context, "invocation_metadata")
            else None,
        )

        sql = _build_read_sql(target_ref, selected_fields, read_session, caller=caller)
        sql_after_rap = self._apply_row_access_rewrite(
            sql=sql,
            project_id=project_id,
            dataset_id=dataset_id,
            table_id=table_id,
            selected_fields=selected_fields,
            read_session=read_session,
            caller=caller,
            context=context,
        )
        if sql_after_rap is None:
            return b""

        try:
            arrow_table = self._ctx.engine.fetch_arrow(sql_after_rap)
        except Exception as exc:  # noqa: BLE001 — DuckDB can throw various exceptions
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Table read failed: {exc}")
            return b""

        formats = _resolve_data_format(read_session, context)
        if formats is None:
            return b""
        session_format, wire_format = formats

        # Create the read session.
        state = create_read_session(
            project_id=project_id,
            table_ref=read_session.table,
            arrow_table=arrow_table,
            max_streams=request.max_stream_count or 1,
            selected_fields=selected_fields,
            data_format=session_format,
            required_field_names=self._required_field_names(
                project_id,
                dataset_id,
                table_id,
            ),
        )

        response = _build_read_session_response(
            state=state,
            read_session=read_session,
            session_format=session_format,
            wire_format=wire_format,
            arrow_table=arrow_table,
        )
        self._ctx.metrics.read_streams_active.inc(len(state.streams))
        return types.ReadSession.serialize(response)

    def _apply_row_access_rewrite(
        self,
        *,
        sql: str,
        project_id: str,
        dataset_id: str,
        table_id: str,
        selected_fields: list[str] | None,
        read_session: Any,
        caller: CallerIdentity,
        context: grpc.ServicerContext,
    ) -> str | None:
        """Apply row-access-policy rewrite if any policies are registered.

        Returns the (possibly unchanged) SQL or ``None`` on rewrite
        failure — the caller short-circuits with an empty response in
        that case (context status is already set).

        Tables with no registered policies short-circuit at the
        rewriter so the cheap-path keeps the original DuckDB SQL.
        """
        if not self._ctx.catalog.list_all_row_access_policies():
            return sql

        bq_sql = self._build_bq_read_sql(
            project_id,
            dataset_id,
            table_id,
            selected_fields,
            read_session,
        )
        rewritten = rewrite_for_row_access(
            bq_sql,
            project_id=project_id,
            caller=caller,
            catalog=self._ctx.catalog,
        )
        if rewritten == bq_sql:
            return sql

        from bqemulator.sql.table_rewriter import rewrite_table_refs
        from bqemulator.sql.translator import SQLTranslator

        translate_result = SQLTranslator().translate(rewritten, caller=caller)
        if not hasattr(translate_result, "value"):  # pragma: no cover — translator Err
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("row-access rewrite failed to translate")
            return None
        return rewrite_table_refs(translate_result.value, project_id)

    def _required_field_names(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> frozenset[str] | None:
        """Return REQUIRED column names from the catalog for the Avro emitter.

        DuckDB's query-result schema marks every column nullable
        regardless of the source-table REQUIRED flag — without this
        lookup, the Storage Read Avro path would wrap every column in
        a ``["null", T]`` union and diverge from real BigQuery's
        canonical schema. Returns ``None`` when the table is not in
        the catalog (synthetic / temp / dry-run queries) so the Avro
        emitter falls through to its nullable-by-default behaviour.
        """
        catalog_table = self._ctx.catalog.get_table(project_id, dataset_id, table_id)
        if catalog_table is None:
            return None
        return frozenset(f.name for f in catalog_table.schema_.fields if f.mode == "REQUIRED")

    def _handle_read_rows(
        self,
        request_bytes: bytes,
        context: grpc.ServicerContext,
    ) -> Any:  # Generator yielding bytes — gRPC server-streaming
        """Handle ReadRows RPC (server-streaming)."""
        from google.cloud.bigquery_storage_v1 import types

        request = types.ReadRowsRequest.deserialize(request_bytes)
        stream_name = request.read_stream

        # Find the session that owns this stream.
        session_name = "/".join(stream_name.split("/")[:-2])
        stream_data = get_stream_data(session_name, stream_name)

        if stream_data is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Stream not found: {stream_name}")
            return

        # The first ReadRowsResponse carries the writer-side schema
        # so a stateless client can deserialise the payload without
        # re-fetching the session; subsequent messages omit it. Real
        # BigQuery also surfaces a ``stats.progress`` heartbeat on
        # every message — the wire-format conformance suite asserts
        # both keys are present.
        from bqemulator.streaming.avro_serializer import (
            serialize_arrow_table_to_avro_rows,
        )
        from bqemulator.streaming.read_session import FORMAT_AVRO, get_session

        session = get_session(session_name)
        if session is None:
            # Defensive — get_stream_data succeeded above so the session
            # MUST exist. Treat as NOT_FOUND.
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Session not found: {session_name}")
            return
        is_first = True
        for batch in stream_data.to_batches(max_chunksize=65536):
            batch_table = pa.Table.from_batches([batch], schema=stream_data.schema)
            # ``ArrowRecordBatch.row_count`` / ``AvroRows.row_count`` are
            # documented as ``[deprecated = true]`` and real BigQuery
            # omits them (proto3 default-skip). The outer
            # ``ReadRowsResponse.row_count`` carries the canonical row
            # count; the wire-format conformance suite asserts the inner
            # field stays at its default.
            kwargs: dict[str, Any] = {
                "row_count": batch.num_rows,
                "stats": types.StreamStats(
                    progress=types.StreamStats.Progress(
                        at_response_start=0.0,
                        at_response_end=1.0,
                    ),
                ),
            }
            if session.data_format == FORMAT_AVRO:
                avro_bytes = serialize_arrow_table_to_avro_rows(
                    batch_table,
                    session.avro_schema_json,
                )
                kwargs["avro_rows"] = types.AvroRows(
                    serialized_binary_rows=avro_bytes,
                )
                if is_first:
                    kwargs["avro_schema"] = types.AvroSchema(
                        schema=session.avro_schema_json,
                    )
                    is_first = False
            else:
                # ``serialized_record_batch`` carries ONLY the
                # batch-message bytes; the schema travels separately
                # via ``ReadSession.arrow_schema.serialized_schema``
                # and the first response's ``arrow_schema`` field.
                # See issue #15 for the format-mismatch fix.
                ipc_bytes = serialize_arrow_record_batch(batch)
                kwargs["arrow_record_batch"] = types.ArrowRecordBatch(
                    serialized_record_batch=ipc_bytes,
                )
                if is_first:
                    kwargs["arrow_schema"] = types.ArrowSchema(
                        serialized_schema=session.arrow_schema_bytes,
                    )
                    is_first = False
            response = types.ReadRowsResponse(**kwargs)
            yield types.ReadRowsResponse.serialize(response)

        self._ctx.metrics.read_streams_active.dec(1)

    def _handle_split_read_stream(
        self,
        request_bytes: bytes,
        context: grpc.ServicerContext,
    ) -> bytes:
        """Handle SplitReadStream — server-side splitting hint.

        Real BigQuery's SplitReadStream returns two new stream names
        carving the original stream's row range at the requested
        fraction. The emulator implements a stub: it mints two fresh
        stream names that route ReadRows back to the same underlying
        data (effectively a no-op split). This is sufficient for
        wire-format conformance — clients that rely on the split
        semantics for parallelism are out of scope per the
        compatibility matrix.
        """
        from google.cloud.bigquery_storage_v1 import types

        from bqemulator.streaming.read_session import split_stream

        request = types.SplitReadStreamRequest.deserialize(request_bytes)
        result = split_stream(request.name, request.fraction)
        if result is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Stream not found: {request.name}")
            return b""
        primary_name, remainder_name = result
        response = types.SplitReadStreamResponse(
            primary_stream=types.ReadStream(name=primary_name),
            remainder_stream=types.ReadStream(name=remainder_name),
        )
        return types.SplitReadStreamResponse.serialize(response)


__all__ = ["BigQueryReadHandler"]
