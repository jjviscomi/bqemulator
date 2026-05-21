"""Tests for the BigQuery → DuckDB string / bytes translation rules.

The rules in :mod:`bqemulator.sql.rules.string_helpers` and the
pre-transpile rewriter
:mod:`bqemulator.sql.rewriter.string_helpers` together cover the 13
``str_*`` / ``unicode_*`` fixtures in Bucket J's string sub-cluster.
Each rule is exercised against a real DuckDB connection so that
``decode``, ``string_split``, ``chr``, ``ord`` and the Python NORMALIZE
helpers all stay under regression coverage.
"""

from __future__ import annotations

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.builtin_udfs import register_builtin_udfs
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture
def t() -> SQLTranslator:
    return SQLTranslator()


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """A DuckDB connection with the JSON extension + our helper UDFs."""
    connection = duckdb.connect()
    connection.execute("INSTALL json; LOAD json;")
    register_builtin_udfs(connection)
    return connection


def _execute(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> object:
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    return con.execute(result.value).fetchone()


class TestOctetLengthRule:
    """``BYTE_LENGTH`` / ``OCTET_LENGTH`` → ``CASE TYPEOF ... STRLEN ...``."""

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("SELECT BYTE_LENGTH('abc') AS n", (3,)),
            ("SELECT BYTE_LENGTH('日本') AS n", (6,)),
            ("SELECT BYTE_LENGTH('😀') AS n", (4,)),
            ("SELECT OCTET_LENGTH('hello') AS n", (5,)),
            ("SELECT OCTET_LENGTH('日本') AS n", (6,)),
        ],
    )
    def test_byte_length_matches_bigquery(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str, expected: tuple
    ) -> None:
        assert _execute(t, con, sql) == expected

    def test_inside_sqlglot_length_dispatch_no_nest(self, t: SQLTranslator) -> None:
        # SQLGlot already wraps ``LENGTH(x)`` in a ``CASE TYPEOF`` that
        # picks ``OCTET_LENGTH`` for the ``BLOB`` branch. Our rule must
        # not re-wrap that inner call in another CASE — the operand is
        # already a CAST to BLOB.
        result = t.translate("SELECT LENGTH(NORMALIZE('é', NFD)) AS n")
        assert isinstance(result, Ok)
        # The SQLGlot-generated outer CASE is the only one we expect.
        assert result.value.upper().count("CASE") == 1


