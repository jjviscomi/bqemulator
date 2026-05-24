"""Pre-translator rewrite for BigQuery's caller-identity functions.

Covers all three spellings the BigQuery SQL surface accepts for
"who is running this query":

* ``SESSION_USER()`` — the canonical function form. Returns the
  email address of the calling principal (``alice@example.com`` for
  a user, ``sa@project.iam.gserviceaccount.com`` for a service
  account).
* ``CURRENT_USER()`` — function alias with identical semantics.
  Documented as a co-equal spelling in the BigQuery reference.
* ``@@session.user`` — system-variable spelling. Same value;
  shows up in ports of MySQL/PG-style code paths. SQLGlot parses
  this as ``Dot(Parameter(Parameter(Var('session'))),
  Identifier('user'))``; this rewriter recognises the exact shape
  and folds it to a string literal.

All three resolve via the same :func:`resolve_session_user` helper
(IAM-member-prefix stripping + unauthenticated fallback). The
emulator's caller identity is carried on the per-query
:class:`~bqemulator.row_access.identity.CallerIdentity` value already
threaded through the row-access enforcement path; this pre-translator
substitutes every call site with a string literal of the resolved
email *before* the SQLGlot BigQuery → DuckDB transpile.

Why a pre-translator and not a DuckDB pass-through:

DuckDB's native ``SESSION_USER`` resolves to the literal string
``'duckdb'`` (the OS-side identity of the DuckDB connection),
**not** the BigQuery caller — so a query like
``SELECT SESSION_USER()`` would silently produce ``'duckdb'`` and a
row-access policy filter
``REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = 'example.com'`` would
deny every legitimate caller. The pre-translator substitutes the call
with the resolved literal so the downstream transpile sees a
plain ``STRING`` constant DuckDB can evaluate correctly. The same
reasoning applies to ``CURRENT_USER()`` (DuckDB returns the OS user)
and ``@@session.user`` (DuckDB raises ``CatalogException`` — no
analog).

Why this rewriter is the right integration point:

The row-access policy enforcement pass
(``rewrite_for_row_access``) inlines policy filters into the user's
query as a derived-subquery WHERE clause. Any caller-identity call
site in the filter ends up in the rewritten SQL that flows into the
main translator. Running this rewriter from
:meth:`bqemulator.sql.translator.SQLTranslator.translate` therefore
catches all four use cases in one place: bare queries, scripts, RAP
filters (inlined by the row-access rewriter), and view bodies (also
inlined).

See [ADR 0038](../../docs/adr/0038-session-user.md) for the original
decision record and [ADR 0040](../../docs/adr/0040-session-user-coverage-closure.md)
for the alias + Storage Read closure.
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
    """Pre-translate BigQuery SQL for every caller-identity spelling.

    Handles ``SESSION_USER()``, ``CURRENT_USER()``, and
    ``@@session.user`` — all three fold to the same resolved string
    literal (the bare email returned by :func:`resolve_session_user`).

    Returns the input unchanged when no rewrite is needed (no
    caller-identity call site or the AST fails to parse — the
    downstream SQLGlot transpile then surfaces its own parse error
    message rather than this rewriter swallowing it).

    The rewriter is intentionally idempotent: applying it twice with
    the same caller yields the same output (the second pass finds
    zero matching nodes because the first pass replaced them all
    with string literals).
    """
    # Fast string-side reject — avoids parsing the AST when there
    # are no occurrences of any of the three spellings. Each check
    # is case-insensitive (BigQuery accepts mixed case) and tolerant
    # of whitespace before the ``(``.
    lowered = bq_sql.lower()
    if (
        "session_user" not in lowered
        and "current_user" not in lowered
        and "@@session.user" not in lowered
    ):
        return bq_sql

    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    resolved = resolve_session_user(caller)
    modified = _substitute_caller_identity_calls(parsed, resolved)
    if not modified:
        return bq_sql
    return parsed.sql(dialect="bigquery")


def _substitute_caller_identity_calls(tree: exp.Expression, resolved: str) -> bool:
    """Replace every caller-identity call site with ``Literal(resolved)``.

    Three node shapes match:

    * :class:`sqlglot.exp.SessionUser` — ``SESSION_USER()``
    * :class:`sqlglot.exp.CurrentUser` — ``CURRENT_USER()``
    * :class:`sqlglot.exp.Dot` whose structure encodes
      ``@@session.user`` (``Dot(Parameter(Parameter(Var('session'))),
      Identifier('user'))``).

    Returns True iff at least one node was replaced. Walks the tree
    snapshot up front because ``find_all`` would otherwise re-visit
    the replacement nodes (no-op, but wasteful).
    """
    modified = False
    function_nodes: list[exp.Expression] = [
        *tree.find_all(exp.SessionUser),
        *tree.find_all(exp.CurrentUser),
    ]
    for node in function_nodes:
        node.replace(exp.Literal.string(resolved))
        modified = True

    for dot_node in list(tree.find_all(exp.Dot)):
        if _is_session_user_system_var(dot_node):
            dot_node.replace(exp.Literal.string(resolved))
            modified = True

    return modified


def _is_session_user_system_var(node: exp.Dot) -> bool:
    """True iff ``node`` is the ``@@session.user`` AST shape.

    SQLGlot parses ``@@session.user`` as

        Dot(
          this=Parameter(this=Parameter(this=Var(this='session'))),
          expression=Identifier(this='user'),
        )

    The two-level ``Parameter`` nest mirrors the literal ``@@``
    prefix on the wire. We pattern-match the exact shape instead of
    string-matching the rendered SQL to avoid false positives on
    user-defined columns named ``user`` reached via a parameter
    expression.
    """
    rhs = node.expression
    if not isinstance(rhs, exp.Identifier) or rhs.name.lower() != "user":
        return False
    outer = node.this
    if not isinstance(outer, exp.Parameter):
        return False
    inner = outer.this
    if not isinstance(inner, exp.Parameter):
        return False
    var = inner.this
    return isinstance(var, exp.Var) and var.name.lower() == "session"


__all__ = [
    "ANONYMOUS_CALLER",
    "resolve_session_user",
    "rewrite_session_user",
]
