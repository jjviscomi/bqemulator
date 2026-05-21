"""Unit tests for the gRPC-shape conformance corpus framework (P3.d).

Pins the contract of:

- :mod:`tests.conformance._grpc_corpus` — fixture discovery, request /
  response models, proto-as-JSON round-trip, JSON dotted-path walker,
  placeholder expansion.
- :mod:`tests.conformance._grpc_comparison` — structural-subset
  message comparison, WILDCARD semantics, status / error-message
  matching, recorder-side volatile-field masking.

The runner-side end-to-end behaviour is covered by the conformance
runner (``test_grpc_corpus.py``); this module pins the algorithms in
isolation so a regression in the dotted-path walker or the proto
round-trip fails fast at the unit tier rather than as a flaky
conformance diff.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conformance._grpc_comparison import (
    GrpcCompareReport,
    compare_avro_schema,
    compare_grpc_calls,
    decode_and_compare_avro_rows,
    mask_volatile_fields,
)
from tests.conformance._grpc_corpus import (
    DEFAULT_METHOD_KIND,
    WILDCARD,
    GrpcExpectedCall,
    deserialize_response,
    discover_grpc_fixtures,
    expand_placeholders,
    expand_placeholders_in_json,
    proto_class_for,
    proto_from_dict,
    proto_to_dict,
    resolve_dotted_path,
    serialize_request,
)

pytestmark = pytest.mark.unit


class TestResolveDottedPath:
    """Walks dicts and lists, fails loudly on a missing key."""

    def test_simple_key(self) -> None:
        assert resolve_dotted_path({"a": 1}, "a") == 1

    def test_nested_key(self) -> None:
        body = {"streams": {"name": "abc"}}
        assert resolve_dotted_path(body, "streams.name") == "abc"

    def test_list_index(self) -> None:
        body = {"streams": [{"name": "s0"}, {"name": "s1"}]}
        assert resolve_dotted_path(body, "streams.1.name") == "s1"

    def test_missing_key_raises(self) -> None:
        with pytest.raises(KeyError, match="not in"):
            resolve_dotted_path({"a": 1}, "b")

    def test_index_out_of_bounds_raises(self) -> None:
        with pytest.raises(IndexError, match="out of bounds"):
            resolve_dotted_path({"streams": [{}]}, "streams.5.name")

    def test_descend_into_scalar_raises(self) -> None:
        with pytest.raises(KeyError, match="cannot descend"):
            resolve_dotted_path({"a": "leaf"}, "a.b")


class TestExpandPlaceholders:
    """``${TOKEN}`` substitution must be strict and JSON-aware."""

    def test_string_substitution(self) -> None:
        assert expand_placeholders("hi ${NAME}", {"NAME": "world"}) == "hi world"

    def test_unknown_token_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown placeholder"):
            expand_placeholders("hi ${MISSING}", {"NAME": "world"})

    def test_json_walks_nested(self) -> None:
        value = {"path": "/p/${PROJECT}/q", "body": {"q": "SELECT ${N}"}}
        out = expand_placeholders_in_json(value, {"PROJECT": "p1", "N": "42"})
        assert out == {"path": "/p/p1/q", "body": {"q": "SELECT 42"}}

    def test_json_passes_scalars_through(self) -> None:
        value = {"a": 1, "b": True, "c": None}
        assert expand_placeholders_in_json(value, {}) == value


class TestProtoRoundTrip:
    """proto-plus message → dict → message round-trip."""

    def test_proto_class_for_request(self) -> None:
        cls = proto_class_for("BigQueryRead.CreateReadSession", role="request")
        assert cls.__name__ == "CreateReadSessionRequest"

    def test_proto_class_for_response(self) -> None:
        cls = proto_class_for("BigQueryRead.CreateReadSession", role="response")
        assert cls.__name__ == "ReadSession"

    def test_proto_class_unknown_method_raises(self) -> None:
        with pytest.raises(ValueError, match="No request proto class"):
            proto_class_for("BogusService.Method", role="request")

    def test_proto_class_invalid_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role must be"):
            proto_class_for("BigQueryRead.CreateReadSession", role="other")

    def test_proto_to_dict_returns_jsonable(self) -> None:
        message = proto_from_dict(
            "BigQueryRead.CreateReadSession",
            "request",
            {"parent": "projects/foo", "max_stream_count": 4},
        )
        out = proto_to_dict(message)
        # Serialisable back to JSON without TypeError.
        assert json.loads(json.dumps(out)) == out
        assert out["parent"] == "projects/foo"
        assert out["max_stream_count"] == 4

    def test_proto_from_dict_round_trip(self) -> None:
        payload = {
            "parent": "projects/foo",
            "read_session": {
                "table": "projects/foo/datasets/d/tables/t",
                "data_format": "ARROW",
            },
            "max_stream_count": 1,
        }
        message = proto_from_dict("BigQueryRead.CreateReadSession", "request", payload)
        round_tripped = proto_to_dict(message)
        # The round-trip MUST preserve every leaf the caller set.
        assert round_tripped["parent"] == "projects/foo"
        assert round_tripped["read_session"]["data_format"] == "ARROW"
        assert round_tripped["read_session"]["table"] == "projects/foo/datasets/d/tables/t"

    def test_serialize_request_yields_bytes(self) -> None:
        wire_bytes = serialize_request(
            "BigQueryRead.CreateReadSession",
            {"parent": "projects/foo"},
        )
        assert isinstance(wire_bytes, bytes)
        assert wire_bytes  # non-empty for non-default request

    def test_deserialize_response_roundtrip(self) -> None:
        # Build a ReadSession via the response class, serialise, and
        # round-trip through the framework's deserializer.
        from google.cloud.bigquery_storage_v1 import types

        session = types.ReadSession(name="projects/p/locations/US/sessions/abc")
        wire = types.ReadSession.serialize(session)
        out = deserialize_response("BigQueryRead.CreateReadSession", wire)
        assert out["name"] == "projects/p/locations/US/sessions/abc"


class TestDefaultMethodKind:
    """Default kind table covers every method the framework supports."""

    def test_read_methods_have_kinds(self) -> None:
        assert DEFAULT_METHOD_KIND["BigQueryRead.CreateReadSession"] == "unary"
        assert DEFAULT_METHOD_KIND["BigQueryRead.ReadRows"] == "server_stream"
        assert DEFAULT_METHOD_KIND["BigQueryRead.SplitReadStream"] == "unary"

    def test_write_methods_have_kinds(self) -> None:
        assert DEFAULT_METHOD_KIND["BigQueryWrite.CreateWriteStream"] == "unary"
        assert DEFAULT_METHOD_KIND["BigQueryWrite.AppendRows"] == "bidi_stream"
        assert DEFAULT_METHOD_KIND["BigQueryWrite.GetWriteStream"] == "unary"
        assert DEFAULT_METHOD_KIND["BigQueryWrite.FinalizeWriteStream"] == "unary"
        assert DEFAULT_METHOD_KIND["BigQueryWrite.BatchCommitWriteStreams"] == "unary"
        assert DEFAULT_METHOD_KIND["BigQueryWrite.FlushRows"] == "unary"


class TestCompareGrpcCalls:
    """Structural-subset message matching with WILDCARD semantics."""

    @staticmethod
    def _expected_ok(method: str, response: dict) -> GrpcExpectedCall:
        return GrpcExpectedCall(method=method, status="OK", response=response)

    def test_exact_match(self) -> None:
        expected = [self._expected_ok("BigQueryRead.CreateReadSession", {"name": "foo"})]
        actual = [
            {
                "method": "BigQueryRead.CreateReadSession",
                "status": "OK",
                "response": {"name": "foo"},
            }
        ]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert report.ok
        assert not report.diffs

    def test_method_mismatch(self) -> None:
        expected = [self._expected_ok("BigQueryRead.CreateReadSession", {"name": "foo"})]
        actual = [{"method": "BigQueryRead.ReadRows", "status": "OK", "response": {"name": "foo"}}]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert not report.ok
        assert any("method" in d for d in report.diffs)

    def test_status_mismatch(self) -> None:
        expected = [self._expected_ok("BigQueryRead.CreateReadSession", {"name": "foo"})]
        actual = [
            {
                "method": "BigQueryRead.CreateReadSession",
                "status": "NOT_FOUND",
                "response": {"name": "foo"},
            }
        ]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert not report.ok
        assert any("status" in d for d in report.diffs)

    def test_call_count_mismatch(self) -> None:
        expected = [
            self._expected_ok("BigQueryRead.CreateReadSession", {}),
            self._expected_ok("BigQueryRead.ReadRows", {}),
        ]
        actual = [{"method": "BigQueryRead.CreateReadSession", "status": "OK", "response": {}}]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert not report.ok
        assert any("call_count" in d for d in report.diffs)

    def test_wildcard_accepts_any_value(self) -> None:
        expected = [self._expected_ok("BigQueryRead.CreateReadSession", {"name": WILDCARD})]
        actual = [
            {
                "method": "BigQueryRead.CreateReadSession",
                "status": "OK",
                "response": {"name": "session-runtime-id-not-recordable"},
            }
        ]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert report.ok

    def test_wildcard_accepts_absent_value(self) -> None:
        expected = [self._expected_ok("BigQueryRead.CreateReadSession", {"name": WILDCARD})]
        actual = [{"method": "BigQueryRead.CreateReadSession", "status": "OK", "response": {}}]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert report.ok

    def test_extra_keys_in_actual_are_tolerated(self) -> None:
        expected = [self._expected_ok("BigQueryRead.CreateReadSession", {"name": "foo"})]
        actual = [
            {
                "method": "BigQueryRead.CreateReadSession",
                "status": "OK",
                "response": {"name": "foo", "newField": "bonus"},
            }
        ]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert report.ok

    def test_missing_required_key_fails(self) -> None:
        expected = [
            self._expected_ok("BigQueryRead.CreateReadSession", {"name": "foo", "table": "t"})
        ]
        actual = [
            {
                "method": "BigQueryRead.CreateReadSession",
                "status": "OK",
                "response": {"name": "foo"},
            }
        ]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert not report.ok
        assert any("table" in d and "absent" in d for d in report.diffs)

    def test_streaming_response_list_length_mismatch(self) -> None:
        expected = [
            GrpcExpectedCall(
                method="BigQueryRead.ReadRows",
                status="OK",
                responses=({"row_count": "5"}, {"row_count": "5"}),
            )
        ]
        actual = [
            {
                "method": "BigQueryRead.ReadRows",
                "status": "OK",
                "responses": [{"row_count": "5"}],
            }
        ]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert not report.ok
        assert any("list length mismatch" in d for d in report.diffs)

    def test_error_message_substring_match(self) -> None:
        """``error_message`` matches if the recorded text is a substring."""
        expected = [
            GrpcExpectedCall(
                method="BigQueryWrite.GetWriteStream",
                status="NOT_FOUND",
                error_message="Requested entity was not found",
            )
        ]
        actual = [
            {
                "method": "BigQueryWrite.GetWriteStream",
                "status": "NOT_FOUND",
                "error_message": "Requested entity was not found. Entity: projects/x/...",
            }
        ]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert report.ok

    def test_error_message_substring_mismatch(self) -> None:
        expected = [
            GrpcExpectedCall(
                method="BigQueryWrite.GetWriteStream",
                status="NOT_FOUND",
                error_message="Requested entity was not found",
            )
        ]
        actual = [
            {
                "method": "BigQueryWrite.GetWriteStream",
                "status": "NOT_FOUND",
                "error_message": "Stream not found",
            }
        ]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert not report.ok
        assert any("error_message" in d for d in report.diffs)


class TestMaskVolatileFields:
    """The recorder's wildcard-masking helper."""

    def test_top_level_field(self) -> None:
        body = {"name": "abc", "table": "y"}
        mask_volatile_fields(body, ("name",))
        assert body == {"name": WILDCARD, "table": "y"}

    def test_nested_field(self) -> None:
        body = {"session": {"name": "abc", "table": "y"}}
        mask_volatile_fields(body, ("session.name",))
        assert body == {"session": {"name": WILDCARD, "table": "y"}}

    def test_list_each_mask(self) -> None:
        body = {"streams": [{"name": "s0"}, {"name": "s1"}]}
        mask_volatile_fields(body, ("streams[].name",))
        assert body == {"streams": [{"name": WILDCARD}, {"name": WILDCARD}]}

    def test_missing_path_is_silent(self) -> None:
        body = {"kind": "x"}
        mask_volatile_fields(body, ("name", "session.name"))
        assert body == {"kind": "x"}

    def test_multiple_paths_in_one_call(self) -> None:
        body = {"a": 1, "b": {"c": 2}}
        mask_volatile_fields(body, ("a", "b.c"))
        assert body == {"a": WILDCARD, "b": {"c": WILDCARD}}


