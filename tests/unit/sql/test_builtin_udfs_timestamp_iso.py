"""Unit coverage for the ISO timestamp format / parse helpers in builtin_udfs.

The ``bqemu_format_timestamp_iso`` and ``bqemu_parse_timestamp_iso``
helpers carry non-trivial zone handling (``%Ez`` colon insertion,
``%Z`` IANA-zone validation) that's hard to reach via SQL-level tests
because DuckDB rewrites the strftime call before reaching the helper.
Drive them directly to pin the behaviour.
"""

from __future__ import annotations

import datetime as _datetime

import pytest

from bqemulator.sql.builtin_udfs import (
    bqemu_farm_fingerprint,
    bqemu_format_timestamp_iso,
    bqemu_parse_timestamp_iso,
)

pytestmark = pytest.mark.unit


class TestFormatTimestampIso:
    """``%Ez`` colon-insertion + zone application."""

    def test_format_with_named_zone(self) -> None:
        ts = _datetime.datetime(2026, 5, 21, 16, 0, 0, tzinfo=_datetime.UTC)
        rendered = bqemu_format_timestamp_iso("%Y-%m-%dT%H:%M:%S%Ez", ts, "America/New_York")
        # NY is UTC-4 in May (EDT). Expect "-04:00" with the colon (the %Ez fix).
        assert rendered == "2026-05-21T12:00:00-04:00"

    def test_format_default_zone_is_utc(self) -> None:
        ts = _datetime.datetime(2026, 5, 21, 16, 0, 0, tzinfo=_datetime.UTC)
        rendered = bqemu_format_timestamp_iso("%Y-%m-%dT%H:%M:%S%Ez", ts, None)
        assert rendered == "2026-05-21T16:00:00+00:00"

    def test_format_with_naive_datetime_treats_as_utc(self) -> None:
        ts = _datetime.datetime(2026, 5, 21, 16, 0, 0)  # noqa: DTZ001 — deliberate naive datetime test input
        rendered = bqemu_format_timestamp_iso("%Y-%m-%dT%H:%M:%S", ts, "UTC")
        assert rendered == "2026-05-21T16:00:00"

    def test_format_rejects_bad_zone(self) -> None:
        ts = _datetime.datetime(2026, 5, 21, 16, 0, 0, tzinfo=_datetime.UTC)
        with pytest.raises(ValueError, match="Invalid time zone"):
            bqemu_format_timestamp_iso("%Y-%m-%d", ts, "Not/A/Zone")

    def test_format_passes_through_none(self) -> None:
        assert bqemu_format_timestamp_iso(None, None, "UTC") is None
        ts = _datetime.datetime(2026, 5, 21, tzinfo=_datetime.UTC)
        assert bqemu_format_timestamp_iso(None, ts, "UTC") is None


class TestParseTimestampIso:
    """``%Z`` IANA-zone parsing + colon-tolerant offset parsing."""

    def test_parse_with_named_zone(self) -> None:
        # %Z parses a literal IANA token at the matched position.
        dt = bqemu_parse_timestamp_iso(
            "%Y-%m-%dT%H:%M:%S %Z",
            "2026-05-21T16:00:00 America/New_York",
        )
        # Output is naive UTC; 16:00 EDT = 20:00 UTC.
        assert dt == _datetime.datetime(2026, 5, 21, 20, 0, 0)  # noqa: DTZ001

    def test_parse_with_iso_offset_accepts_colon(self) -> None:
        dt = bqemu_parse_timestamp_iso("%Y-%m-%dT%H:%M:%S%Ez", "2026-05-21T16:00:00-04:00")
        # Naive UTC: 16:00-04:00 == 20:00 UTC.
        assert dt == _datetime.datetime(2026, 5, 21, 20, 0, 0)  # noqa: DTZ001

    def test_parse_rejects_unknown_zone(self) -> None:
        with pytest.raises(ValueError, match="Invalid time zone"):
            bqemu_parse_timestamp_iso("%Y-%m-%dT%H:%M:%S %Z", "2026-05-21T16:00:00 NOT_A_ZONE")

    def test_parse_rejects_mismatched_prefix(self) -> None:
        with pytest.raises(ValueError, match="Failed to parse"):
            # The %Z position requires the literal prefix to match; a
            # missing space prefix causes the prefix-len helper to
            # return None.
            bqemu_parse_timestamp_iso("%Y-%m-%dT%H:%M:%S %Z", "Xnot-a-prefix")

    def test_parse_rejects_invalid_iso(self) -> None:
        with pytest.raises(ValueError, match="Failed to parse"):
            bqemu_parse_timestamp_iso("%Y-%m-%dT%H:%M:%S", "not-a-timestamp")

    def test_parse_passes_through_none(self) -> None:
        assert bqemu_parse_timestamp_iso(None, "anything") is None
        assert bqemu_parse_timestamp_iso("%Y-%m-%d", None) is None


class TestFarmFingerprintLengthBranches:
    """Cover each FarmHash internal-length branch.

    The FarmHash port dispatches based on input byte length: ≤16, 17—32,
    33—64, 65+. Exact hash values aren't asserted (only that the result
    is a deterministic int) — stability across runs is the contract.
    """

    def test_empty_input_returns_constant(self) -> None:
        # length=0 → returns FH_K2 (the empty-input fingerprint).
        assert bqemu_farm_fingerprint("") == bqemu_farm_fingerprint("")

    def test_1_byte_input(self) -> None:
        # length=1 → the ``length > 0`` micro-branch in ``_fh_hash_len_0_to_16``.
        h = bqemu_farm_fingerprint("a")
        assert isinstance(h, int)

    def test_4_byte_input(self) -> None:
        # length=4 → the ``length >= 4`` branch in ``_fh_hash_len_0_to_16``.
        h = bqemu_farm_fingerprint("abcd")
        assert isinstance(h, int)

    def test_8_byte_input(self) -> None:
        # length=8 → the ``length >= 8`` branch in ``_fh_hash_len_0_to_16``.
        h = bqemu_farm_fingerprint("abcdefgh")
        assert isinstance(h, int)

    def test_16_byte_boundary(self) -> None:
        # length=16 → top of ``_fh_hash_len_0_to_16`` range.
        h = bqemu_farm_fingerprint("a" * 16)
        assert isinstance(h, int)

    def test_24_byte_input(self) -> None:
        # length=24 → ``_fh_hash_len_17_to_32`` branch.
        h = bqemu_farm_fingerprint("a" * 24)
        assert isinstance(h, int)

    def test_50_byte_input_is_stable(self) -> None:
        # length=50 → ``_fh_hash_len_33_to_64`` branch. Stability check.
        payload = "x" * 50
        assert bqemu_farm_fingerprint(payload) == bqemu_farm_fingerprint(payload)

    def test_33_byte_boundary(self) -> None:
        h = bqemu_farm_fingerprint("a" * 33)
        assert isinstance(h, int)

    def test_64_byte_boundary(self) -> None:
        h = bqemu_farm_fingerprint("a" * 64)
        assert isinstance(h, int)

    def test_65_byte_long_input(self) -> None:
        # length=65 → the ``> 64`` general-purpose branch.
        h = bqemu_farm_fingerprint("a" * 65)
        assert isinstance(h, int)

    def test_128_byte_long_input(self) -> None:
        h = bqemu_farm_fingerprint("a" * 128)
        assert isinstance(h, int)

    def test_different_payloads_differ(self) -> None:
        assert bqemu_farm_fingerprint("x" * 50) != bqemu_farm_fingerprint("y" * 50)

    def test_passes_through_none(self) -> None:
        assert bqemu_farm_fingerprint(None) is None
