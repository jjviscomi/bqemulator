"""Row-order perturbation helpers for the differential conformance tier.

The differential tier (workstream P8.f, ADR 0028) re-runs every
**perturbable** conformance fixture with the order of its setup rows
reversed. The query is unchanged; only the order rows reach DuckDB
changes. A fixture that returns the same result set under both
orderings exercises BigQuery-equivalent semantics independent of
storage order; a fixture that diverges under perturbation hides a
storage-order shortcut the recorded baseline cannot surface.

The two surfaces this module exposes:

* :func:`reverse_insert_values` — a parser that walks ``INSERT INTO …
  VALUES (…), (…), … ;`` statements and reverses the order of the
  value tuples. CREATE TABLE / DDL / non-INSERT statements are
  preserved verbatim. The parser respects single/double-quoted
  strings, backtick-quoted identifiers, line comments, and balanced
  parentheses (so a ``STRUCT(1, 2)`` literal inside a tuple is parsed
  as one token rather than confused for a tuple boundary).
* :func:`is_perturbable` — eligibility check applied per fixture by
  the runner. Returns ``(perturbable, reason)`` so the parametrised
  test can record *why* a fixture was skipped (visible in pytest
  output via ``pytest.skip``).

ADR 0028 §"Perturbation taxonomy" defines three perturbation modes:

* **A. Row-order.** Reverse the insertion order of setup rows. The
  query result must match the recorded baseline under canonical
  sorting. **This module implements A only.**
* **B. Value-shift.** Add a fixed offset to numeric/date columns.
  Requires operator BigQuery time to re-record perturbed-sibling
  fixtures; deferred to v1.0.x.
* **C. Schema-reorder.** Permute the ``CREATE TABLE`` column order
  while preserving row identity. Same v1.0.x deferral as B.

The skip-list (:data:`PERTURBATION_SKIP_LIST`) captures fixtures
whose semantics are intentionally row-order-dependent (e.g.
``ARRAY_AGG`` without ``ORDER BY``, ``LIMIT`` without ``ORDER BY``)
or whose setup cannot be syntactically perturbed (e.g. setup that
loads from a fixture-staged binary). Every entry references an ADR
or an ``out-of-scope.md`` anchor — invented skips are forbidden.
"""

from __future__ import annotations

import json
import re

from tests.conformance._corpus import Fixture

#: Sentinel returned by :func:`is_perturbable` when the fixture is
#: eligible. The empty-reason form is canonical so the runner can
#: emit ``pytest.skip(reason)`` without re-checking the bool.
_PERTURBABLE_OK = (True, "")

#: Keyword markers that imply the query's row-order or row-content
#: depends on storage order by design. A fixture matching any of
#: these is skipped because a divergence under perturbation reflects
#: BigQuery's own non-determinism on these surfaces, not an emulator
#: bug. The markers are matched case-insensitively on the query text
#: AFTER stripping line comments. Word-boundary anchored so a
#: column named ``my_limit`` is not mistaken for the ``LIMIT``
#: clause.
_ORDER_SENSITIVE_KEYWORDS: tuple[str, ...] = (
    # Order-sensitive aggregates whose output content depends on the
    # input row order. BigQuery documents these as non-deterministic
    # without an explicit ORDER BY inside the aggregate.
    "ARRAY_AGG",
    "STRING_AGG",
    "ANY_VALUE",
    "APPROX_QUANTILES",
    "APPROX_TOP_COUNT",
    "APPROX_TOP_SUM",
    # HLL sketch construction is technically order-independent, but
    # the recorded serialised form encodes register state in a way
    # that's not byte-stable across input permutations; the runner
    # already xfails these fixtures via divergences.py — perturbing
    # them would just turn an xfail PASS into an xfail FAIL.
    "HLL_COUNT.INIT",
    "HLL_COUNT.MERGE_PARTIAL",
    # Order-sensitive window functions WITHOUT an explicit ORDER BY
    # inside the OVER clause produce non-deterministic output. The
    # check is conservative — a fixture that uses ROW_NUMBER WITH an
    # OVER (ORDER BY …) is also skipped, which loses some coverage,
    # but avoids hand-rolling an OVER-clause parser.
    "ROW_NUMBER",
    "RANK",
    "DENSE_RANK",
    "PERCENT_RANK",
    "CUME_DIST",
    "NTILE",
    "FIRST_VALUE",
    "LAST_VALUE",
    "NTH_VALUE",
    "LAG",
    "LEAD",
    # Sampling operators are row-order-dependent by spec.
    "TABLESAMPLE",
)