class TestDiscoverGrpcFixtures:
    """End-to-end smoke test against the on-disk corpus."""

    def test_discovers_storage_read_and_storage_write(self) -> None:
        fixtures = discover_grpc_fixtures()
        assert fixtures, "expected at least one gRPC fixture under grpc_corpus/"
        ids = {f.id for f in fixtures}
        # Spot-check both services.
        assert any(fid.startswith("storage_read/sr_") for fid in ids)
        assert any(fid.startswith("storage_write/sw_") for fid in ids)

    def test_request_json_required(self, tmp_path: Path) -> None:
        """A fixture directory without request.json is silently skipped."""
        phase = tmp_path / "storage_read" / "no_request"
        phase.mkdir(parents=True)
        (phase / "setup.sql").write_text("SELECT 1")
        assert discover_grpc_fixtures(corpus_dir=tmp_path, include_unrecorded=True) == []

    def test_expected_response_required_by_default(self, tmp_path: Path) -> None:
        """Discovery skips fixtures without expected_response.json unless asked."""
        phase = tmp_path / "storage_read" / "no_expected"
        phase.mkdir(parents=True)
        (phase / "request.json").write_text(
            json.dumps(
                {
                    "calls": [
                        {
                            "method": "BigQueryRead.CreateReadSession",
                            "request": {"parent": "projects/x"},
                        }
                    ]
                }
            )
        )
        assert discover_grpc_fixtures(corpus_dir=tmp_path) == []
        fixtures = discover_grpc_fixtures(corpus_dir=tmp_path, include_unrecorded=True)
        assert len(fixtures) == 1
        assert fixtures[0].name == "no_expected"


