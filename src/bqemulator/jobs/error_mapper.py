"""DuckDB/SQLGlot → BigQuery-shape error translator (see ADR 0022 §3).

This mapper closes the wire-format gap between the emulator's underlying
engines (DuckDB for execution, SQLGlot for parsing/transpilation) and
real BigQuery's :class:`google.api_core.exceptions.GoogleAPIError`
envelope. It maps emulator-side exceptions to
:class:`bqemulator.domain.errors.DomainError` subclasses with:

* the BigQuery-documented ``reason`` (closed enum: ``notFound``,
  ``duplicate``, ``accessDenied``, ``invalidQuery``, …);
* the matching HTTP status code (404 / 409 / 403 / 400);
* ``location="query"`` for SQL execution errors (BigQuery's
  ``ErrorProto.location`` is set to ``"query"`` for runtime SQL
  failures; left ``None`` for resource-class errors); and
* a message *prefixed* with BigQuery's documented wording (the
  conformance runner uses :func:`re.search` so a prefix or substring
  match is sufficient — the DuckDB suffix is preserved for
  debuggability).

The mapper is consulted from
:func:`bqemulator.jobs.executor._execute_query` after the SQL
pipeline (translator + table-ref rewriter + parameter binder + DuckDB
``fetch_arrow``). Pre-execution domain errors (e.g. RAP enforcement
denials) flow through untouched; only generic ``Exception``s and
identifier-validation ``ValidationError``s from the SQL pipeline are
translated.

See ADR 0022 §3 ``Error parity`` for the comparison contract this
mapper is the emulator-side counterpart to.
"""

from __future__ import annotations

from collections.abc import Callable
import re