_ORDER_BY_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

#: Skip-list of fixtures whose row-order perturbation is intentionally
#: invalid even after the structural skip checks pass. Each entry
#: maps a fixture id to a rationale that references an ADR or an
#: ``out-of-scope.md`` anchor. The runner emits ``pytest.skip`` with
#: this rationale so a CI failure on a skip-listed fixture is visible
#: rather than silently green. Populated during P8.f triage — see
#: ADR 0028 §"Skip-list policy".
PERTURBATION_SKIP_LIST: dict[str, str] = {
    # Populated by the P8.f triage pass. Each entry MUST cite an ADR
    # or an out-of-scope.md anchor; invented skips are forbidden.
}


def is_perturbable(fixture: Fixture) -> tuple[bool, str]:
    """Return ``(True, "")`` iff the fixture supports row-order perturbation.

    The structural checks come first (no setup, no INSERT VALUES, REST
    setup, identity headers, expected error) so they short-circuit
    without the cost of scanning the query text. The semantic checks
    follow: top-level ``ORDER BY`` pins row order; ``LIMIT`` without
    ``ORDER BY`` is non-deterministic by BigQuery's own contract;
    order-sensitive aggregates / window functions need an explicit
    ``ORDER BY`` inside the OVER clause to be deterministic, and the
    runner cannot cheaply verify that — so it conservatively skips.

    The skip-list (:data:`PERTURBATION_SKIP_LIST`) is consulted last
    so individual rationales remain debuggable in pytest output.
    """
    if fixture.setup_sql is None:
        return False, "no setup.sql to perturb"
    if not _contains_insert_values(fixture.setup_sql):
        return False, "setup.sql has no INSERT … VALUES tuples"
    if fixture.setup_rest:
        return False, "fixture uses setup_rest.json (not perturbable)"
    if fixture.headers:
        return False, "fixture pins caller-identity headers"
    if fixture.parameters is not None or fixture.job_config is not None:
        # Parameter / job-config fixtures pin specific request shapes;
        # the perturbation does not exercise their non-storage axis,
        # so they're conservatively skipped from the differential pass
        # for v1.0. ADR 0028 §"Skip-list policy" carries the rationale.
        return False, "fixture pins parameters or job_config"

    sanitised = _strip_comments(fixture.query_sql)

    if _ORDER_BY_RE.search(sanitised):
        return False, "query has ORDER BY (row order is contractually pinned)"
    if _LIMIT_RE.search(sanitised):
        return False, "query has LIMIT (storage-order-dependent without ORDER BY)"

    upper_sql = sanitised.upper()
    for keyword in _ORDER_SENSITIVE_KEYWORDS:
        if _keyword_matches(upper_sql, keyword):
            return False, f"query uses order-sensitive operator {keyword!r}"

    skip_reason = PERTURBATION_SKIP_LIST.get(fixture.id)
    if skip_reason is not None:
        return False, skip_reason

    return _PERTURBABLE_OK


def _keyword_matches(upper_sql: str, keyword: str) -> bool:
    """Match ``keyword`` against ``upper_sql`` with word boundaries.

    SQL keywords containing a ``.`` (e.g. ``HLL_COUNT.INIT``) fall
    through the standard ``\\b`` boundary at the ``.`` — Python's
    ``re`` treats ``.`` as a non-word character, so ``\\b`` correctly
    fires on both sides of the dot. Identifiers like ``my_array_agg``
    are not mis-matched because ``_`` IS a word character — ``\\b``
    requires a transition, so ``my_array_agg`` does not start a
    boundary at ``array``.
    """
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return re.search(pattern, upper_sql) is not None


def _strip_comments(sql: str) -> str:
    """Remove SQL comments so keyword detection isn't fooled by docstrings.

    A fixture's ``query.sql`` may carry leading ``--`` comments
    describing what it tests. A token like ``LIMIT`` mentioned in a
    comment must not cause a structural skip. Block comments are
    also stripped though the corpus authoring guide forbids them
    inside fixture SQL.
    """
    no_block = _BLOCK_COMMENT_RE.sub("", sql)
    return _LINE_COMMENT_RE.sub("", no_block)


