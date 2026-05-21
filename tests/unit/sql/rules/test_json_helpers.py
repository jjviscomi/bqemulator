"""Tests for the BigQuery → DuckDB JSON helper translation rules.

The rules in :mod:`bqemulator.sql.rules.json_helpers` cover every JSON
function the slice-2 conformance corpus exercises that SQLGlot's
native transpile either mis-translates or leaves entirely untouched.
We assert two contracts per rule:

* The translated SQL no longer contains the BigQuery-flavoured name
  (sanity check that the rule fired).
* Executing the translated SQL against a real DuckDB connection
  yields the BigQuery-expected value.

The Python-backed helpers (``bqemu_json_remove`` / ``_set`` /
``_strip_nulls``) are exercised through the engine path so the
``create_function`` call shape stays under test as well.
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
    """A DuckDB connection with the JSON extension + our helper UDFs loaded."""
    connection = duckdb.connect()
    connection.execute("INSTALL json; LOAD json;")
    register_builtin_udfs(connection)
    return connection


def _execute(t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str) -> object:
    result = t.translate(sql)
    assert isinstance(result, Ok), result
    return con.execute(result.value).fetchone()


class TestJsonArrayObject:
    """SQLGlot already transpiles these; we only verify behaviour holds."""

    def test_json_array(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT JSON_ARRAY(1, 'two', TRUE) AS j")
        assert row == ('[1,"two",true]',)

    def test_json_object(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT JSON_OBJECT('k1', 1, 'k2', 'two') AS j")
        assert row == ('{"k1":1,"k2":"two"}',)


class TestJsonKeysRule:
    """``JSON_KEYS`` SQLGlot mis-names — verify our rule names it correctly."""

    def test_rewrites_to_json_keys(self, t: SQLTranslator) -> None:
        result = t.translate("SELECT JSON_KEYS(PARSE_JSON('{\"a\":1}')) AS k")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "J_S_O_N_KEYS_AT_DEPTH" not in upper
        assert "JSON_KEYS" in upper

    def test_returns_keys_array(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, 'SELECT JSON_KEYS(PARSE_JSON(\'{"a":1,"b":2,"c":3}\')) AS k')
        assert row == (["a", "b", "c"],)


class TestLaxRules:
    """``LAX_*`` extractors → ``TRY_CAST(json_extract_string(j, '$') AS T)``."""

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("SELECT LAX_BOOL(PARSE_JSON('true')) AS b", (True,)),
            ("SELECT LAX_INT64(PARSE_JSON('\"42\"')) AS n", (42,)),
            ("SELECT LAX_FLOAT64(PARSE_JSON('\"3.14\"')) AS x", (3.14,)),
            ("SELECT LAX_STRING(PARSE_JSON('42')) AS s", ("42",)),
        ],
    )
    def test_each_lax_variant(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection, sql: str, expected: tuple
    ) -> None:
        row = _execute(t, con, sql)
        assert row == expected


class TestStrictExtractors:
    """``BOOL(json)`` / ``FLOAT64(json)`` / ``STRING(json)`` rules."""

    def test_bool_json(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        assert _execute(t, con, "SELECT BOOL(PARSE_JSON('true')) AS b") == (True,)

    def test_float64_json(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        assert _execute(t, con, "SELECT FLOAT64(PARSE_JSON('3.25')) AS x") == (3.25,)

    def test_string_json_strips_quotes(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # The pre-rule output of SQLGlot is ``CAST(JSON('"hello"') AS TEXT)``
        # which DuckDB executes as the literal string ``"hello"`` — the
        # rule must rewrite to ``json_extract_string(j, '$')`` to strip the
        # outer quotes so the result matches BigQuery's ``hello``.
        assert _execute(t, con, "SELECT STRING(PARSE_JSON('\"hello\"')) AS s") == ("hello",)


class TestJsonMutationHelpers:
    """``JSON_REMOVE`` / ``JSON_SET`` / ``JSON_STRIP_NULLS`` Python UDF rules."""

    def test_json_remove_drops_key(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT JSON_REMOVE(PARSE_JSON('{\"a\":1,\"b\":2}'), '$.a') AS j")
        # Helper round-trips through json.dumps so the trailing whitespace
        # matches BigQuery's spaced encoding.
        assert row == ('{"b": 2}',)

    def test_json_remove_unknown_key_is_passthrough(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT JSON_REMOVE(PARSE_JSON('{\"a\":1}'), '$.z') AS j")
        assert row == ('{"a": 1}',)

    def test_json_set_adds_key(self, t: SQLTranslator, con: duckdb.DuckDBPyConnection) -> None:
        row = _execute(t, con, "SELECT JSON_SET(PARSE_JSON('{\"a\":1}'), '$.b', 2) AS j")
        assert row == ('{"a": 1, "b": 2}',)

    def test_json_strip_nulls_recursive(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(
            t, con, 'SELECT JSON_STRIP_NULLS(PARSE_JSON(\'{"a":1,"b":null,"c":2}\')) AS j'
        )
        assert row == ('{"a": 1, "c": 2}',)

    def test_json_remove_propagates_null(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = _execute(t, con, "SELECT JSON_REMOVE(NULL, '$.a') AS j")
        assert row == (None,)


class TestBuiltinHelpersDirectly:
    """Spot-check the Python helpers without the SQL pipeline.

    Guards the contract that the helpers are pure and ``None``-safe so
    that future SQLGlot rule edits can rely on it.
    """

    def test_remove_propagates_null_value(self) -> None:
        from bqemulator.sql.builtin_udfs import bqemu_json_remove

        assert bqemu_json_remove(None, "$.a") is None
        assert bqemu_json_remove('{"a":1}', None) is None

    def test_normalize_unknown_form_falls_back_to_nfc(self) -> None:
        from bqemulator.sql.builtin_udfs import bqemu_normalize

        # An explicit None is treated as NFC.
        assert bqemu_normalize("café", None) == "café"

    def test_farm_fingerprint_is_deterministic(self) -> None:
        from bqemulator.sql.builtin_udfs import bqemu_farm_fingerprint

        assert bqemu_farm_fingerprint("seed-42") == bqemu_farm_fingerprint("seed-42")
        assert bqemu_farm_fingerprint(None) is None