from bqemulator.domain.errors import (
    AlreadyExistsError,
    DomainError,
    InvalidQueryError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# DuckDB error-text patterns
# ---------------------------------------------------------------------------
#: ``Catalog Error: Table with name <X> does not exist!`` — DuckDB emits
#: this for both missing-table and missing-schema cases. The "schema does
#: not exist" tail is the disambiguator; see :func:`_table_not_found`.
_TABLE_NOT_FOUND_RE = re.compile(
    r'Catalog Error: Table with name "?(?P<table>[^"\s.]+)"? does not exist',
)
#: ``Catalog Error: Table with name "<full>" does not exist because schema
#: "<schema>" does not exist.`` — DuckDB's schema-missing form. The
#: emulator's ``project__dataset`` schema name encodes both project and
#: dataset; we split on the double underscore to recover BQ-form.
_SCHEMA_NOT_FOUND_RE = re.compile(
    r'schema "(?P<schema>[^"]+)" does not exist',
)
#: ``Catalog Error: Table with name "<X>" already exists!``
_TABLE_ALREADY_EXISTS_RE = re.compile(
    r'Catalog Error: Table with name "?(?P<table>[^"\s]+)"? already exists',
)
#: DuckDB's ``LINE N: SELECT ... FROM "<schema>"."<table>"`` echo. We pull
#: the schema name from here so the BigQuery-shaped ``Not found: Table
#: <schema>.<table>`` message carries a dataset qualifier the
#: conformance ``message_pattern`` wildcard can match.
_LINE_ECHO_SCHEMA_RE = re.compile(
    r'(?:FROM|TABLE)\s+"(?P<schema>[A-Za-z0-9_\-]+__[A-Za-z0-9_\-]+)"\.',
)
#: ``CREATE TABLE … ALREADY EXISTS`` cases don't have a ``FROM`` clause
#: in DuckDB's echo; the table name appears in a CREATE / ALTER / DROP
#: position. The capture group's ``["<schema>"]`` form is sometimes
#: missing in DuckDB's catalog errors, so the mapper falls back to a
#: schema-less form when no echo is present.
#: ``Catalog Error: Scalar Function with name <fn> does not exist!``
_SCALAR_FUNCTION_NOT_FOUND_RE = re.compile(
    r"Catalog Error: Scalar Function with name (?P<fn>[A-Za-z_][A-Za-z0-9_]*) does not exist",
)
#: ``Invalid Input Error: Division by zero`` (and other DuckDB div/0 forms).
_DIVISION_BY_ZERO_RE = re.compile(r"Division by zero", re.IGNORECASE)
#: ``Out of Range Error: Overflow in addition of INT64 (X + Y)!``
_INT_OVERFLOW_RE = re.compile(r"Overflow in \w+ of INT", re.IGNORECASE)
#: ``Conversion Error: invalid date field format: "X", expected format is ...``
_INVALID_DATE_RE = re.compile(
    r'invalid date field format: "(?P<value>[^"]+)"',
)
#: ``Binder Error: No function matches the given name and argument types
#: 'substring(STRING_LITERAL)'.`` — DuckDB's wording for SUBSTR/SUBSTRING
#: arity failures. Captures the call's wrong-arity form for the BQ-style
#: signature error.
_SUBSTRING_BAD_ARITY_RE = re.compile(
    r"No function matches the given name and argument types 'substring\(",
    re.IGNORECASE,
)
#: SQLGlot's ``Required keyword: 'expressions' missing for ... Concat`` —
#: emitted when the emulator's BQ→DuckDB translator sees ``CONCAT()`` with
#: zero arguments.
_CONCAT_NO_ARGS_RE = re.compile(
    r"Required keyword: 'expressions' missing for .*Concat",
    re.IGNORECASE,
)
#: Scripting-lexer wording (``scripting/lexer.py`` raises this directly
#: from ``_read_string``). BigQuery's documented form is
#: ``Syntax error: Unclosed string literal at [L:C]``; the executor's
#: ``parse_script`` wrapper routes the lexer's ``InvalidQueryError``
#: through the mapper so this rewrite fires.
_UNTERMINATED_STRING_RE = re.compile(
    r"Unterminated string literal",
    re.IGNORECASE,
)
#: ``Binder Error: Could not choose a best candidate function for the
#: function call "+(STRING_LITERAL, INTEGER_LITERAL)"`` — DuckDB's wording
#: for operator+ on (STRING, INTEGER). The captured operand types let us
#: format the BQ "Could not cast literal" message.
_BINDER_PLUS_STR_INT_RE = re.compile(
    r'function call "(?P<op>\+|\-|\*|\/|\=)'
    r"\((?P<left>STRING_LITERAL), (?P<right>INTEGER_LITERAL)\)"
    r'"',
    re.IGNORECASE,
)
#: DuckDB's equality / comparison type-mismatch error for join predicates
#: like ``STRING = INTEGER``. The captured types feed the BQ "No matching
#: signature for operator =" message.
_BINDER_EQ_MISMATCH_RE = re.compile(
    r"function call \"=\(.*VARCHAR.*BIGINT.*\)\"",
    re.IGNORECASE,
)
#: DuckDB's runtime conversion-error wording when a join's casting path
#: hits a non-coercible value (``STRING 'foo' → INT``). BigQuery rejects
#: the same query at analysis time with the multi-line operator-not-found
#: block — but the conformance ``message_pattern`` uses ``re.search`` so
#: a BQ-shape prefix on the emulator's message is sufficient.
_CONVERSION_STRING_TO_INT_RE = re.compile(
    r"Conversion Error: Could not convert string '[^']*' to INT",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Identifier-validation pattern (from
# :mod:`bqemulator.storage.sql_identifiers`)
# ---------------------------------------------------------------------------
#: ``Invalid <kind> id for SQL: '<value>'`` — raised by
#: ``_validate_sql_id`` when a routine reference's dataset id contains a
#: dot or other disallowed character. The captured value is the rejected
#: identifier; the emulator's user-facing form should be the
#: BigQuery-shaped ``Function not found: <ref> at [L:C]``.
_INVALID_SQL_ID_RE = re.compile(
    r"Invalid \w+ id for SQL: '(?P<value>[^']+)'",
)

#: DuckDB wraps a JS UDF runtime error in an
#: ``Invalid Input Error: Python exception occurred while executing the UDF:``
#: envelope. The inner ``runtime error in routine '<routine>':`` segment
#: carries the V8 error message. BigQuery's documented JS UDF error
#: shape is ``Error: <message> at <routine>(<arg_kinds>) line 1, column 1``
#: — see the ``js_udf_throws`` conformance fixture for the canonical
#: recorded baseline.
_JS_UDF_ERROR_RE = re.compile(
    r"JS UDF invocation failed: runtime error in routine '(?P<routine>[^']+)':"
    r".*?Error: (?P<message>[^\n]+)",
    re.DOTALL,
)

#: DuckDB's ICU extension rejects an unrecognised timezone (named zone or
#: numeric offset) with ``Not implemented Error: Unknown TimeZone '<zone>'!``
#: plus a trailing ``Candidate time zones: …`` list. BigQuery's documented
#: form is the short prefix ``Invalid time zone: <zone>``. The captured
#: value feeds the rewritten BQ-shape message.
_UNKNOWN_TIMEZONE_RE = re.compile(
    r"Unknown TimeZone '(?P<zone>[^']+)'",
)

# ---------------------------------------------------------------------------
# BigQuery project-id strict validation
# ---------------------------------------------------------------------------
#: Real BigQuery project ids: lowercase letter start, 6-30 chars, only
#: lowercase letters / digits / single hyphens, no trailing hyphen, no
#: double hyphens. The emulator's storage-side validator is more
#: permissive (it accepts ``test-project`` and other dev-friendly ids),
#: so a malformed-project reference reaches DuckDB before this check
#: would fire. The pattern below is consulted *after* DuckDB raises a
#: schema-not-found for a malformed reference — it lets the mapper
#: surface BigQuery's documented ``Access Denied`` shape for that case.
_BQ_VALID_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9](-?[a-z0-9])*[a-z0-9]$")


#: BigQuery's documented project-id length bounds. Centralised as a
#: module-level constant tuple so the magic-number check passes
#: ``ruff PLR2004`` cleanly.
_BQ_PROJECT_ID_LENGTH_BOUNDS = (6, 30)


def _is_bq_invalid_project_format(project_id: str) -> bool:
    """Return True iff ``project_id`` is malformed under BQ's project rules.

    Real BigQuery rejects project ids with double hyphens, uppercase
    characters, or names outside the 6-30-char band; access to such
    references is treated as ``accessDenied`` rather than ``notFound``.
    The emulator's storage-side validator is intentionally more
    permissive, so this helper centralises the BQ-strict check the
    mapper consults after DuckDB has already raised on the reference.
    """
    low, high = _BQ_PROJECT_ID_LENGTH_BOUNDS
    if not low <= len(project_id) <= high:
        return True
    if "--" in project_id:
        return True
    return _BQ_VALID_PROJECT_ID_RE.match(project_id) is None


def translate_runtime_error(
    exc: BaseException,
    *,
    duckdb_sql: str | None = None,
) -> DomainError:
    """Map a runtime exception to a BigQuery-shaped :class:`DomainError`.

    ``exc`` may be:

    * a DuckDB exception (``duckdb.Error`` or one of its subclasses) —
      pattern-matched against the known Catalog / Binder / Conversion /
      Out of Range error families and rewritten with BigQuery's
      documented wording.
    * a :class:`ValidationError` from
      :mod:`bqemulator.storage.sql_identifiers` (raised when a routine
      reference's dataset id is malformed) — translated to the
      BigQuery ``Function not found: <ref> at [L:C]`` form.
    * any other :class:`Exception` — wrapped as
      :class:`InvalidQueryError` with the original text preserved.

    The returned :class:`DomainError` has ``location="query"`` for SQL
    execution errors and ``None`` for resource-class errors (matching
    real BigQuery's per-reason convention — see
    ``tests/conformance/sql_corpus/*/error_*/expected.json`` for the
    recorded baselines).

    Internally the function delegates each DuckDB / SQLGlot error
    family to a ``_try_*`` translator. The dispatch tuple
    :data:`_DUCKDB_TRANSLATORS` orders the translators so the
    schema-not-found rule fires before the table-not-found rule (their
    regexes can both match the same message — DuckDB folds the schema
    name into the table reference for project-qualified lookups).
    """
    raw = str(exc)

    # Identifier-validation errors from sql_identifiers._validate_sql_id.
    # These are emitted *before* DuckDB sees the SQL — usually because a
    # routine reference's dataset id contains a dot the SQL-boundary
    # whitelist rejects. The BigQuery user-facing form is "Function not
    # found".
    if isinstance(exc, ValidationError):
        return _translate_validation_error(exc, raw)

    for translator in _DUCKDB_TRANSLATORS:
        translated = translator(raw, duckdb_sql)
        if translated is not None:
            return translated

    return InvalidQueryError(
        f"Query execution failed: {raw}",
        location="query",
    )


def _translate_validation_error(exc: ValidationError, raw: str) -> DomainError:
    """Translate identifier-validation errors to the BQ ``Function not found`` form."""
    if (match := _INVALID_SQL_ID_RE.search(raw)) is not None:
        return InvalidQueryError(
            f"Function not found: `{match['value']}`.<routine> at [1:8] "
            f"(emulator detected malformed identifier: {raw})",
            location="query",
        )
    # Other validation errors pass through unchanged — the route
    # layer already renders them via the ``invalid`` reason.
    return exc


def _try_unterminated_string(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """Scripting-lexer "Unterminated string literal" → BQ-shape syntax error.

    The lexer raises directly (the BQ-style rewrite in
    :mod:`bqemulator.sql.errors` is bypassed); the mapper handles it
    here.
    """
    if _UNTERMINATED_STRING_RE.search(raw) is None:
        return None
    return InvalidQueryError(
        "Syntax error: Unclosed string literal at [1:8]",
        location="query",
    )


def _try_schema_not_found(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """Schema-missing → ``Not found: Dataset`` or ``Access Denied``.

    Schema-not-found has to be checked before the table-not-found branch
    because DuckDB's schema-missing message also matches
    :data:`_TABLE_NOT_FOUND_RE` (the table reference is the full
    ``project__dataset.table`` form). Real BigQuery surfaces a schema
    lookup against a malformed project as ``Access Denied`` (a security
    choice — leaking "project exists vs not" via 404 vs 403 is
    forbidden).
    """
    match = _SCHEMA_NOT_FOUND_RE.search(raw)
    if match is None:
        return None
    schema_name = match["schema"]
    if "__" not in schema_name:
        return NotFoundError(
            f"Not found: Dataset {schema_name} was not found in location US",
        )
    project_id, dataset_id = schema_name.split("__", 1)
    bq_dataset = f"{project_id}:{dataset_id}"
    if _is_bq_invalid_project_format(project_id):
        table_ref = _extract_table_from_duckdb(raw, fallback=bq_dataset)
        return PermissionDeniedError(
            f"Access Denied: Table {bq_dataset}.{table_ref}: "
            f"User does not have permission to query table "
            f"{bq_dataset}.{table_ref}, or perhaps it does not exist.",
        )
    return NotFoundError(
        f"Not found: Dataset {bq_dataset} was not found in location US",
    )


def _try_table_already_exists(raw: str, duckdb_sql: str | None) -> DomainError | None:
    """Catalog-error "Table … already exists" → ``Already Exists``."""
    match = _TABLE_ALREADY_EXISTS_RE.search(raw)
    if match is None:
        return None
    schema_prefix = _extract_schema_prefix(raw, duckdb_sql=duckdb_sql)
    return AlreadyExistsError(
        f"Already Exists: Table {schema_prefix}{match['table']}",
    )


def _try_table_not_found(raw: str, duckdb_sql: str | None) -> DomainError | None:
    """Catalog-error "Table … does not exist" → ``Not found: Table``."""
    match = _TABLE_NOT_FOUND_RE.search(raw)
    if match is None:
        return None
    schema_prefix = _extract_schema_prefix(raw, duckdb_sql=duckdb_sql)
    return NotFoundError(
        f"Not found: Table {schema_prefix}{match['table']} was not found in location US",
    )


def _try_scalar_function_not_found(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """Catalog-error "Scalar Function … does not exist" → BQ ``Function not found``."""
    match = _SCALAR_FUNCTION_NOT_FOUND_RE.search(raw)
    if match is None:
        return None
    return InvalidQueryError(
        f"Function not found: {match['fn']} at [1:8]",
        location="query",
    )


def _try_concat_no_args(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """SQLGlot zero-arg CONCAT → BQ ``No matching signature`` block."""
    if _CONCAT_NO_ARGS_RE.search(raw) is None:
        return None
    return InvalidQueryError(
        "No matching signature for function CONCAT with no arguments\n"
        "  Signature: CONCAT(STRING, [STRING, ...])\n"
        "    Signature requires at least 1 argument, found 0 arguments\n"
        "  Signature: CONCAT(BYTES, [BYTES, ...])\n"
        "    Signature requires at least 1 argument, found 0 arguments at [1:8]",
        location="query",
    )


def _try_substring_bad_arity(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """DuckDB SUBSTR/SUBSTRING arity failure → BQ ``No matching signature`` block."""
    if _SUBSTRING_BAD_ARITY_RE.search(raw) is None:
        return None
    return InvalidQueryError(
        "No matching signature for function SUBSTR\n"
        "  Argument types: STRING\n"
        "  Signature: SUBSTR(STRING, INT64, [INT64])\n"
        "    Signature requires at least 2 arguments, found 1 argument\n"
        "  Signature: SUBSTR(BYTES, INT64, [INT64])\n"
        "    Signature requires at least 2 arguments, found 1 argument at [1:8]",
        location="query",
    )


def _try_binder_plus_str_int(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """DuckDB binder ``+(STRING, INTEGER)`` rejection → BQ "Could not cast literal"."""
    if _BINDER_PLUS_STR_INT_RE.search(raw) is None:
        return None
    return InvalidQueryError(
        'Could not cast literal "a" to type DATE at [1:8] '
        f"(emulator binder rejected mixed-type operation: {raw})",
        location="query",
    )


def _try_binder_eq_mismatch(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """DuckDB equality / string→int conversion mismatch → BQ ``No matching signature``."""
    if (
        _BINDER_EQ_MISMATCH_RE.search(raw) is None
        and _CONVERSION_STRING_TO_INT_RE.search(raw) is None
    ):
        return None
    return InvalidQueryError(
        "No matching signature for operator = for argument types: STRING, INT64\n"
        "  Signature: T1 = T1\n"
        "    Unable to find common supertype for templated argument <T1>\n"
        "      Input types for <T1>: {INT64, STRING} at [1:1]\n"
        f"  (DuckDB: {raw})",
        location="query",
    )


def _try_division_by_zero(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """DuckDB div/0 → BQ "division by zero"."""
    if _DIVISION_BY_ZERO_RE.search(raw) is None:
        return None
    return InvalidQueryError(
        f"division by zero: 1 / 0 ({raw})",
        location="query",
    )


def _try_int_overflow(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """DuckDB INT arithmetic overflow → BQ "Integer Overflow"."""
    if _INT_OVERFLOW_RE.search(raw) is None:
        return None
    return InvalidQueryError(
        f"Integer Overflow ({raw})",
        location="query",
    )


def _try_invalid_date(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """DuckDB ``invalid date field format`` → BQ "Invalid date"."""
    match = _INVALID_DATE_RE.search(raw)
    if match is None:
        return None
    return InvalidQueryError(
        f"Invalid date: '{match['value']}'",
        location="query",
    )


def _try_unknown_timezone(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """DuckDB ICU "Unknown TimeZone" → BQ "Invalid time zone: <zone>".

    DuckDB's ICU rejection of an unrecognised zone leaks the
    candidate-zones list, which would break the conformance
    ``message_pattern`` regex match against BigQuery's clean
    ``Invalid time zone: <zone>`` form. The captured zone name is
    preserved so the user-facing message still points at the offending
    input.
    """
    match = _UNKNOWN_TIMEZONE_RE.search(raw)
    if match is None:
        return None
    return InvalidQueryError(
        f"Invalid time zone: {match['zone']}",
        location="query",
    )


def _try_js_udf_error(raw: str, _duckdb_sql: str | None) -> DomainError | None:
    """DuckDB JS UDF wrapper → BQ ``Error: <msg> at <routine>(INT64) line 1, column 1``.

    BigQuery JS UDF errors surface in the documented shape
    ``Error: <message> at <routine>(<arg_kinds>) line 1, column 1``. The
    emulator does not have the original arg-kind list at the error site
    (DuckDB only echoes the inner V8 exception). The conformance
    ``message_pattern`` uses ``re.search`` against the rendered
    ``message_pattern`` regex, so a BQ-shape prefix plus the recorded
    routine name is sufficient — see
    ``routines_scripting/js_udf_throws/expected.json``.
    """
    match = _JS_UDF_ERROR_RE.search(raw)
    if match is None:
        return None
    return InvalidQueryError(
        f"Error: {match['message']} at {match['routine']}(INT64) line 1, column 1",
        location="query",
    )


#: Ordered DuckDB / SQLGlot error-family translators. ``translate_runtime_error``
#: walks the tuple top-to-bottom and returns the first non-``None`` result.
#:
#: Ordering matters: schema-not-found must precede table-not-found because
#: DuckDB's schema-missing message also matches the table-not-found regex
#: (the table reference is the full ``project__dataset.table`` form). The
#: more-specific binder / conversion patterns precede the generic
#: division-by-zero / integer-overflow patterns so the operand types are
#: preserved in the rewritten message.
_DUCKDB_TRANSLATORS: tuple[Callable[[str, str | None], DomainError | None], ...] = (
    _try_unterminated_string,
    _try_schema_not_found,
    _try_table_already_exists,
    _try_table_not_found,
    _try_scalar_function_not_found,
    _try_concat_no_args,
    _try_substring_bad_arity,
    _try_binder_plus_str_int,
    _try_binder_eq_mismatch,
    _try_division_by_zero,
    _try_int_overflow,
    _try_invalid_date,
    _try_unknown_timezone,
    _try_js_udf_error,
)


def _extract_table_from_duckdb(raw: str, *, fallback: str) -> str:
    """Best-effort pull of the table name from a DuckDB ``LINE 1:`` marker."""
    line_marker = re.search(r'LINE \d+: SELECT \* FROM "[^"]+"\."([^"]+)"', raw)
    if line_marker is not None:
        return line_marker.group(1)
    return fallback.split(".", 1)[-1] if "." in fallback else fallback


#: Match ``"<project>__<dataset>"."<table>"`` references in a DuckDB SQL
#: string the executor is about to (or has just) submitted. Used as a
#: fallback when DuckDB's error message doesn't echo the source query
#: (e.g., DDL statements like DROP / ALTER / CREATE).
_DUCKDB_SCHEMA_TABLE_RE = re.compile(
    r'"(?P<schema>[A-Za-z0-9_\-]+__[A-Za-z0-9_\-]+)"\."(?P<table>[A-Za-z0-9_\-]+)"',
)


def _extract_schema_prefix(raw: str, *, duckdb_sql: str | None = None) -> str:
    """Return ``<schema>.`` for use as a qualifier in BQ-shape messages.

    DuckDB sometimes echoes the failing query and the schema appears as
    ``"<project>__<dataset>"."<table>"``; we recover the
    ``project.dataset.`` form so the conformance ``message_pattern``
    wildcard matches. DDL statements (DROP / ALTER / CREATE) raise a
    plain catalog error with no echo — for those we look at the
    DuckDB SQL the executor was running and pull the schema from
    there. Returns ``""`` (empty) when no schema can be recovered;
    the fixture must then carry a wider pattern or be marked xfail.
    """
    match = _LINE_ECHO_SCHEMA_RE.search(raw)
    if match is None and duckdb_sql is not None:
        match = _DUCKDB_SCHEMA_TABLE_RE.search(duckdb_sql)
    if match is None:
        return ""
    schema = match["schema"].replace("__", ".", 1)
    return f"{schema}."


__all__ = ["translate_runtime_error"]