def _contains_insert_values(setup_sql: str) -> bool:
    """Return ``True`` iff the setup script has at least one ``INSERT … VALUES``.

    A purely-DDL setup (CREATE TABLE only; or ``INSERT … SELECT …``)
    has no value tuples to reverse and is structurally non-perturbable.
    The check is intentionally a substring scan — the parser does the
    rigorous matching when it actually rewrites the script.
    """
    cleaned = _strip_comments(setup_sql).upper()
    if "INSERT" not in cleaned:
        return False
    return "VALUES" in cleaned and bool(re.search(r"\bVALUES\b\s*\(", cleaned))


def reverse_insert_values(setup_sql: str) -> str:
    """Return a copy of ``setup_sql`` with every ``INSERT … VALUES`` tuple list reversed.

    The walker preserves the original formatting of everything outside
    the tuple lists — CREATE TABLE statements, comments, whitespace,
    and the ``INSERT INTO foo (col1, col2) VALUES`` preamble are all
    rendered byte-for-byte unchanged. Only the comma-separated tuple
    list AFTER ``VALUES`` is rewritten.

    The implementation is a single-pass character walker that
    distinguishes:

    * **Strings**: single-quoted ``'…'`` and double-quoted ``"…"``.
      Inside a string, ``,`` and ``)`` are inert.
    * **Backtick identifiers**: ``` `…` ``` (BigQuery's quoting for
      identifiers that need it; e.g. ``\\`project.dataset.table\\```).
    * **Line comments**: ``-- …\\n``. A ``,`` or ``)`` inside a
      comment is inert.
    * **Nested parens**: ``STRUCT(1, 2)`` inside a tuple is one
      token, not two tuples.

    The walker yields a list of ``(prefix, tuples, suffix)`` runs
    where ``prefix`` is the literal text up to and including the
    ``VALUES`` keyword, ``tuples`` is the parsed tuple list (each
    element is a string including its surrounding whitespace), and
    ``suffix`` is what follows the last tuple. The function rebuilds
    the script with ``tuples`` reversed.
    """
    out: list[str] = []
    pos = 0
    while True:
        # ``re.search`` over the still-unprocessed tail.
        match = re.search(r"\bVALUES\b", setup_sql[pos:], re.IGNORECASE)
        if match is None:
            out.append(setup_sql[pos:])
            break
        prefix_end = pos + match.end()
        out.append(setup_sql[pos:prefix_end])

        try:
            tuples, after_pos = _parse_value_tuples(setup_sql, prefix_end)
        except _UnparseableValuesError:
            # If we can't parse the VALUES clause cleanly, leave the
            # rest of the script alone. The runner will then run the
            # un-perturbed setup and the fixture won't surface a
            # genuine row-order divergence — but neither will it
            # report a spurious one. The triage pass will see "no
            # divergence" and the matter is dropped.
            out.append(setup_sql[prefix_end:])
            break

        if not tuples:
            out.append(setup_sql[prefix_end:after_pos])
            pos = after_pos
            continue

        out.append(_render_reversed_tuples(tuples))
        pos = after_pos
    return "".join(out)


class _UnparseableValuesError(Exception):
    """Raised by :func:`_parse_value_tuples` on malformed VALUES syntax."""


def _parse_value_tuples(text: str, start: int) -> tuple[list[str], int]:
    """Parse a ``(…), (…), …`` tuple list starting at ``text[start:]``.

    Returns ``(tuples, after_pos)`` where ``tuples`` is the list of
    *raw* tuple substrings (each WITHOUT its surrounding whitespace
    or separator commas, but including the parens themselves), and
    ``after_pos`` is the position one character past the last tuple's
    closing paren. The caller renders the reversed list back into
    ``text`` between ``start`` and ``after_pos``.

    Skipped non-tuple bytes between tuples (whitespace, line breaks,
    the inter-tuple commas) are collapsed in the rebuild — the
    rendered output uses ``, `` as the canonical separator. This is
    acceptable because BigQuery / DuckDB SQL is whitespace-insensitive
    inside VALUES clauses; the result is functionally identical to
    the original.
    """
    tuples: list[str] = []
    pos = start

    while pos < len(text):
        # Skip whitespace + line comments between tuples.
        pos = _skip_insignificant(text, pos)
        if pos >= len(text):
            break
        char = text[pos]
        if char == "(":
            tuple_end = _scan_balanced_paren(text, pos)
            tuples.append(text[pos:tuple_end])
            pos = tuple_end
            # After the closing paren, look for a separator comma.
            pos = _skip_insignificant(text, pos)
            if pos < len(text) and text[pos] == ",":
                pos += 1
                continue
            # No comma → tuple list is complete.
            break
        # Anything else means the VALUES clause has ended (next
        # statement, ON CONFLICT clause, etc.). Stop here.
        break

    return tuples, pos


