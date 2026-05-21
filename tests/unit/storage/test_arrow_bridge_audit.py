"""Coverage-audit tests for :mod:`bqemulator.storage.arrow_bridge`.

Closes Phase 10 production-readiness audit gap #3 — ``arrow_bridge.py``
sat at 79.88% before this file, with the uncovered lines all clustered
in the defensive "value isn't the expected Python type" fallback
branches of :func:`_format_bq_value` and :func:`_coerce_to_arrow_value`,
plus the BigQuery-canonical interval-string dispatch and the WKT parse
failure.

The branches reach lines pyarrow's normal ``to_pylist()`` /
``pa.array()`` flows would never produce (e.g. a non-``date`` value in
a DATE arrow type), but the emulator's input does come from JSON
deserialisation and from external clients — these fallbacks are real
defensive code that must do something documented when the input is
malformed. Hitting them directly is the cleanest way to lock the
defensive contract in.

Test strategy:
1. Call the private formatter/coercer with arrow types that match each
   branch, but with Python values that miss the ``isinstance`` happy
   path so the ``return str(value)`` / ``return value`` fallback fires.
2. Drive the interval-string dispatch table with one literal per
   recognised shape.
3. Exercise the WKT error path by handing :func:`_wkt_to_wkb_hex` a
   nonsense input.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pyarrow as pa
import pytest

from bqemulator.storage import arrow_bridge as ab

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _format_bq_value fallbacks — covers lines 130, 146, 152, 158, 177, 187,
# 200, 203, plus the naive-datetime tz-substitution at 170-172.
# ---------------------------------------------------------------------------


class TestFormatBqValueFallbacks:
    """Every non-happy-path branch in ``_format_bq_value`` must produce a string."""

    def test_decimal_with_non_decimal_value(self) -> None:
        # Arrow type is decimal, but the value is a plain float — the
        # ``isinstance(value, Decimal)`` branch is missed, fallback fires.
        out = ab._format_bq_value(1.25, pa.decimal128(38, 9))
        assert isinstance(out, str)

    def test_binary_with_non_bytes_value(self) -> None:
        # Arrow type is binary but value is a string — fallback emits str().
        out = ab._format_bq_value("not-bytes", pa.binary())
        assert out == "not-bytes"

    def test_date_with_non_date_value(self) -> None:
        # Arrow type is date but value is a string.
        out = ab._format_bq_value("2026-04-15", pa.date32())
        assert out == "2026-04-15"

    def test_time_with_non_time_value(self) -> None:
        out = ab._format_bq_value("14:30:00", pa.time64("us"))
        assert out == "14:30:00"

    def test_timestamp_with_non_datetime_value(self) -> None:
        # Non-datetime value on a tz-aware timestamp type → str() fallback.
        out = ab._format_bq_value("not-a-dt", pa.timestamp("us", tz="UTC"))
        assert out == "not-a-dt"

    def test_timestamp_tz_aware_with_naive_datetime_substitutes_utc(self) -> None:
        """Naive datetime on a tz-aware timestamp type is treated as UTC.

        Lines 170-172 substitute UTC for ``tzinfo`` when the incoming
        value is naive. The expected output is microseconds-since-epoch
        as a string, computed from the same datetime stamped UTC.
        """
        naive = datetime(2026, 4, 15, 12, 0, 0)  # noqa: DTZ001 — intentional
        out = ab._format_bq_value(naive, pa.timestamp("us", tz="UTC"))
        expected = int(naive.replace(tzinfo=UTC).timestamp() * 1_000_000)
        assert out == str(expected)

    def test_list_with_non_list_value(self) -> None:
        out = ab._format_bq_value("not-a-list", pa.list_(pa.int64()))
        assert out == "not-a-list"

    def test_struct_with_non_dict_value(self) -> None:
        struct_type = pa.struct([pa.field("x", pa.int64())])
        out = ab._format_bq_value("not-a-dict", struct_type)
        assert out == "not-a-dict"

    def test_unknown_arrow_type_falls_back_to_string(self) -> None:
        # ``null`` is not matched by any of the dispatch branches → final
        # ``return str(value)`` on line 203.
        out = ab._format_bq_value("placeholder", pa.null())
        assert out == "placeholder"


# ---------------------------------------------------------------------------
# _coerce_to_arrow_value fallbacks — covers lines 298, 310, 319, 332,
# the ISO parse failures at 344-346 / 355-357 / 366-372, and the non-string
# branches.
# ---------------------------------------------------------------------------


class TestCoerceToArrowFallbacks:
    """Every defensive branch in ``_coerce_to_arrow_value`` must keep going."""

    def test_bool_from_int_value(self) -> None:
        # ``1`` is neither ``bool`` nor ``str`` → ``bool(int)`` fallback at 298.
        # (Note: True/False are ``int`` subclasses; we pass an explicit ``int``
        # value that isn't a Python ``bool``.)
        coerced = ab._coerce_to_arrow_value(2, pa.bool_())
        assert coerced is True

    def test_binary_from_bytes_value(self) -> None:
        # value is bytes (not a string) → ``bytes(value)`` fallback at 310.
        coerced = ab._coerce_to_arrow_value(b"raw", pa.binary())
        assert coerced == b"raw"

    def test_list_from_non_list_value(self) -> None:
        # value is a string, target type is list → fallback returns value
        # unchanged (line 319). pyarrow will reject the resulting array,
        # but the coercer's contract is "pass it through so the caller
        # raises".
        coerced = ab._coerce_to_arrow_value("not-a-list", pa.list_(pa.int64()))
        assert coerced == "not-a-list"

    def test_struct_from_non_dict_value(self) -> None:
        # value is a string, target type is struct → fallback at 332.
        struct_type = pa.struct([pa.field("x", pa.int64())])
        coerced = ab._coerce_to_arrow_value("not-a-dict", struct_type)
        assert coerced == "not-a-dict"

    def test_timestamp_with_unparseable_string_returns_original(self) -> None:
        # Bad ISO string → ``ValueError`` swallowed, original value returned
        # (lines 344-346).
        coerced = ab._coerce_to_arrow_value("not-an-iso", pa.timestamp("us"))
        assert coerced == "not-an-iso"

    def test_timestamp_with_non_string_returns_value(self) -> None:
        # Non-string timestamp value (e.g. a ``datetime`` already, or any
        # other object) → ``return value`` at line 346.
        already = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        coerced = ab._coerce_to_arrow_value(already, pa.timestamp("us", tz="UTC"))
        assert coerced is already

    def test_date_with_unparseable_string_returns_original(self) -> None:
        coerced = ab._coerce_to_arrow_value("not-a-date", pa.date32())
        assert coerced == "not-a-date"

    def test_date_with_non_string_returns_value(self) -> None:
        # Pass an int — neither None nor str — falls through to ``return value``.
        coerced = ab._coerce_to_arrow_value(42, pa.date32())
        assert coerced == 42

    def test_time_with_unparseable_string_returns_original(self) -> None:
        coerced = ab._coerce_to_arrow_value("not-a-time", pa.time64("us"))
        assert coerced == "not-a-time"

    def test_time_with_non_string_returns_value(self) -> None:
        coerced = ab._coerce_to_arrow_value(123, pa.time64("us"))
        assert coerced == 123

    def test_unknown_arrow_type_returns_value_unchanged(self) -> None:
        # ``pa.null()`` doesn't match any dispatch branch — final fallback.
        coerced = ab._coerce_to_arrow_value("anything", pa.null())
        assert coerced == "anything"


# ---------------------------------------------------------------------------
# WKT parse failure — line 413.
# ---------------------------------------------------------------------------


class TestWktConversionFailure:
    """``_wkt_to_wkb_hex`` must raise a clean ``ValueError`` on bad input.

    The helper is called by the insertAll path when a GEOGRAPHY column
    sees a non-WKT string. We exercise two failure modes:

    1. DuckDB ``ST_GeomFromText`` raises on syntactically invalid input —
       the exception propagates (the helper doesn't swallow it).
    2. The DuckDB conversion returns NULL — the helper raises a clean
       ``ValueError`` with the offending WKT in the message (line 413).

    Mode 2 is genuinely defensive code; DuckDB usually raises before it
    can return NULL, so we monkeypatch the conversion connection to
    exercise the explicit-raise branch deterministically.
    """

    def test_invalid_wkt_string_propagates_duckdb_error(self) -> None:
        with pytest.raises((ValueError, Exception)) as excinfo:
            ab._wkt_to_wkb_hex("not a valid WKT string")
        assert excinfo.value is not None

    def test_wkt_conversion_returning_none_raises_value_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Defensive raise at line 413 when DuckDB returns NULL.

        We replace the conv-connection factory with one whose
        ``execute(...).fetchone()`` returns ``(None,)`` so the
        ``row[0] is None`` check fires.
        """

        class _NullConn:
            def execute(self, _sql: str, _params: list) -> _NullConn:
                return self

            def fetchone(self) -> tuple[None]:
                return (None,)

        def _factory() -> _NullConn:
            return _NullConn()

        monkeypatch.setattr(
            "bqemulator.types.geography._ensure_conv_conn",
            _factory,
        )
        with pytest.raises(ValueError, match="Cannot parse WKT"):
            ab._wkt_to_wkb_hex("placeholder")


class TestIsGeometryField:
    """Cover the metadata-loop continuation branch at line 211→210.

    A field with metadata that doesn't include ``ARROW:extension:name``
    must traverse the loop without returning True and end at line 213.
    """

    def test_non_geometry_metadata_returns_false(self) -> None:
        field = pa.field("x", pa.binary(), metadata={b"other_key": b"other_value"})
        assert ab._is_geometry_field(field) is False

    def test_geometry_metadata_returns_true(self) -> None:
        field = pa.field(
            "g",
            pa.binary(),
            metadata={b"ARROW:extension:name": b"geoarrow.wkb"},
        )
        assert ab._is_geometry_field(field) is True


# ---------------------------------------------------------------------------
# Interval parsing — covers lines 428, 430, 433 in _coerce_interval and
# 446-459 in _bq_interval_string_to_tuple.
# ---------------------------------------------------------------------------


class TestCoerceInterval:
    """Every branch in :func:`_coerce_interval`."""

    def test_three_tuple_passes_through(self) -> None:
        # Line 428 — pre-built ``(months, days, nanos)`` tuple is returned
        # verbatim.
        triple = (12, 5, 1_000_000_000)
        out = ab._coerce_interval(triple)
        assert out == triple

    def test_month_day_nano_duck_typed_object(self) -> None:
        """Line 430 — object with ``months`` / ``days`` / ``nanoseconds`` attrs."""

        class _Mock:
            months = 3
            days = 4
            nanoseconds = 5_000_000_000

        out = ab._coerce_interval(_Mock())
        assert out == (3, 4, 5_000_000_000)

    def test_unrecognised_value_returns_unchanged(self) -> None:
        # Line 433 — neither tuple, nor month-day-nano, nor string.
        sentinel = object()
        out = ab._coerce_interval(sentinel)
        assert out is sentinel


class TestBqIntervalStringToTuple:
    """Every dispatch branch in :func:`_bq_interval_string_to_tuple`."""

    def test_year_to_second_form(self) -> None:
        # ``Y-M D H:M:S[.ffffff]`` — full canonical form, line 445.
        months, days, nanos = ab._bq_interval_string_to_tuple("1-2 3 4:5:6.789")
        assert months == 12 + 2
        assert days == 3
        # 4h5m6.789s in nanoseconds
        expected_nanos = (
            4 * 3600 * 1_000_000_000 + 5 * 60 * 1_000_000_000 + int(6.789 * 1_000_000_000)
        )
        assert nanos == expected_nanos

    def test_day_to_second_form(self) -> None:
        # ``D H:M:S`` — line 446-447.
        months, days, nanos = ab._bq_interval_string_to_tuple("3 4:5:6")
        assert months == 0
        assert days == 3
        assert nanos == 4 * 3600 * 1_000_000_000 + 5 * 60 * 1_000_000_000 + 6 * 1_000_000_000

    def test_year_to_month_form(self) -> None:
        # ``Y-M`` — line 449-450.
        months, days, nanos = ab._bq_interval_string_to_tuple("2-3")
        assert months == 2 * 12 + 3
        assert days == 0
        assert nanos == 0

    def test_hour_to_second_form(self) -> None:
        # ``H:M:S`` (2 colons) — line 453-454.
        months, days, nanos = ab._bq_interval_string_to_tuple("1:2:3")
        assert months == 0
        assert days == 0
        assert nanos == 1 * 3600 * 1_000_000_000 + 2 * 60 * 1_000_000_000 + 3 * 1_000_000_000

    def test_hour_to_minute_form(self) -> None:
        # ``H:M`` (1 colon) — line 455-456.
        months, days, nanos = ab._bq_interval_string_to_tuple("1:30")
        assert months == 0
        assert days == 0
        assert nanos == 1 * 3600 * 1_000_000_000 + 30 * 60 * 1_000_000_000

    def test_day_shorthand_form(self) -> None:
        # Plain integer string — line 458-459 fallback to DAY.
        months, days, nanos = ab._bq_interval_string_to_tuple("7")
        assert months == 0
        assert days == 7
        assert nanos == 0


# ---------------------------------------------------------------------------
# Coverage-of-coverage check: hit the decimal path with an actual Decimal
# so the ``isinstance(value, Decimal)`` happy path is exercised in this
# audit file too — line 128/129 is already covered by the broader suite,
# but we keep the test here to prove the alternative branch lives next
# to its negative case.
# ---------------------------------------------------------------------------


class TestDecimalHappyPath:
    def test_decimal_value_preserves_precision(self) -> None:
        out = ab._format_bq_value(Decimal("12.500000000"), pa.decimal128(38, 9))
        assert isinstance(out, str)
        assert "12.5" in out
