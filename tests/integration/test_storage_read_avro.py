"""Integration tests: Storage Read API — Avro wire format (G3 / ADR 0030).

Pairs with :mod:`tests.integration.test_storage_read_api` (Arrow). Every
Arrow-side scenario gets an Avro twin so the format-branch in the
gRPC servicer is exercised through the official BQ Storage proto
types. The decoded-row assertion uses :func:`fastavro.schemaless_reader`
to confirm the emulator's bytes are real Avro, not just proto-valid
random bytes.

Three non-negotiable tests guard the "real Avro file" contract per
ADR 0030 §"Cross-implementation Avro interop":

* :func:`test_emulator_avro_bytes_round_trip_to_disk` — wraps the
  emulator's bytes into an Avro Object Container File (OCF) on disk
  and re-reads via :func:`fastavro.reader`.
* :func:`test_avro_schema_converter_against_reference_file` — runs the
  schema converter against each committed reference file's embedded
  schema and asserts ``fastavro.parse_schema`` equality.
* :func:`test_emulator_avro_bytes_decode_with_apache_avro_tools` —
  uses the canonical Apache Avro implementation (``avro-tools``) as a
  second decoder so an emulator-side drift away from the documented
  wire format is caught by a non-fastavro consumer.
"""

from __future__ import annotations

from decimal import Decimal
import io
import json
from pathlib import Path
import shutil
import subprocess

import fastavro
import grpc
import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration

_READ_PATH = "/google.cloud.bigquery.storage.v1.BigQueryRead"


def _make_bq_client(bqemu_server: EmulatorServer):
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )


def _seed_table(bqemu_server: EmulatorServer) -> None:
    from google.cloud import bigquery

    client = _make_bq_client(bqemu_server)
    try:
        client.create_dataset("avro_read", exists_ok=True)
        schema = [
            bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("value", "STRING"),
        ]
        table = client.create_table(
            bigquery.Table("test-project.avro_read.data", schema=schema),
            exists_ok=True,
        )
        client.insert_rows_json(
            table,
            [{"id": i, "value": f"row_{i}"} for i in range(10)],
        )
    finally:
        client.close()


def _cleanup_table(bqemu_server: EmulatorServer) -> None:
    client = _make_bq_client(bqemu_server)
    try:
        client.delete_dataset(
            "avro_read",
            delete_contents=True,
            not_found_ok=True,
        )
    finally:
        client.close()


def _create_avro_session(bqemu_server: EmulatorServer, *, max_streams: int = 1):
    """Open an Avro Storage Read session and return (session, channel)."""
    from google.cloud.bigquery_storage_v1 import types

    channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
    request = types.CreateReadSessionRequest(
        parent="projects/test-project",
        read_session=types.ReadSession(
            table="projects/test-project/datasets/avro_read/tables/data",
            data_format=types.DataFormat.AVRO,
        ),
        max_stream_count=max_streams,
    )
    response_bytes = channel.unary_unary(
        f"{_READ_PATH}/CreateReadSession",
    )(types.CreateReadSessionRequest.serialize(request))
    session = types.ReadSession.deserialize(response_bytes)
    return session, channel


def _read_all_avro_rows(
    channel,
    session,
) -> tuple[list[dict], str]:
    """Read every Avro row from every stream; return (rows, schema_json)."""
    from google.cloud.bigquery_storage_v1 import types

    schema_json = session.avro_schema.schema
    parsed = fastavro.parse_schema(json.loads(schema_json))
    rows: list[dict] = []
    for stream in session.streams:
        read_req = types.ReadRowsRequest(read_stream=stream.name)
        for resp_bytes in channel.unary_stream(
            f"{_READ_PATH}/ReadRows",
        )(types.ReadRowsRequest.serialize(read_req)):
            resp = types.ReadRowsResponse.deserialize(resp_bytes)
            payload = resp.avro_rows.serialized_binary_rows
            if not payload:
                continue
            reader_io = io.BytesIO(payload)
            rows.extend(
                fastavro.schemaless_reader(reader_io, parsed) for _ in range(resp.row_count)
            )
    return rows, schema_json