class TestCodePointsRules:
    """``CODE_POINTS_TO_STRING`` and ``TO_CODE_POINTS`` expansions."""

    def test_code_points_to_string_concatenates(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        assert _execute(t, con, "SELECT CODE_POINTS_TO_STRING([97, 98, 99]) AS s") == ("abc",)

    def test_to_code_points_handles_ascii(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        assert _execute(t, con, "SELECT TO_CODE_POINTS('AbC') AS pts") == ([65, 98, 67],)

    def test_to_code_points_handles_unicode(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # CJK characters round-trip with the same code-point values.
        assert _execute(t, con, "SELECT TO_CODE_POINTS('日本') AS pts") == ([26085, 26412],)


class TestSafeConvertBytesToString:
    """``SAFE_CONVERT_BYTES_TO_STRING(blob)`` → ``TRY(DECODE(blob))``."""

    def test_decodes_utf8(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT SAFE_CONVERT_BYTES_TO_STRING(FROM_BASE64('aGVsbG8=')) AS s")
        assert row == ("hello",)

    def test_bytes_literal_decodes(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(
            t,
            con,
            r"SELECT SAFE_CONVERT_BYTES_TO_STRING(b'\x68\x65\x6c\x6c\x6f') AS s",
        )
        assert row == ("hello",)


class TestNormalizeRewriter:
    """The pre-translator rewriter routes NORMALIZE → ``bqemu_normalize``."""

    def test_normalize_nfc_idempotent(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        assert _execute(t, con, "SELECT NORMALIZE('é', NFC) AS s") == ("é",)

    def test_normalize_nfd_doubles_length(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # NFD decomposes 'é' into 'e' + combining accent → 2 characters.
        assert _execute(t, con, "SELECT LENGTH(NORMALIZE('é', NFD)) AS n") == (2,)

    def test_normalize_and_casefold_lowers_sharp_s(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # Casefold expands sharp-s ('ß') to 'ss', lowercases everything.
        assert _execute(t, con, "SELECT NORMALIZE_AND_CASEFOLD('Straße', NFC) AS s") == ("strasse",)

    def test_normalize_default_form_is_nfc(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT NORMALIZE('é') AS s")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "BQEMU_NORMALIZE" in upper
        assert "'NFC'" in upper


class TestToBase32Rule:
    """``TO_BASE32(blob)`` → ``bqemu_to_base32(blob)``."""

    def test_hello_strips_padding(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # ``b'hello'`` is 5 bytes — input length multiple of 5 → no padding.
        assert _execute(t, con, "SELECT TO_BASE32(b'hello') AS x") == ("NBSWY3DP",)

    def test_empty_returns_empty(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        assert _execute(t, con, "SELECT TO_BASE32(b'') AS x") == ("",)

    def test_keeps_padding_for_non_multiple_of_five(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # ``b'foobar'`` is 6 bytes — not a multiple of 5 → keeps padding.
        row = _execute(t, con, "SELECT TO_BASE32(b'foobar') AS x")
        assert row == ("MZXW6YTBOI======",)

    def test_translation_emits_helper_name(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT TO_BASE32(b'hello') AS x")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "BQEMU_TO_BASE32" in upper
        assert "TO_BASE32(B" not in upper or "BQEMU_TO_BASE32" in upper


class TestFromBase32Rule:
    """``FROM_BASE32(string)`` → ``bqemu_from_base32(string)``."""

    def test_unpadded_decodes(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        # ``JBSWY3DP`` is exactly 8 chars — no padding needed.
        assert _execute(t, con, "SELECT FROM_BASE32('JBSWY3DP') AS x") == (b"Hello",)

    def test_padded_decodes(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT FROM_BASE32('JBSWY3DPEB3W64TMMQ======') AS x")
        assert row == (b"Hello world",)

    def test_translation_emits_helper_name(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT FROM_BASE32('JBSWY3DP') AS x")
        assert isinstance(result, Ok)
        assert "BQEMU_FROM_BASE32" in result.value.upper()


class TestCodePointsToBytesRule:
    """``CODE_POINTS_TO_BYTES(arr)`` → ``bqemu_code_points_to_bytes(arr)``."""

    def test_basic(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT TO_HEX(CODE_POINTS_TO_BYTES([65, 66, 67])) AS x")
        assert row == ("414243",)

    def test_empty(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(
            t,
            con,
            "SELECT TO_HEX(CODE_POINTS_TO_BYTES(CAST([] AS ARRAY<INT64>))) AS x",
        )
        assert row == ("",)

    def test_translation_emits_helper_name(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT CODE_POINTS_TO_BYTES([65]) AS x")
        assert isinstance(result, Ok)
        assert "BQEMU_CODE_POINTS_TO_BYTES" in result.value.upper()


class TestSoundexRule:
    """``SOUNDEX(s)`` → ``bqemu_soundex(s)``."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Robert", "R163"),
            ("Rupert", "R163"),
            ("Ashcraft", "A261"),
            ("Ashcroft", "A261"),
            ("Tymczak", "T522"),
            ("Pfister", "P236"),
            ("Honeyman", "H555"),
            ("Müller", "M460"),
        ],
    )
    def test_canonical_cases(
        self,
        t: SQLTranslator,
        con: duckdb.DuckDBPyConnection,
        name: str,
        expected: str,
    ) -> None:
        sql = f"SELECT SOUNDEX('{name}') AS x"
        assert _execute(t, con, sql) == (expected,)

    def test_translation_emits_helper_name(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT SOUNDEX('Robert') AS x")
        assert isinstance(result, Ok)
        assert "BQEMU_SOUNDEX" in result.value.upper()


class TestRegexpExtractNullifEmptyRule:
    """``REGEXP_EXTRACT(...)`` → ``NULLIF(REGEXP_EXTRACT(...), '')``."""

    def test_no_match_returns_null(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, r"SELECT REGEXP_EXTRACT('abcdef', '\d+') AS x")
        assert row == (None,)

    def test_match_returns_value(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, r"SELECT REGEXP_EXTRACT('abc123', '\d+') AS x")
        assert row == ("123",)

    def test_substr_alias_no_match_returns_null(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # ``REGEXP_SUBSTR`` is BigQuery's alias for ``REGEXP_EXTRACT`` —
        # SQLGlot maps both to the same typed node so the rule covers
        # the alias for free.
        row = _execute(t, con, r"SELECT REGEXP_SUBSTR('abcdef', '\d+') AS x")
        assert row == (None,)

    def test_emits_nullif_wrap(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT REGEXP_EXTRACT('abc', 'b') AS x")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "NULLIF(REGEXP_EXTRACT" in upper
        assert "''" in result.value or "''" in upper
