"""Pre-translator for BigQuery's ``RANGE_SESSIONIZE`` table-valued function.

BigQuery's ``RANGE_SESSIONIZE(TABLE <ref>, '<range_col>', [<part_cols>]
[, '<sessionize_option>'])`` groups rows by their partition columns,
orders them by the named RANGE-typed column, and emits each input row
plus a ``session_range`` RANGE column spanning the session the row
belongs to.

SQLGlot's BigQuery parser rejects the ``TABLE <ref>`` token in TVF
arguments (no ``TABLE`` keyword exists in its TVF-call grammar), so
the rewrite must happen at the source-text level — before SQLGlot
ever sees the SQL. Every match of the ``RANGE_SESSIONIZE(...)`` call
shape is replaced with a windowed subquery that implements the
gaps-and-islands sessionisation pattern. The subquery's outer SELECT
emits ``RANGE(MIN(...), MAX(...)) AS session_range``; the existing
:mod:`bqemulator.sql.rewriter.specialized_types`
``rewrite_specialized_types`` pass (which runs right after this one)
rewrites that ``RANGE(a, b)`` constructor into the canonical
``STRUCT(a AS 'start', b AS 'end')`` shape SQLGlot transpiles cleanly
to DuckDB. The resulting DuckDB column lands as ``STRUCT("start" T,
"end" T)`` — the exact shape :func:`bqemulator.types.range_type.detect_range_element`
maps back to ``RANGE<T>`` on the REST wire.

Sessionize-option semantics (BigQuery documented behaviour, confirmed
against real-BigQuery recordings in the conformance corpus):

* ``MEETS`` (default) — a new session begins iff the current row's
  range *start* is **strictly greater than** the running maximum of
  prior row ends. Touching ranges (current.start == max_prior_end)
  stay in the same session, as do overlapping ranges.
* ``OVERLAPS`` — a new session begins iff the current row's range
  *start* is **greater than or equal to** the running maximum of
  prior row ends. Touching ranges form *separate* sessions; only
  strict overlap keeps them together.

Unknown modes (including ``OVERLAPS_OR_MEETS`` — a token whose
"alias for MEETS" status was empirically refuted on 2026-05-18 when
the ``specialized_types/range_sessionize_overlaps_or_meets_alias``
conformance fixture recorded a BigQuery ``invalidQuery`` error
``Could not cast literal "OVERLAPS_OR_MEETS" to type
RANGE_SESSIONIZE_MODE``) raise :class:`InvalidQueryError` matching
BigQuery's wire-format rejection.

The ``MAX(range.end) OVER (… ROWS BETWEEN UNBOUNDED PRECEDING AND 1
PRECEDING)`` window tracks the running max of prior row ends, which
correctly captures the cumulative reach of overlapping ranges
(``[1, 10), [5, 7)`` → after the second row, the session still
extends to 10, not 7).
"""

from __future__ import annotations

import re

#: BigQuery sessionize-option → comparison operator that determines
#: whether a row starts a new session relative to the running maximum
#: of prior-row ends in the partition. ``MEETS`` uses strict ``>``
#: (touching stays in the same session); ``OVERLAPS`` uses ``>=``
#: (touching ends form separate sessions). No other tokens are
#: accepted — see the ``OVERLAPS_OR_MEETS`` empirical-refutation note
#: in the module docstring.
_MODE_TO_OP: dict[str, str] = {
    "MEETS": ">",
    "OVERLAPS": ">=",
}