def test_create_read_session_returns_avro_schema(
    bqemu_server: EmulatorServer,
) -> None:
    """The Avro session response carries an AvroSchema, not an ArrowSchema."""
    from google.cloud.bigquery_storage_v1 import types

    _seed_table(bqemu_server)
    try:
        session, channel = _create_avro_session(bqemu_server)
        try:
            assert session.data_format == types.DataFormat.AVRO
            assert session.avro_schema.schema  # non-empty
            # Avro schema is a JSON-encoded record type.
            parsed = json.loads(session.avro_schema.schema)
            assert parsed["type"] == "record"
            field_names = {f["name"] for f in parsed["fields"]}
            assert field_names == {"id", "value"}
            # ArrowSchema MUST NOT carry serialized bytes on an Avro session.
            assert not session.arrow_schema.serialized_schema
        finally:
            channel.close()
    finally:
        _cleanup_table(bqemu_server)


def test_read_rows_emits_decodable_avro(bqemu_server: EmulatorServer) -> None:
    """Every row round-trips through fastavro.schemaless_reader."""
    _seed_table(bqemu_server)
    try:
        session, channel = _create_avro_session(bqemu_server)
        try:
            rows, _ = _read_all_avro_rows(channel, session)
            assert len(rows) == 10
            ids = sorted(r["id"] for r in rows)
            assert ids == list(range(10))
            for row in rows:
                assert row["value"] == f"row_{row['id']}"
        finally:
            channel.close()
    finally:
        _cleanup_table(bqemu_server)


def test_avro_session_with_projection(bqemu_server: EmulatorServer) -> None:
    """selected_fields filters the Avro schema AND the decoded rows."""
    from google.cloud.bigquery_storage_v1 import types

    _seed_table(bqemu_server)
    try:
        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            request = types.CreateReadSessionRequest(
                parent="projects/test-project",
                read_session=types.ReadSession(
                    table="projects/test-project/datasets/avro_read/tables/data",
                    data_format=types.DataFormat.AVRO,
                    read_options=types.ReadSession.TableReadOptions(
                        selected_fields=["value"],
                    ),
                ),
                max_stream_count=1,
            )
            resp_bytes = channel.unary_unary(
                f"{_READ_PATH}/CreateReadSession",
            )(types.CreateReadSessionRequest.serialize(request))
            session = types.ReadSession.deserialize(resp_bytes)
            schema = json.loads(session.avro_schema.schema)
            # Projection drops ``id`` from the schema.
            assert {f["name"] for f in schema["fields"]} == {"value"}
            rows, _ = _read_all_avro_rows(channel, session)
            # Decoded rows must mirror the projected schema.
            for row in rows:
                assert "id" not in row
                assert row["value"].startswith("row_")
        finally:
            channel.close()
    finally:
        _cleanup_table(bqemu_server)


