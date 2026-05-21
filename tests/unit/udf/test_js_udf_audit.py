"""Production-audit coverage for js_udf error paths + type coercions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
import pytest_asyncio

from bqemulator.catalog.models import RoutineArgument, RoutineMeta
from bqemulator.config import Settings
from bqemulator.domain.errors import InvalidQueryError
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.js_udf import (
    JavaScriptUDFRuntime,
    _coerce_arg_for_js,
    _coerce_return_from_js,
    _from_js_value,
    _load_mini_racer_errors,
    _raise_js_error,
    _to_json_value,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 4, 15, tzinfo=UTC)


def _routine(
    rid: str,
    *,
    body: str = "return x;",
) -> RoutineMeta:
    return RoutineMeta(
        project_id="p",
        dataset_id="ds",
        routine_id=rid,
        routine_type="SCALAR_FUNCTION",
        language="JAVASCRIPT",
        definition_body=body,
        arguments=(RoutineArgument(name="x", data_type={"typeKind": "INT64"}),),
        return_type={"typeKind": "INT64"},
        creation_time=NOW,
        last_modified_time=NOW,
        etag="e",
    )


@pytest_asyncio.fixture
async def engine(ephemeral_settings: Settings) -> AsyncIterator[DuckDBEngine]:
    e = DuckDBEngine(ephemeral_settings)
    await e.start()
    try:
        yield e
    finally:
        await e.stop()


class TestToJsonValue:
    def test_passthrough(self) -> None:
        assert _to_json_value(1) == 1
        assert _to_json_value("s") == "s"
        assert _to_json_value(None) is None
        assert _to_json_value(True) is True  # noqa: FBT003

    def test_bytes_to_base64(self) -> None:
        assert _to_json_value(b"hello") == "aGVsbG8="

    def test_decimal_to_string(self) -> None:
        assert _to_json_value(Decimal("3.14")) == "3.14"

    def test_datetime_gets_utc_tz(self) -> None:
        naive = datetime(2026, 1, 1, 12, 0)  # noqa: DTZ001
        assert _to_json_value(naive).endswith("+00:00")

    def test_aware_datetime(self) -> None:
        aware = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert _to_json_value(aware).startswith("2026-01-01T12:00:00")

    def test_date_time(self) -> None:
        assert _to_json_value(date(2026, 4, 15)) == "2026-04-15"
        assert _to_json_value(time(10, 30)) == "10:30:00"

    def test_list_and_dict(self) -> None:
        assert _to_json_value([1, b"x"]) == [1, "eA=="]
        assert _to_json_value({"k": b"x"}) == {"k": "eA=="}


class TestFromJsValue:
    def test_passthrough(self) -> None:
        assert _from_js_value(42) == 42
        assert _from_js_value("ok") == "ok"
        assert _from_js_value(None) is None


class TestCoerceArgForJs:
    """BigQuery encodes INT64 / NUMERIC / BIGNUMERIC as JS strings to preserve precision."""

    def test_int64_stringifies(self) -> None:
        assert _coerce_arg_for_js(41, "INT64") == "41"

    def test_int64_none_stays_none(self) -> None:
        assert _coerce_arg_for_js(None, "INT64") is None

    def test_int64_bool_does_not_stringify(self) -> None:
        # bool is a subclass of int in Python but is encoded as a JS Boolean.
        assert _coerce_arg_for_js(True, "INT64") is True  # noqa: FBT003

    def test_numeric_decimal_stringifies(self) -> None:
        assert _coerce_arg_for_js(Decimal("3.14"), "NUMERIC") == "3.14"

    def test_bignumeric_stringifies(self) -> None:
        assert _coerce_arg_for_js(Decimal("1e30"), "BIGNUMERIC") == "1E+30"

    def test_float64_passes_through(self) -> None:
        assert _coerce_arg_for_js(3.14, "FLOAT64") == 3.14

    def test_string_passes_through(self) -> None:
        assert _coerce_arg_for_js("hello", "STRING") == "hello"

    def test_unknown_kind_falls_back_to_to_json(self) -> None:
        # Decimal under an unknown kind uses _to_json_value (str conversion).
        assert _coerce_arg_for_js(Decimal("1.5"), "") == "1.5"


class TestCoerceReturnFromJs:
    """JS UDFs may return either a JS Number or JS String for precision-tracked types."""

    def test_int64_string_widens_to_int(self) -> None:
        assert _coerce_return_from_js("411", "INT64") == 411

    def test_int64_number_passes_through(self) -> None:
        assert _coerce_return_from_js(42, "INT64") == 42

    def test_numeric_string_widens_to_decimal(self) -> None:
        assert _coerce_return_from_js("3.14", "NUMERIC") == Decimal("3.14")

    def test_string_return_stays_string(self) -> None:
        assert _coerce_return_from_js("hello", "STRING") == "hello"

    def test_null_passes_through(self) -> None:
        assert _coerce_return_from_js(None, "INT64") is None

    def test_invalid_int_string_falls_back(self) -> None:
        # An invalid number-shaped string can't widen — pass through.
        assert _coerce_return_from_js("not a number", "INT64") == "not a number"


class TestIdempotentReregister:
    def test_double_materialize_replaces(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        """Re-registering drops the prior V8 context (covers the cleanup branch).

        BigQuery encodes INT64 JS UDF arguments as a JS String to preserve
        precision (see ``routines_scripting/js_udf_*`` conformance fixtures),
        so ``x + 2`` is string concatenation: ``"10" + 2 == "102"``. The
        return-coercion pass widens that string back to INT64 (102) when
        the routine's return type is INT64.
        """
        rt = JavaScriptUDFRuntime(
            cpu_timeout_ms=ephemeral_settings.udf_js_timeout_ms,
            memory_limit_bytes=ephemeral_settings.udf_js_memory_bytes,
        )
        r1 = _routine("dup", body="return Number(x) + 1;")
        r2 = _routine("dup", body="return Number(x) + 2;")
        rt.materialize(r1, engine)
        rt.materialize(r2, engine)
        (result,) = engine.execute("SELECT p__ds__dup(10)").fetchone()
        assert result == 12


class TestErrorRaising:
    def test_generic_error_wraps(self) -> None:
        exc = RuntimeError("unknown boom")
        with pytest.raises(InvalidQueryError, match="unknown boom"):
            _raise_js_error("JS UDF failure", exc, "my_fn")

    def test_timeout_error_wraps(self) -> None:
        _, _, _, timeout_cls = _load_mini_racer_errors()
        exc = timeout_cls()
        with pytest.raises(InvalidQueryError, match="CPU time limit"):
            _raise_js_error("JS UDF failure", exc, "my_fn")

    def test_oom_error_wraps(self) -> None:
        _, oom_cls, _, _ = _load_mini_racer_errors()
        exc = oom_cls()
        with pytest.raises(InvalidQueryError, match="memory limit"):
            _raise_js_error("JS UDF failure", exc, "my_fn")

    def test_parse_error_wraps(self) -> None:
        _, _, parse_cls, _ = _load_mini_racer_errors()
        exc = parse_cls()
        with pytest.raises(InvalidQueryError, match="parse error"):
            _raise_js_error("JS UDF failure", exc, "my_fn")

    def test_eval_error_wraps(self) -> None:
        eval_cls, _, _, _ = _load_mini_racer_errors()
        exc = eval_cls()
        with pytest.raises(InvalidQueryError, match="runtime error"):
            _raise_js_error("JS UDF failure", exc, "my_fn")


class TestLoadMiniRacerErrors:
    def test_returns_four_tuple(self) -> None:
        result = _load_mini_racer_errors()
        assert len(result) == 4
        for cls in result:
            assert isinstance(cls, type)