class TestGrpcCompareReportContract:
    """Type contract of the report dataclass."""

    def test_report_default_state(self) -> None:
        report = GrpcCompareReport(ok=True)
        assert report.ok
        assert report.diffs == []

    def test_diffs_accumulate(self) -> None:
        """Multiple diffs are collected so an operator sees all regressions at once."""
        expected = [
            GrpcExpectedCall(
                method="BigQueryRead.CreateReadSession",
                status="OK",
                response={"name": "expected-name", "table": "t1"},
            )
        ]
        actual = [
            {
                "method": "BigQueryRead.CreateReadSession",
                "status": "NOT_FOUND",
                "response": {"name": "wrong", "table": "t1"},
            }
        ]
        report = compare_grpc_calls(expected=expected, actual=actual)
        assert len(report.diffs) >= 2  # status + response.name


class TestCompareAvroSchema:
    """Canonical-parse equality for the G3 three-layer comparator (ADR 0030)."""

    def test_identical_schemas_match(self) -> None:
        schema = json.dumps(
            {
                "type": "record",
                "name": "Row",
                "fields": [{"name": "id", "type": "long"}],
            }
        )
        diffs = compare_avro_schema(
            recorded_schema_json=schema,
            actual_schema_json=schema,
        )
        assert diffs == []

    def test_whitespace_normalisation_is_ignored(self) -> None:
        """fastavro.parse_schema canonicalises whitespace + key ordering."""
        pretty = json.dumps(
            {
                "type": "record",
                "name": "Row",
                "fields": [{"name": "id", "type": "long"}],
            },
            indent=4,
        )
        compact = json.dumps(
            {
                "fields": [{"type": "long", "name": "id"}],
                "name": "Row",
                "type": "record",
            }
        )
        assert (
            compare_avro_schema(
                recorded_schema_json=pretty,
                actual_schema_json=compact,
            )
            == []
        )

    def test_field_addition_surfaces_as_diff(self) -> None:
        recorded = json.dumps(
            {"type": "record", "name": "R", "fields": [{"name": "a", "type": "long"}]}
        )
        actual = json.dumps(
            {
                "type": "record",
                "name": "R",
                "fields": [
                    {"name": "a", "type": "long"},
                    {"name": "b", "type": "string"},
                ],
            }
        )
        diffs = compare_avro_schema(recorded_schema_json=recorded, actual_schema_json=actual)
        assert diffs
        assert any("canonical-parse mismatch" in d for d in diffs)

    def test_invalid_recorded_schema_surfaces_diff(self) -> None:
        diffs = compare_avro_schema(
            recorded_schema_json="{ not valid",
            actual_schema_json='{"type": "record", "name": "R", "fields": []}',
        )
        assert diffs
        assert any("recorded schema" in d for d in diffs)

    def test_invalid_actual_schema_surfaces_diff(self) -> None:
        diffs = compare_avro_schema(
            recorded_schema_json='{"type": "record", "name": "R", "fields": []}',
            actual_schema_json="not avro",
        )
        assert diffs
        assert any("emulator schema" in d for d in diffs)


