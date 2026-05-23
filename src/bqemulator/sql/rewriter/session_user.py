"""Pre-translator rewrite for BigQuery's ``SESSION_USER()`` function.

``SESSION_USER()`` returns the email address of the user running the
query (``alice@example.com`` for a user principal,
``sa@project.iam.gserviceaccount.com`` for a service account). The
emulator's caller identity is carried on the per-query
:class:`~bqemulator.row_access.identity.CallerIdentity` value already
threaded through the row-access enforcement path; this pre-translator
substitutes every ``SESSION_USER()`` call site with a string literal
of the resolved email *before* the SQLGlot BigQuery → DuckDB transpile.

Why a pre-translator and not a DuckDB pass-through:

DuckDB's native ``SESSION_USER`` resolves to the literal string
``'duckdb'`` (the OS-side identity of the DuckDB connection),
**not** the BigQuery caller — so a query like
``SELECT SESSION_USER()`` would silently produce ``'duckdb'`` and a
row-access policy filter
``REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = 'example.com'`` would
deny every legitimate caller. The pre-translator substitutes the call
with the resolved literal so the downstream transpile sees a
plain ``STRING`` constant DuckDB can evaluate correctly.

Why this rewriter is the right integration point:

The row-access policy enforcement pass
(``rewrite_for_row_access``) inlines policy filters into the user's
query as a derived-subquery WHERE clause. Any ``SESSION_USER()`` in
the filter ends up in the rewritten SQL that flows into the main
translator. Running this rewriter from
:meth:`bqemulator.sql.translator.SQLTranslator.translate` therefore
catches all four use cases in one place: bare queries, scripts, RAP
filters (inlined by the row-access rewriter), and view bodies (also
inlined).

See [ADR 0038](../../docs/adr/0038-session-user.md) for the full
decision record (option A vs B vs C, fallback identity contract, and
out-of-scope notes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.row_access.identity import CallerIdentity


#: BigQuery's IAM-member kind prefixes. ``SESSION_USER()`` returns the
#: bare email (``alice@example.com``) for ``user:`` / ``serviceAccount:``
#: callers; we strip the prefix to match that contract.
#: ``group:`` / ``domain:`` are never passed as the request's
#: ``X-Bqemu-Caller`` in practice, but stripping them defensively keeps
#: the function consistent with BigQuery's "principal email" shape.
_IAM_MEMBER_PREFIXES: tuple[str, ...] = (
    "user:",
    "serviceAccount:",
    "group:",
    "domain:",
)

#: Sentinel returned when the caller is the unauthenticated fallback
#: identity. Real BigQuery never reaches this branch because every
#: API call carries an authenticated caller; the emulator's default
#: caller (``DEFAULT_CALLER`` from ``row_access.identity``) folds to
#: this string so RAP policies that compare ``SESSION_USER()`` against
#: a tenant key deny every row for un-headered requests. Documented
#: in ADR 0038 §"Unauthenticated fallback".
ANONYMOUS_CALLER = "anonymous"


def resolve_session_user(caller: CallerIdentity) -> str:
    """Return BigQuery's ``SESSION_USER()`` value for ``caller``.

    Resolution:

    * ``is_authenticated == False`` → :data:`ANONYMOUS_CALLER` literal.
    * ``user:<email>`` / ``serviceAccount:<email>`` /
      ``group:<email>`` / ``domain:<host>`` → strip the prefix, return
      the bare email or host.
    * Anything else (``allUsers``, ``allAuthenticatedUsers``, an
      unknown shape) → return the raw principal string unchanged.
      Real BigQuery never invokes the function as one of these
      members because they're grantee-side identifiers, not caller
      identifiers; the passthrough exists so a misconfigured emulator
      doesn't raise.
    """
    if not caller.is_authenticated:
        return ANONYMOUS_CALLER
    principal = caller.principal
    for prefix in _IAM_MEMBER_PREFIXES:
        if principal.startswith(prefix):
            return principal[len(prefix) :]
    return principal


def rewrite_session_user(bq_sql: str, caller: CallerIdentity) -> str:
    """Pre-translate BigQuery SQL for the ``SESSION_USER()`` function.

    Returns the input unchanged when no rewrite is needed (no
    ``SESSION_USER()`` call site or the AST fails to parse — the
    downstream SQLGlot transpile then surfaces its own parse error
    message rather than this rewriter swallowing it).

    The rewriter is intentionally idempotent: applying it twice with
    the same caller yields the same output (the second pass finds
    zero ``SessionUser`` nodes because the first pass replaced them
    all with string literals).
    """
    # Fast string-side reject — avoids parsing the AST when there are
    # no occurrences. The check is case-insensitive (BigQuery accepts
    # ``session_user()`` and ``Session_User()`` too) and tolerates
    # whitespace before the ``(``.
    if "session_user" not in bq_sql.lower():
        return bq_sql

    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    resolved = resolve_session_user(caller)
    modified = _substitute_session_user(parsed, resolved)
    if not modified:
        return bq_sql
    return parsed.sql(dialect="bigquery")


def _substitute_session_user(tree: exp.Expression, resolved: str) -> bool:
    """Replace every ``SessionUser`` node with ``Literal(resolved)``.

    Returns True iff at least one node was replaced. Walks the tree
    snapshot up front because ``find_all`` would otherwise re-visit
    the replacement nodes (no-op, but wasteful).
    """
    modified = False
    nodes = list(tree.find_all(exp.SessionUser))
    for node in nodes:
        node.replace(exp.Literal.string(resolved))
        modified = True
    return modified


__all__ = [
    "ANONYMOUS_CALLER",
    "resolve_session_user",
    "rewrite_session_user",
]