#: Matches one ``RANGE_SESSIONIZE(TABLE <ref>, '<col>', [<parts>]
#: [, '<mode>'])`` call. The pattern is intentionally tight:
#: ``<table>`` accepts backticked refs (``` `proj.ds.tbl` ```) and
#: bare qualified refs (``proj.ds.tbl``) including hyphens (BigQuery
#: project IDs allow ``-``); the column-name and mode literals are
#: single-quoted; the partition-columns list is a flat
#: comma-separated set of string literals. Multi-line whitespace is
#: tolerated (the conformance fixtures format the call across several
#: lines).
_CALL_RE = re.compile(
    r"""
    RANGE_SESSIONIZE\s*\(\s*
    TABLE\s+
    (?P<table>`[^`]+`|[A-Za-z_][\w.\-]*)
    \s*,\s*
    '(?P<range_col>[^']+)'
    \s*,\s*
    \[(?P<parts>[^\]]*)\]
    (?:\s*,\s*'(?P<mode>[^']+)')?
    \s*\)
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

#: Pulls every single-quoted string literal out of the partition-
#: columns list body. Empty body (``[]``) yields zero matches — the
#: rewriter handles that by emitting an OVER clause with no
#: ``PARTITION BY`` (one global session per fixture).
_PART_LITERAL_RE = re.compile(r"'([^']+)'")


def rewrite_range_sessionize(bq_sql: str) -> str:
    """Replace every ``RANGE_SESSIONIZE(...)`` call with a windowed subquery.

    Returns the input unchanged when the SQL contains no
    ``RANGE_SESSIONIZE`` token (the common case). The substring guard
    keeps the regex pass off the critical path for every non-RANGE-
    SESSIONIZE query in the corpus.
    """
    if "RANGE_SESSIONIZE" not in bq_sql.upper():
        return bq_sql
    return _CALL_RE.sub(_expand_call, bq_sql)


def _expand_call(match: re.Match[str]) -> str:
    """Build the windowed-subquery rewrite for one matched call.

    The output is a single parenthesised SELECT that drops in as a
    FROM-clause table source. Three helper columns
    (``_bqemu_max_prior_end``, ``_bqemu_partition_has_null``,
    ``_bqemu_session_id``) are computed in nested subqueries and then
    projected out via ``SELECT * EXCEPT`` so the call site sees only
    the original columns plus ``session_range``.

    NULL-bridge semantic: BigQuery's ``RANGE_SESSIONIZE`` collapses every non-NULL row in
    a partition into a single session spanning
    ``[min(start), max(end)]`` whenever any NULL range is present in
    that partition; the NULL rows themselves return
    ``session_range = NULL``. The
    ``specialized_types/range_sessionize_null_range``
    conformance fixture pins this contract against real BigQuery.
    The rewriter implements the bridge via two coordinated changes:

    1. ``_bqemu_partition_has_null = BOOL_OR(<range>.start IS NULL)
       OVER (PARTITION BY parts)`` flags partitions containing any
       NULL range.
    2. The session-id CASE skips NULL rows entirely (no increment)
       and suppresses the standard MEETS / OVERLAPS comparison
       (``rc.start > _bqemu_max_prior_end``) when
       ``_bqemu_partition_has_null`` is TRUE — so every non-NULL row
       in a NULL-containing partition shares the same session id
       (1, the first non-NULL row's increment).
    3. The outer ``session_range`` is wrapped in
       ``CASE WHEN rc.start IS NULL THEN NULL ELSE RANGE(...) END``
       so NULL rows output ``NULL`` instead of
       ``STRUCT(NULL, NULL)``.
    """
    table = match.group("table")
    range_col = match.group("range_col")
    parts_text = match.group("parts").strip()
    mode_raw = match.group("mode")
    mode = (mode_raw or "MEETS").upper()
    # ADR 0022 §3 (Error parity, 2026-05-17). BigQuery rejects unknown
    # mode literals at analysis time with
    # ``Could not cast literal "X" to type RANGE_SESSIONIZE_MODE``;
    # without this guard the emulator silently accepted unknown modes
    # because :data:`_MODE_TO_OP.get(mode, ">")` defaulted to the
    # ``MEETS`` comparator. The recorded mode is preserved verbatim in
    # the message so the conformance ``message_pattern`` regex matches.
    if mode not in _MODE_TO_OP:
        from bqemulator.domain.errors import InvalidQueryError

        raise InvalidQueryError(
            f'Could not cast literal "{mode_raw}" to type RANGE_SESSIONIZE_MODE at [1:1]',
            location="query",
        )
    op = _MODE_TO_OP[mode]

    partition_columns = _PART_LITERAL_RE.findall(parts_text)
    part_list = ", ".join(f"`{p}`" for p in partition_columns)
    partition_clause = f"PARTITION BY {part_list}" if partition_columns else ""
    session_partition_clause = (
        f"PARTITION BY {part_list}, _bqemu_session_id"
        if partition_columns
        else "PARTITION BY _bqemu_session_id"
    )

    rc_start = f"`{range_col}`.start"
    rc_end = f"`{range_col}`.`end`"
    order_clause = f"ORDER BY {rc_start}, {rc_end}"

    prior_end_window = " ".join(
        clause
        for clause in (
            partition_clause,
            order_clause,
            "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING",
        )
        if clause
    )
    session_id_window = " ".join(
        clause
        for clause in (
            partition_clause,
            order_clause,
            "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW",
        )
        if clause
    )

    return (
        "(SELECT * EXCEPT "
        "(_bqemu_max_prior_end, _bqemu_partition_has_null, _bqemu_session_id) FROM ("
        "  SELECT *,"
        f"    CASE WHEN {rc_start} IS NULL THEN NULL"
        "    ELSE RANGE("
        f"      MIN({rc_start}) OVER ({session_partition_clause}),"
        f"      MAX({rc_end}) OVER ({session_partition_clause})"
        "    )"
        "    END AS session_range"
        "  FROM ("
        "    SELECT *,"
        "      SUM(CASE"
        f"            WHEN {rc_start} IS NULL THEN 0"
        "            WHEN _bqemu_max_prior_end IS NULL"
        f"                 OR ({rc_start} {op} _bqemu_max_prior_end"
        "                     AND NOT _bqemu_partition_has_null)"
        "            THEN 1"
        "            ELSE 0"
        "          END)"
        f"        OVER ({session_id_window}) AS _bqemu_session_id"
        "    FROM ("
        "      SELECT *,"
        f"        MAX({rc_end}) OVER ({prior_end_window}) AS _bqemu_max_prior_end,"
        f"        BOOL_OR({rc_start} IS NULL) OVER ({partition_clause})"
        "          AS _bqemu_partition_has_null"
        f"      FROM {table}"
        "    )"
        "  )"
        "))"
    )


__all__ = ["rewrite_range_sessionize"]