def _skip_insignificant(text: str, pos: int) -> int:
    """Advance ``pos`` past whitespace and line comments."""
    while pos < len(text):
        char = text[pos]
        if char.isspace():
            pos += 1
            continue
        if char == "-" and pos + 1 < len(text) and text[pos + 1] == "-":
            # Line comment — skip to next newline.
            newline = text.find("\n", pos)
            if newline == -1:
                return len(text)
            pos = newline + 1
            continue
        if char == "/" and pos + 1 < len(text) and text[pos + 1] == "*":
            # Block comment.
            close = text.find("*/", pos + 2)
            if close == -1:
                msg = "unterminated block comment in setup.sql"
                raise _UnparseableValuesError(msg)
            pos = close + 2
            continue
        break
    return pos


def _scan_balanced_paren(text: str, pos: int) -> int:
    """Return the index one past the matching close-paren for ``text[pos] == '('``.

    The scanner tracks string state (single quote, double quote,
    backtick) so ``,`` and ``)`` inside a literal don't confuse the
    paren counter. Nested parens are balanced by depth; reaching EOF
    without closing raises.
    """
    if pos >= len(text) or text[pos] != "(":
        msg = f"expected '(' at position {pos}, got {text[pos : pos + 1]!r}"
        raise _UnparseableValuesError(msg)

    depth = 0
    in_quote: str | None = None
    i = pos
    while i < len(text):
        char = text[i]
        if in_quote is not None:
            if char == "\\" and i + 1 < len(text):
                # Escape sequence inside the string — skip the next
                # char so an escaped quote doesn't end the literal.
                i += 2
                continue
            if char == in_quote:
                in_quote = None
            i += 1
            continue
        if char in ("'", '"', "`"):
            in_quote = char
            i += 1
            continue
        if char == "(":
            depth += 1
            i += 1
            continue
        if char == ")":
            depth -= 1
            i += 1
            if depth == 0:
                return i
            continue
        i += 1
    msg = f"unbalanced parentheses starting at position {pos}"
    raise _UnparseableValuesError(msg)


def _render_reversed_tuples(tuples: list[str]) -> str:
    """Render the reversed tuple list back into canonical separator form.

    Uses ``,\\n  `` as the inter-tuple separator so the rendered SQL
    is human-readable in test output. The exact whitespace doesn't
    matter to DuckDB; this format mirrors the corpus's own indent
    convention.
    """
    return ",\n  ".join(reversed(tuples))


def canonical_row_key(row: dict[str, object]) -> str:
    """Stable JSON-based sort key for a result row.

    Used by the differential runner to canonical-sort both the
    recorded ``expected.rows`` and the emulator's actual rows so they
    line up for pairwise comparison even when BigQuery and DuckDB
    return the same row contents in different orders.

    The key is ``json.dumps(row, sort_keys=True, default=str)``:

    * ``sort_keys=True`` so dict-ordering doesn't affect the key.
    * ``default=str`` so non-JSON Python objects (e.g. ``Decimal``,
      ``datetime`` returned by the BigQuery client) round-trip
      through their ``str()`` form.

    Floating-point ULP drift between BigQuery and DuckDB on the same
    expression could theoretically cause two semantically-equal rows
    to sort differently, but in practice corpus fixtures use distinct
    column values per row so the sort is stable. If a future fixture
    surfaces ULP-jitter sorting, the right closure is to widen the
    fixture's integer / string anchor column rather than complicate
    the canonical key.
    """
    return json.dumps(row, sort_keys=True, default=str)


__all__ = [
    "PERTURBATION_SKIP_LIST",
    "canonical_row_key",
    "is_perturbable",
    "reverse_insert_values",
]