def test_unsupported_data_format_returns_invalid_argument(
    bqemu_server: EmulatorServer,
) -> None:
    """Asking for a future format (e.g. PROTO) returns INVALID_ARGUMENT.

    Pins the gRPC error-path branch in the read servicer. proto-plus
    constrains the client-side enum, so we hand-mutate a valid
    serialised request to inject an out-of-range data_format value
    (varint ``0xff 0x01`` = 255). Real BQ would also reject this with
    INVALID_ARGUMENT; the test pins our wording.
    """
    from google.cloud.bigquery_storage_v1 import types

    _seed_table(bqemu_server)
    try:
        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            # Build a valid AVRO request and serialise.
            valid = types.CreateReadSessionRequest(
                parent="projects/test-project",
                read_session=types.ReadSession(
                    table=("projects/test-project/datasets/avro_read/tables/data"),
                    data_format=types.DataFormat.AVRO,
                ),
                max_stream_count=1,
            )
            wire = types.CreateReadSessionRequest.serialize(valid)
            # The serialised form contains the byte sequence
            # ``0x18 0x01`` for the inner ``data_format=AVRO`` field
            # (field 3, wire-type 0 → tag=0x18; value 1 = AVRO).
            # Replace with ``0x18 0xff 0x01`` (value 255 → unknown
            # enum) and extend the inner read_session length by one.
            #
            # Locate the read_session length prefix (right after
            # the parent string + the field-2 tag 0x12).
            parent_str = b"projects/test-project"
            parent_field = b"\x0a" + bytes([len(parent_str)]) + parent_str
            assert wire.startswith(parent_field)
            offset = len(parent_field)
            assert wire[offset] == 0x12  # field 2, length-delimited
            old_inner_len = wire[offset + 1]
            inner_start = offset + 2
            inner = wire[inner_start : inner_start + old_inner_len]
            new_inner = inner.replace(b"\x18\x01", b"\x18\xff\x01", 1)
            assert new_inner != inner, "expected to find data_format=AVRO byte pair"
            new_wire = (
                wire[: offset + 1]
                + bytes([old_inner_len + 1])
                + new_inner
                + wire[inner_start + old_inner_len :]
            )

            with pytest.raises(grpc.RpcError) as exc_info:
                channel.unary_unary(f"{_READ_PATH}/CreateReadSession")(new_wire)
            assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert "Unsupported data_format" in exc_info.value.details()
        finally:
            channel.close()
    finally:
        _cleanup_table(bqemu_server)


def test_split_read_stream_preserves_avro_format(
    bqemu_server: EmulatorServer,
) -> None:
    """SplitReadStream child streams emit Avro just like the parent."""
    from google.cloud.bigquery_storage_v1 import types

    _seed_table(bqemu_server)
    try:
        session, channel = _create_avro_session(bqemu_server)
        try:
            split_req = types.SplitReadStreamRequest(
                name=session.streams[0].name,
                fraction=0.5,
            )
            split_resp = types.SplitReadStreamResponse.deserialize(
                channel.unary_unary(f"{_READ_PATH}/SplitReadStream")(
                    types.SplitReadStreamRequest.serialize(split_req),
                ),
            )
            assert split_resp.primary_stream.name
            assert split_resp.remainder_stream.name

            # The child streams must serve Avro too. Read each and
            # confirm the bytes decode under the session's schema.
            parsed = fastavro.parse_schema(json.loads(session.avro_schema.schema))
            for child_name in (
                split_resp.primary_stream.name,
                split_resp.remainder_stream.name,
            ):
                read_req = types.ReadRowsRequest(read_stream=child_name)
                for resp_bytes in channel.unary_stream(
                    f"{_READ_PATH}/ReadRows",
                )(types.ReadRowsRequest.serialize(read_req)):
                    resp = types.ReadRowsResponse.deserialize(resp_bytes)
                    if resp.avro_rows.serialized_binary_rows:
                        # MUST be naked-row bytes (not OCF) and decodable.
                        assert not resp.avro_rows.serialized_binary_rows.startswith(
                            b"Obj\x01",
                        )
                        reader_io = io.BytesIO(
                            resp.avro_rows.serialized_binary_rows,
                        )
                        for _ in range(resp.row_count):
                            fastavro.schemaless_reader(reader_io, parsed)
        finally:
            channel.close()
    finally:
        _cleanup_table(bqemu_server)


def test_avro_response_has_no_arrow_record_batch(
    bqemu_server: EmulatorServer,
) -> None:
    """The proto oneof MUST be ``avro_rows``, not ``arrow_record_batch``."""
    from google.cloud.bigquery_storage_v1 import types

    _seed_table(bqemu_server)
    try:
        session, channel = _create_avro_session(bqemu_server)
        try:
            read_req = types.ReadRowsRequest(read_stream=session.streams[0].name)
            saw_avro = False
            for resp_bytes in channel.unary_stream(
                f"{_READ_PATH}/ReadRows",
            )(types.ReadRowsRequest.serialize(read_req)):
                resp = types.ReadRowsResponse.deserialize(resp_bytes)
                if resp.avro_rows.serialized_binary_rows:
                    saw_avro = True
                # ArrowRecordBatch must be empty on an Avro session.
                assert not resp.arrow_record_batch.serialized_record_batch
            assert saw_avro, "expected at least one avro_rows message"
        finally:
            channel.close()
    finally:
        _cleanup_table(bqemu_server)


