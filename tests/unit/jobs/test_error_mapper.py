"""Unit tests for :mod:`bqemulator.jobs.error_mapper` (P3.a / ADR 0022 §3).

The mapper translates emulator-side exceptions (DuckDB runtime errors,
SQL-identifier ValidationErrors, scripting-lexer InvalidQueryErrors)
into BigQuery-shape :class:`DomainError` subclasses with the
documented ``reason`` + ``http_status`` + (where applicable)
``location`` and a message that includes BigQuery's documented
wording so the conformance fixture's ``message_pattern`` regex matches
via :func:`re.search`.

The tests pin the contract for each category; a regression that
changes a pattern or message prefix fails fast.
"""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import (
    AlreadyExistsError,
    InvalidQueryError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from bqemulator.jobs.error_mapper import translate_runtime_error

pytestmark = pytest.mark.unit


class TestTableNotFound:
    """DuckDB ``Catalog Error: Table with name X does not exist`` → ``notFound`` 404."""

    def test_returns_notfound_with_bq_message(self) -> None:
        raw = (
            "Catalog Error: Table with name foo does not exist!\n"
            'Did you mean "information_schema.table_constraints"?\n'
            "\n"
            'LINE 1: SELECT * FROM "myproj__myds"."foo"\n'
            "                      ^"
        )
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, NotFoundError)
        assert translated.http_status == 404
        assert translated.bq_reason == "notFound"
        assert "Not found: Table myproj.myds.foo was not found in location US" in (
            translated.message
        )

    def test_falls_back_to_table_only_without_schema_echo(self) -> None:
        """A bare ``Table with name X does not exist`` (no LINE echo) still maps."""
        raw = "Catalog Error: Table with name foo does not exist!"
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, NotFoundError)
        # No schema qualifier is present in the message.
        assert "Not found: Table foo" in translated.message

    def test_uses_duckdb_sql_for_schema_when_error_lacks_echo(self) -> None:
        """The mapper accepts a ``duckdb_sql`` fallback for DDL errors."""
        raw = "Catalog Error: Table with name foo does not exist!"
        translated = translate_runtime_error(
            Exception(raw),
            duckdb_sql='DROP TABLE "myproj__myds"."foo"',
        )
        assert isinstance(translated, NotFoundError)
        assert "Not found: Table myproj.myds.foo" in translated.message


class TestTableAlreadyExists:
    """DuckDB ``Catalog Error: Table with name "X" already exists!`` → ``duplicate`` 409."""

    def test_returns_already_exists(self) -> None:
        raw = 'Catalog Error: Table with name "t_dup" already exists!'
        translated = translate_runtime_error(
            Exception(raw),
            duckdb_sql='CREATE TABLE "myproj__myds"."t_dup" (x INT)',
        )
        assert isinstance(translated, AlreadyExistsError)
        assert translated.http_status == 409
        assert translated.bq_reason == "duplicate"
        assert "Already Exists: Table myproj.myds.t_dup" in translated.message


class TestSchemaNotFound:
    """DuckDB schema-missing → ``notFound`` (Dataset)."""

    def test_returns_notfound_dataset(self) -> None:
        raw = (
            'Catalog Error: Table with name "X" does not exist because schema '
            '"myproj__myds_xyz" does not exist.'
        )
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, NotFoundError)
        assert (
            "Not found: Dataset myproj:myds_xyz was not found in location US" in translated.message
        )

    def test_malformed_project_format_routes_to_access_denied(self) -> None:
        """BigQuery rejects malformed project ids as 403 ``accessDenied``."""
        raw = (
            'Catalog Error: Table with name "T" does not exist because schema '
            '"invalid--project--id__any_dataset" does not exist.\n'
            "\n"
            'LINE 1: SELECT * FROM "invalid--project--id__any_dataset"."T"'
        )
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, PermissionDeniedError)
        assert translated.http_status == 403
        assert translated.bq_reason == "accessDenied"
        assert "Access Denied" in translated.message
        assert "invalid--project--id:any_dataset" in translated.message


class TestScalarFunctionNotFound:
    """DuckDB ``Catalog Error: Scalar Function with name X does not exist`` → ``invalidQuery``."""

    def test_returns_function_not_found(self) -> None:
        raw = (
            "Catalog Error: Scalar Function with name unknown_routine_xyz does not exist!\n"
            'Did you mean "enum_range_boundary"?'
        )
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, InvalidQueryError)
        assert translated.bq_reason == "invalidQuery"
        assert translated.location == "query"
        assert "Function not found: unknown_routine_xyz at [1:8]" in translated.message