class TestDecodeAndCompareAvroRows:
    """Decoded-row equality (third comparison layer)."""

    @staticmethod
    def _simple_schema_and_bytes(rows: list[dict]) -> tuple[str, bytes]:
        import io as _io

        import fastavro

        schema_json = json.dumps(
            {
                "type": "record",
                "name": "Row",
                "fields": [
                    {"name": "id", "type": "long"},
                    {"name": "name", "type": "string"},
                ],
            }
        )
        parsed = fastavro.parse_schema(json.loads(schema_json))
        sink = _io.BytesIO()
        for row in rows:
            fastavro.schemaless_writer(sink, parsed, row)
        return schema_json, sink.getvalue()

    def test_happy_path(self) -> None:
        rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        schema_json, payload = self._simple_schema_and_bytes(rows)
        diffs = decode_and_compare_avro_rows(
            schema_json=schema_json,
            actual_bytes=payload,
            expected_decoded_rows=rows,
        )
        assert diffs == []

    def test_value_divergence_per_row(self) -> None:
        rows = [{"id": 1, "name": "a"}]
        schema_json, payload = self._simple_schema_and_bytes(rows)
        diffs = decode_and_compare_avro_rows(
            schema_json=schema_json,
            actual_bytes=payload,
            expected_decoded_rows=[{"id": 99, "name": "a"}],
        )
        assert diffs
        assert any("avro_rows[0].id" in d for d in diffs)

    def test_truncated_bytes_surface_decode_error(self) -> None:
        rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        schema_json, payload = self._simple_schema_and_bytes(rows)
        # Truncate so the second row can't be fully decoded.
        truncated = payload[: max(1, len(payload) // 2)]
        diffs = decode_and_compare_avro_rows(
            schema_json=schema_json,
            actual_bytes=truncated,
            expected_decoded_rows=rows,
        )
        assert diffs
        assert any("decode failed" in d for d in diffs)

    def test_invalid_schema_surfaces_parse_error(self) -> None:
        diffs = decode_and_compare_avro_rows(
            schema_json="{ not valid",
            actual_bytes=b"",
            expected_decoded_rows=[],
        )
        assert diffs
        assert any("schema parse failed" in d for d in diffs)

    def test_float_tolerance_within_epsilon(self) -> None:
        import io as _io

        import fastavro

        schema_json = json.dumps(
            {
                "type": "record",
                "name": "R",
                "fields": [{"name": "f", "type": "double"}],
            }
        )
        parsed = fastavro.parse_schema(json.loads(schema_json))
        sink = _io.BytesIO()
        # Encode a value that round-trips bit-for-bit, then compare
        # against an "expected" that is the same value — proves the
        # tolerance-comparator returns empty.
        fastavro.schemaless_writer(sink, parsed, {"f": 1.0 + 1e-15})
        diffs = decode_and_compare_avro_rows(
            schema_json=schema_json,
            actual_bytes=sink.getvalue(),
            expected_decoded_rows=[{"f": 1.0}],
        )
        assert diffs == []  # within epsilon

    def test_nested_dict_diff(self) -> None:
        import io as _io

        import fastavro

        schema_json = json.dumps(
            {
                "type": "record",
                "name": "R",
                "fields": [
                    {
                        "name": "inner",
                        "type": {
                            "type": "record",
                            "name": "I",
                            "fields": [{"name": "v", "type": "long"}],
                        },
                    }
                ],
            }
        )
        parsed = fastavro.parse_schema(json.loads(schema_json))
        sink = _io.BytesIO()
        fastavro.schemaless_writer(sink, parsed, {"inner": {"v": 5}})
        diffs = decode_and_compare_avro_rows(
            schema_json=schema_json,
            actual_bytes=sink.getvalue(),
            expected_decoded_rows=[{"inner": {"v": 99}}],
        )
        assert diffs
        assert any("inner.v" in d for d in diffs)

    def test_extra_keys_in_actual_tolerated(self) -> None:
        """Emulator-side extra keys are tolerated (structural-subset rule)."""
        rows = [{"id": 1, "name": "a"}]
        schema_json, payload = self._simple_schema_and_bytes(rows)
        diffs = decode_and_compare_avro_rows(
            schema_json=schema_json,
            actual_bytes=payload,
            expected_decoded_rows=[{"id": 1}],  # name omitted from expected
        )
        assert diffs == []

    def test_missing_key_in_actual_surfaces_diff(self) -> None:
        rows = [{"id": 1, "name": "a"}]
        schema_json, payload = self._simple_schema_and_bytes(rows)
        diffs = decode_and_compare_avro_rows(
            schema_json=schema_json,
            actual_bytes=payload,
            expected_decoded_rows=[{"id": 1, "name": "a", "missing_field": "x"}],
        )
        assert diffs
        assert any("missing_field" in d and "absent" in d for d in diffs)

    def test_list_length_mismatch(self) -> None:
        import io as _io

        import fastavro

        schema_json = json.dumps(
            {
                "type": "record",
                "name": "R",
                "fields": [{"name": "tags", "type": {"type": "array", "items": "string"}}],
            }
        )
        parsed = fastavro.parse_schema(json.loads(schema_json))
        sink = _io.BytesIO()
        fastavro.schemaless_writer(sink, parsed, {"tags": ["a", "b"]})
        diffs = decode_and_compare_avro_rows(
            schema_json=schema_json,
            actual_bytes=sink.getvalue(),
            expected_decoded_rows=[{"tags": ["a", "b", "c"]}],
        )
        assert diffs
        assert any("list length" in d for d in diffs)

    def test_empty_rows_empty_bytes(self) -> None:
        """No expected rows + no bytes → no diffs."""
        diffs = decode_and_compare_avro_rows(
            schema_json='{"type":"record","name":"R","fields":[]}',
            actual_bytes=b"",
            expected_decoded_rows=[],
        )
        assert diffs == []