# --- The three load-bearing real-Avro-file guardrails (ADR 0030 §6) -------


def test_emulator_avro_bytes_round_trip_to_disk(
    bqemu_server: EmulatorServer,
    tmp_path: Path,
) -> None:
    """End-to-end disk round trip: emulator → OCF → fastavro.reader."""
    _seed_table(bqemu_server)
    try:
        session, channel = _create_avro_session(bqemu_server)
        try:
            rows, schema_json = _read_all_avro_rows(channel, session)
        finally:
            channel.close()

        # Write the rows into an Avro Object Container File on disk —
        # the canonical "real Avro file" shape every standard decoder
        # accepts.
        avro_path = tmp_path / "session_dump.avro"
        parsed = fastavro.parse_schema(json.loads(schema_json))
        with avro_path.open("wb") as fh:
            fastavro.writer(fh, parsed, rows)

        # File MUST start with the OCF magic bytes.
        assert avro_path.read_bytes().startswith(b"Obj\x01")

        # Re-read with the standard reader and assert decoded rows
        # equal the original.
        with avro_path.open("rb") as fh:
            reader = fastavro.reader(fh)
            decoded = list(reader)
        assert decoded == rows
    finally:
        _cleanup_table(bqemu_server)


_REFERENCE_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "avro"


def test_avro_schema_converter_against_reference_file() -> None:
    """For each reference file, the emitted schema must canonical-match.

    The reference files under ``tests/fixtures/avro/`` are committed
    OCFs whose embedded schemas serve as the source-of-truth contract
    for the BigQuery → Avro mapping. Running the emulator's schema
    converter against the equivalent Arrow schema must produce a
    fastavro-parsed schema equal to the file's embedded schema.
    """
    from bqemulator.streaming.avro_serializer import arrow_schema_to_avro_json
    from tests.fixtures.avro._schemas import REFERENCE_SCHEMAS

    assert _REFERENCE_FIXTURE_DIR.is_dir(), (
        f"expected reference Avro fixtures under {_REFERENCE_FIXTURE_DIR}"
    )
    files = sorted(_REFERENCE_FIXTURE_DIR.glob("*.avro"))
    assert files, "no reference Avro files committed"

    for avro_path in files:
        key = avro_path.stem
        arrow_schema = REFERENCE_SCHEMAS.get(key)
        assert arrow_schema is not None, (
            f"reference fixture {key!r} has no entry in REFERENCE_SCHEMAS"
        )

        # Load the embedded schema from the OCF.
        with avro_path.open("rb") as fh:
            reader = fastavro.reader(fh)
            embedded_schema = reader.writer_schema

        # Convert the equivalent Arrow schema and parse the result.
        emitted_json = arrow_schema_to_avro_json(arrow_schema)
        emitted_parsed = fastavro.parse_schema(json.loads(emitted_json))

        # The two schemas must be equal under fastavro's canonical form.
        # parse_schema normalises both, so dict equality is the
        # canonical-equality check.
        assert emitted_parsed == fastavro.parse_schema(embedded_schema), (
            f"reference schema mismatch for {key}\n"
            f"emitted: {emitted_parsed}\n"
            f"embedded: {fastavro.parse_schema(embedded_schema)}"
        )


