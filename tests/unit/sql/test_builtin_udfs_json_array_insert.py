"""Tests for the JSON_ARRAY_INSERT helper UDF and translator rule.

``bqemu_json_array_insert`` mirrors BigQuery's ``JSON_ARRAY_INSERT``
contract for the JSONPath subset the conformance corpus exercises:
top-level ``$[N]`` array index, nested ``$.key[N]`` arrays, and
chained-key forms. Each entry is exercised both directly (pure-Python
contract) and through a live DuckDB connection wired by
:func:`bqemulator.sql.builtin_udfs.register_builtin_udfs` plus the
:class:`JSONArrayInsertRule` translator-rule integration.
"""

from __future__ import annotations

import json

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.builtin_udfs import (
    _parse_json_array_insert_path,
    bqemu_json_array_insert,
    register_builtin_udfs,
)
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    """Live DuckDB connection with the JSON extension + helper UDF."""
    connection = duckdb.connect(":memory:")
    connection.execute("INSTALL json; LOAD json;")
    register_builtin_udfs(connection)
    return connection


@pytest.fixture(scope="module")
def t() -> SQLTranslator:
    return SQLTranslator()


class TestPurePythonContract:
    """Direct calls against the Python implementation."""

    def test_insert_at_start_of_top_level_array(self) -> None:
        result = bqemu_json_array_insert("[1,2,3]", "$[0]", "99")
        assert json.loads(result) == [99, 1, 2, 3]

    def test_insert_at_end_clamps_when_out_of_bounds(self) -> None:
        # BigQuery clamps an out-of-bounds positive index to the end of
        # the array — the inserted element appears as the new last
        # element rather than raising or no-oping.
        result = bqemu_json_array_insert("[1,2,3]", "$[99]", "42")
        assert json.loads(result) == [1, 2, 3, 42]

    def test_insert_into_nested_array(self) -> None:
        result = bqemu_json_array_insert('{"a":[1,2,3]}', "$.a[1]", "99")
        assert json.loads(result) == {"a": [1, 99, 2, 3]}

    def test_chained_keys_navigate_through_nested_objects(self) -> None:
        result = bqemu_json_array_insert('{"a":{"b":[1,2]}}', "$.a.b[0]", '"x"')
        assert json.loads(result) == {"a": {"b": ["x", 1, 2]}}

    def test_path_does_not_reach_array_returns_input_unchanged(self) -> None:
        # ``$.a[0]`` resolves to the scalar ``1`` (not an array), so the
        # function is a no-op per BigQuery's documented behaviour.
        result = bqemu_json_array_insert('{"a":1}', "$.a[0]", "99")
        assert json.loads(result) == {"a": 1}

    def test_null_propagates(self) -> None:
        assert bqemu_json_array_insert(None, "$[0]", "1") is None
        assert bqemu_json_array_insert("[1]", None, "1") is None
        assert bqemu_json_array_insert("[1]", "$[0]", None) is None

    def test_malformed_json_returns_input(self) -> None:
        # A non-JSON input round-trips so the comparison helper surfaces
        # the malformed-input divergence directly rather than crashing
        # the engine.
        assert bqemu_json_array_insert("not json", "$[0]", "1") == "not json"


class TestPathParser:
    """Coverage for the ``$`` / ``.key`` / ``[N]`` JSONPath subset parser."""

    def test_root_only_path_yields_no_tokens(self) -> None:
        assert _parse_json_array_insert_path("$") == []

    def test_top_level_array_index(self) -> None:
        assert _parse_json_array_insert_path("$[3]") == [("index", 3)]

    def test_key_then_index(self) -> None:
        assert _parse_json_array_insert_path("$.a[1]") == [("key", "a"), ("index", 1)]

    def test_chained_keys(self) -> None:
        assert _parse_json_array_insert_path("$.a.b[0]") == [
            ("key", "a"),
            ("key", "b"),
            ("index", 0),
        ]

    def test_malformed_path_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported JSONPath syntax"):
            _parse_json_array_insert_path("no-dollar")


class TestEngineIntegration:
    """End-to-end translation + DuckDB execution covers the SQL surface."""

    def _exec(
        self,
        t: SQLTranslator,
        con: duckdb.DuckDBPyConnection,
        sql: str,
    ) -> tuple:
        translated = t.translate(sql)
        assert isinstance(translated, Ok), translated
        row = con.execute(translated.value).fetchone()
        assert row is not None
        return row

    def test_top_level_insert_returns_recorded_bq_shape(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = self._exec(
            t,
            con,
            "SELECT JSON_ARRAY_INSERT(JSON '[1,2,3]', '$[0]', 99) AS j",
        )
        # JSON content compared via ``json.loads`` so the spaced-vs-compact
        # output the schema renderer might use doesn't matter.
        assert json.loads(row[0]) == [99, 1, 2, 3]

    def test_nested_insert_returns_recorded_bq_shape(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        row = self._exec(
            t,
            con,
            "SELECT JSON_ARRAY_INSERT(JSON '{\"a\":[1,2,3]}', '$.a[1]', 99) AS j",
        )
        assert json.loads(row[0]) == {"a": [1, 99, 2, 3]}