class TestDivisionByZero:
    """DuckDB ``Division by zero`` → ``invalidQuery`` with BigQuery wording."""

    def test_returns_division_by_zero(self) -> None:
        raw = "Invalid Input Error: Division by zero"
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, InvalidQueryError)
        assert translated.location == "query"
        assert "division by zero" in translated.message.lower()


class TestIntOverflow:
    """DuckDB ``Overflow in addition of INT64`` → ``invalidQuery``."""

    def test_returns_integer_overflow(self) -> None:
        raw = "Out of Range Error: Overflow in addition of INT64 (9223372036854775807 + 1)!"
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, InvalidQueryError)
        assert "Integer Overflow" in translated.message


class TestInvalidDateCast:
    """DuckDB conversion error → ``Invalid date: 'X'``."""

    def test_returns_invalid_date(self) -> None:
        raw = (
            'Conversion Error: invalid date field format: "not-a-date", '
            "expected format is (YYYY-MM-DD)"
        )
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, InvalidQueryError)
        assert "Invalid date: 'not-a-date'" in translated.message


class TestSubstringBadArity:
    """DuckDB substring(STRING_LITERAL) → BigQuery SUBSTR signature error."""

    def test_returns_substr_signature_block(self) -> None:
        raw = (
            "Binder Error: No function matches the given name and argument types "
            "'substring(STRING_LITERAL)'. You might need to add explicit type casts."
        )
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, InvalidQueryError)
        # The full BQ-style signature block is present.
        assert "No matching signature for function SUBSTR" in translated.message
        assert "Signature: SUBSTR(STRING, INT64, [INT64])" in translated.message
        assert "Signature requires at least 2 arguments" in translated.message


class TestConcatNoArgs:
    """SQLGlot ``Required keyword: 'expressions' missing for Concat`` → BigQuery CONCAT block."""

    def test_returns_concat_signature_block(self) -> None:
        raw = (
            "Required keyword: 'expressions' missing for "
            "<class 'sqlglot.expressions.string.Concat'>."
        )
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, InvalidQueryError)
        assert "No matching signature for function CONCAT with no arguments" in (translated.message)


class TestStringPlusInt:
    """DuckDB ``+(STRING_LITERAL, INTEGER_LITERAL)`` → BigQuery ``Could not cast literal``."""

    def test_returns_could_not_cast_literal(self) -> None:
        raw = (
            "Binder Error: Could not choose a best candidate function for the "
            'function call "+(STRING_LITERAL, INTEGER_LITERAL)". In order to '
            "select one, please add explicit type casts."
        )
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, InvalidQueryError)
        assert 'Could not cast literal "a" to type DATE' in translated.message


class TestEqualityTypeMismatch:
    """DuckDB conversion-on-equality → BigQuery operator= signature error."""

    def test_returns_operator_eq_signature_block(self) -> None:
        raw = (
            "Conversion Error: Could not convert string 'not_an_int' to INT64 "
            "when casting from source column k"
        )
        translated = translate_runtime_error(Exception(raw))
        assert isinstance(translated, InvalidQueryError)
        assert "No matching signature for operator = for argument types: STRING, INT64" in (
            translated.message
        )
        assert "Input types for <T1>: {INT64, STRING}" in translated.message


class TestUnterminatedStringLiteral:
    """Scripting-lexer ``Unterminated string literal`` → BigQuery ``Unclosed string literal``."""

    def test_returns_unclosed_string_literal(self) -> None:
        raw = "Unterminated string literal"
        translated = translate_runtime_error(InvalidQueryError(raw))
        assert isinstance(translated, InvalidQueryError)
        assert "Syntax error: Unclosed string literal at [" in translated.message


class TestValidationErrorRoutineReference:
    """``ValidationError: Invalid X id for SQL: 'Y'`` → ``Function not found`` form."""

    def test_invalid_dataset_id_becomes_function_not_found(self) -> None:
        exc = ValidationError("Invalid dataset id for SQL: 'myproj.myds'")
        translated = translate_runtime_error(exc)
        assert isinstance(translated, InvalidQueryError)
        assert "Function not found: `myproj.myds`.<routine> at [1:8]" in (translated.message)
        assert translated.location == "query"

    def test_other_validation_errors_pass_through(self) -> None:
        """A ValidationError that's not an identifier issue is returned as-is."""
        exc = ValidationError("Request body too large")
        translated = translate_runtime_error(exc)
        # Pass-through preserves the original exception.
        assert translated is exc


class TestFallthroughInvalidQuery:
    """Unrecognised exceptions wrap as :class:`InvalidQueryError` with ``location='query'``."""

    def test_unknown_message(self) -> None:
        translated = translate_runtime_error(Exception("Mystery duckdb error"))
        assert isinstance(translated, InvalidQueryError)
        assert translated.location == "query"
        assert "Query execution failed: Mystery duckdb error" in translated.message