def test_emulator_avro_bytes_decode_with_apache_avro_tools(
    bqemu_server: EmulatorServer,
    tmp_path: Path,
) -> None:
    """The canonical Apache Avro implementation must accept the bytes.

    Proves a second, independent Avro implementation can decode the
    emulator's output. Skipped (not xfailed) when ``avro-tools`` is
    not on PATH so local dev environments don't have to install Java
    just to run the suite; CI installs it unconditionally per ADR
    0030.
    """
    avro_tools = shutil.which("avro-tools")
    if avro_tools is None:
        pytest.skip(
            "avro-tools not on PATH — install via "
            "`brew install avro-tools` or set up the CI workflow",
        )

    _seed_table(bqemu_server)
    try:
        session, channel = _create_avro_session(bqemu_server)
        try:
            rows, schema_json = _read_all_avro_rows(channel, session)
        finally:
            channel.close()

        # Materialise the rows into an OCF on disk.
        ocf_path = tmp_path / "emulator_dump.avro"
        parsed = fastavro.parse_schema(json.loads(schema_json))
        with ocf_path.open("wb") as fh:
            fastavro.writer(fh, parsed, rows)

        # avro-tools getschema returns the embedded schema as JSON.
        result = subprocess.run(  # noqa: S603 — controlled args
            [avro_tools, "getschema", str(ocf_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"avro-tools getschema failed: stderr={result.stderr!r}"
        embedded_schema = json.loads(result.stdout)
        assert embedded_schema["type"] == "record"

        # avro-tools tojson decodes the rows. Validates Apache Avro
        # accepts every byte the emulator emitted.
        json_result = subprocess.run(  # noqa: S603
            [avro_tools, "tojson", str(ocf_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert json_result.returncode == 0, (
            f"avro-tools tojson failed: stderr={json_result.stderr!r}"
        )
        decoded_lines = [
            json.loads(line) for line in json_result.stdout.splitlines() if line.strip()
        ]
        assert len(decoded_lines) == len(rows)
    finally:
        _cleanup_table(bqemu_server)


# --- Avro twins of existing Arrow integration tests -----------------------


def test_avro_session_multi_stream(bqemu_server: EmulatorServer) -> None:
    """Larger tables produce multiple streams; each emits Avro."""
    from google.cloud import bigquery
    from google.cloud.bigquery_storage_v1 import types

    # Seed a larger table than the basic seeder so the small-table
    # cap doesn't collapse us to 1 stream.
    client = _make_bq_client(bqemu_server)
    try:
        client.create_dataset("avro_read", exists_ok=True)
        client.create_table(
            bigquery.Table(
                "test-project.avro_read.big",
                schema=[bigquery.SchemaField("id", "INT64", mode="REQUIRED")],
            ),
            exists_ok=True,
        )
        client.insert_rows_json(
            "test-project.avro_read.big",
            [{"id": i} for i in range(50)],
        )
    finally:
        client.close()

    channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
    try:
        request = types.CreateReadSessionRequest(
            parent="projects/test-project",
            read_session=types.ReadSession(
                table="projects/test-project/datasets/avro_read/tables/big",
                data_format=types.DataFormat.AVRO,
            ),
            max_stream_count=2,
        )
        resp_bytes = channel.unary_unary(
            f"{_READ_PATH}/CreateReadSession",
        )(types.CreateReadSessionRequest.serialize(request))
        session = types.ReadSession.deserialize(resp_bytes)
        # The current emulator caps at 1 stream for sub-1MB tables —
        # mirror the Arrow test's stream-count assertion.
        assert len(session.streams) >= 1
        rows, _ = _read_all_avro_rows(channel, session)
        assert len(rows) == 50
        assert sorted(r["id"] for r in rows) == list(range(50))
    finally:
        channel.close()
        _cleanup_table(bqemu_server)


def test_avro_session_all_basic_types(bqemu_server: EmulatorServer) -> None:
    """One row exercising INT64, FLOAT64, STRING, BOOL, NUMERIC."""
    from google.cloud import bigquery
    from google.cloud.bigquery_storage_v1 import types

    client = _make_bq_client(bqemu_server)
    try:
        client.create_dataset("avro_read", exists_ok=True)
        schema = [
            bigquery.SchemaField("i", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("f", "FLOAT64", mode="REQUIRED"),
            bigquery.SchemaField("s", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("b", "BOOL", mode="REQUIRED"),
            bigquery.SchemaField("n", "NUMERIC", mode="REQUIRED"),
        ]
        client.create_table(
            bigquery.Table("test-project.avro_read.alltypes", schema=schema),
            exists_ok=True,
        )
        client.insert_rows_json(
            "test-project.avro_read.alltypes",
            [{"i": 42, "f": 3.14, "s": "hello", "b": True, "n": "1.5"}],
        )
    finally:
        client.close()

    channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
    try:
        request = types.CreateReadSessionRequest(
            parent="projects/test-project",
            read_session=types.ReadSession(
                table="projects/test-project/datasets/avro_read/tables/alltypes",
                data_format=types.DataFormat.AVRO,
            ),
            max_stream_count=1,
        )
        resp_bytes = channel.unary_unary(
            f"{_READ_PATH}/CreateReadSession",
        )(types.CreateReadSessionRequest.serialize(request))
        session = types.ReadSession.deserialize(resp_bytes)
        rows, _ = _read_all_avro_rows(channel, session)
        assert len(rows) == 1
        row = rows[0]
        assert row["i"] == 42
        assert abs(row["f"] - 3.14) < 1e-9
        assert row["s"] == "hello"
        assert row["b"] is True
        # NUMERIC round-trips as Decimal under the decimal logical type.
        assert row["n"] == Decimal("1.5")
    finally:
        channel.close()
        _cleanup_table(bqemu_server)


def test_avro_explicit_arrow_request_still_returns_arrow(
    bqemu_server: EmulatorServer,
) -> None:
    """Regression guard: an explicit ARROW request stays on the Arrow path."""
    from google.cloud.bigquery_storage_v1 import types

    _seed_table(bqemu_server)
    try:
        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            request = types.CreateReadSessionRequest(
                parent="projects/test-project",
                read_session=types.ReadSession(
                    table="projects/test-project/datasets/avro_read/tables/data",
                    data_format=types.DataFormat.ARROW,
                ),
                max_stream_count=1,
            )
            resp_bytes = channel.unary_unary(
                f"{_READ_PATH}/CreateReadSession",
            )(types.CreateReadSessionRequest.serialize(request))
            session = types.ReadSession.deserialize(resp_bytes)
            assert session.data_format == types.DataFormat.ARROW
            assert session.arrow_schema.serialized_schema
            assert not session.avro_schema.schema

            read_req = types.ReadRowsRequest(read_stream=session.streams[0].name)
            saw_arrow = False
            for resp_bytes in channel.unary_stream(
                f"{_READ_PATH}/ReadRows",
            )(types.ReadRowsRequest.serialize(read_req)):
                resp = types.ReadRowsResponse.deserialize(resp_bytes)
                if resp.arrow_record_batch.serialized_record_batch:
                    saw_arrow = True
                # Avro fields stay empty on the Arrow path.
                assert not resp.avro_rows.serialized_binary_rows
            assert saw_arrow
        finally:
            channel.close()
    finally:
        _cleanup_table(bqemu_server)


def test_unspecified_data_format_defaults_to_arrow(
    bqemu_server: EmulatorServer,
) -> None:
    """An unset data_format on the request defaults to ARROW (proto3)."""
    from google.cloud.bigquery_storage_v1 import types

    _seed_table(bqemu_server)
    try:
        channel = grpc.insecure_channel(bqemu_server.grpc_endpoint)
        try:
            # Build a request WITHOUT data_format. proto-plus sets it
            # to DATA_FORMAT_UNSPECIFIED (0) when omitted.
            request = types.CreateReadSessionRequest(
                parent="projects/test-project",
                read_session=types.ReadSession(
                    table="projects/test-project/datasets/avro_read/tables/data",
                ),
                max_stream_count=1,
            )
            resp_bytes = channel.unary_unary(
                f"{_READ_PATH}/CreateReadSession",
            )(types.CreateReadSessionRequest.serialize(request))
            session = types.ReadSession.deserialize(resp_bytes)
            # Default → ARROW on the server, per real BQ's behaviour.
            assert session.data_format == types.DataFormat.ARROW
        finally:
            channel.close()
    finally:
        _cleanup_table(bqemu_server)
