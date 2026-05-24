# ADR 0040: ``SESSION_USER()`` coverage closure — aliases + Storage Read pre-pass

- **Status**: Accepted
- **Supersedes**: [ADR 0038](0038-session-user.md) §"Out of scope"
  (partial — the three items closed here move from "out of scope"
  to "implemented"; the SQL-UDF body item remains out of scope).

## Context

[ADR 0038](0038-session-user.md) shipped ``SESSION_USER()`` as a
pre-translator substitution that folds every call to a string
literal carrying the resolved caller email. The original PR (#50)
deliberately scoped out three follow-up items:

1. **``CURRENT_USER()``** — function alias with identical semantics
   per BigQuery's documented surface.
2. **``@@session.user``** — system-variable spelling. Same value;
   shows up in ports of MySQL / PG-style code paths.
3. **Storage Read ``row_restriction`` caller threading** —
   ``grpc_api/read_servicer.py:122`` (``_build_filter_sql``) called
   the translator without a ``caller`` kwarg, so any caller-identity
   function inside a Storage Read ``row_restriction`` folded to the
   ``ANONYMOUS_CALLER`` sentinel regardless of the actual
   ``X-Bqemu-Caller`` header. The canonical SESSION_USER use is RAP
   filters (already correct), but a row-restriction-on-Storage-Read
   that referenced ``SESSION_USER()`` would deny every row in
   practice.

The three items are tightly coupled: closing (1) and (2) without
(3) leaves a known asymmetry between the REST-side query path
(``SESSION_USER`` resolves to the caller) and the Storage Read
gRPC path (``SESSION_USER`` always resolves to ``"anonymous"``).
This ADR closes all three in a single PR.

## Decision

**Extend the existing ``rewrite_session_user`` pre-translator to
recognise three node shapes; pass ``caller`` through every
remaining translator-call site that was previously dropping it.**

### 1. ``CURRENT_USER()`` — new AST match

SQLGlot parses ``CURRENT_USER()`` as :class:`sqlglot.exp.CurrentUser`,
parallel to the existing :class:`sqlglot.exp.SessionUser` match. The
new substitute helper folds both via the same
``resolve_session_user(caller)`` path — no new resolution logic.

### 2. ``@@session.user`` — Dot-pattern match

SQLGlot parses ``@@session.user`` as

```text
Dot(
  this=Parameter(this=Parameter(this=Var(this='session'))),
  expression=Identifier(this='user'),
)
```

The two-level ``Parameter`` nest mirrors the literal ``@@`` prefix
on the wire. A new helper :func:`_is_session_user_system_var`
pattern-matches the exact shape — not the rendered SQL — so user-
defined columns named ``user`` reached via an unrelated parameter
expression don't false-positive into the substitution path.

### 3. Storage Read ``row_restriction`` caller threading

Pre-ADR-0040 control flow in ``read_servicer._handle_create_read_session``:

```python
sql = _build_read_sql(target_ref, selected_fields, read_session)
# ... later in the function:
caller = resolve_caller_from_metadata(...)
```

``_build_read_sql`` ran the user's ``row_restriction`` through the
translator without a caller — every caller-identity call inside the
row_restriction folded to ``"anonymous"``. The fix hoists the
``resolve_caller_from_metadata`` call to *above* ``_build_read_sql``
and threads the resolved ``caller`` into ``_build_read_sql`` (and
through to the inner ``translator.translate(..., caller=caller)``
call). The second row-restriction handling path (the BigQuery-shaped
variant for the row-access policy rewriter) already received the
caller via the existing plumbing at line 252 (unchanged).

## Implementation contract

The public ``rewrite_session_user(bq_sql, caller)`` API in
[`bqemulator.sql.rewriter.session_user`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/session_user.py)
keeps its name and signature. Internally the substitute helper is
renamed from ``_substitute_session_user`` to
``_substitute_caller_identity_calls`` to reflect the broader
scope; this is a private helper so the rename has no API
consequence.

The fast-path string-side reject now checks all three spellings
(``session_user``, ``current_user``, ``@@session.user``) before
parsing the AST — same lower-case-tolerant strategy as ADR 0038.

## Resolution contract — unchanged from ADR 0038

* ``is_authenticated == False`` →
  [``ANONYMOUS_CALLER``](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/session_user.py) literal.
* ``user:<email>`` / ``serviceAccount:<email>`` / ``group:<email>``
  / ``domain:<host>`` → strip the prefix, return the bare email or host.
* Anything else (``allUsers``, ``allAuthenticatedUsers``, an unknown
  shape) → return the raw principal string unchanged.

All three spellings resolve via the same helper, so the contract
is identical across them — there is exactly one source of truth.

## Coverage

* **8 new unit tests** in
  [`tests/unit/sql/rewriter/test_session_user.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/rewriter/test_session_user.py)
  pin the three new code paths: bare ``CURRENT_USER()``, lower-case
  ``current_user()``, unauthenticated-caller fallback,
  RAP-filter-shape ``REGEXP_EXTRACT(CURRENT_USER(), …)``, bare
  ``@@session.user``, unauthenticated ``@@session.user``, the
  ``SELECT user FROM users`` false-positive guard, and all-three-
  spellings-in-one-query. The 21 existing SESSION_USER tests
  continue to pass — the new code is layered, not refactored.
* **1 new integration test** in
  [`tests/integration/test_storage_read_edge_cases.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/integration/test_storage_read_edge_cases.py)
  exercises a Storage Read ``row_restriction`` of the form
  ``owner = SESSION_USER()`` with an ``X-Bqemu-Caller`` gRPC
  metadata header. Pre-ADR-0040 the test would fail (every row
  filtered out because ``owner != 'anonymous'``); post-ADR-0040
  exactly the calling user's row is returned. This is the
  regression pin for the caller-threading change.
* **6 new e2e tests** (2 per client × Python / Node.js / Go /
  Java SDKs) cover ``SELECT CURRENT_USER()`` and
  ``SELECT @@session.user`` through the official client libraries
  against a live container. ``bq`` CLI is skipped per ADR 0038's
  existing rationale (the CLI doesn't set ``X-Bqemu-Caller``).

## Trade-offs considered

### Why not a single new node-type matcher?

We considered subclassing :class:`sqlglot.exp.Expression` to define
a single ``CallerIdentity`` marker and routing all three parsed
node types through it. Rejected because SQLGlot's AST shape for
``@@session.user`` (the Dot + nested Parameter + Identifier
structure) doesn't map cleanly to a function-call node; the
pattern-match in :func:`_is_session_user_system_var` is more
honest about the divergence than a forced marker would be.

### Why not move the substitution to the row-access enforcement pass?

The row-access enforcement pass already runs *after* the
pre-translator. Folding ``SESSION_USER()`` there would mean the
caller's email leaks into the RAP filter SQL but not into bare
``SELECT SESSION_USER()`` queries. The pre-translator integration
keeps the contract uniform across query shapes — bare selects,
scripts, RAP filters, view bodies — all see the same substituted
literal in the SQL that hits DuckDB.

### Why hoist the caller resolution rather than memoize it?

We considered caching the resolved caller in a context-local
variable so the row-restriction filter pre-pass could lazily
look it up. Rejected because the call order in
``_handle_create_read_session`` is small and explicit; an
``threadlocal`` / ``contextvars`` cache would add a second source
of truth for an identity that's already a per-request value.
Hoisting the resolution is one line of code; the lazy-cache path
is ~30 lines plus invisible coupling.

## Still out of scope

* **``SESSION_USER()`` inside a SQL UDF body** — UDFs are
  pre-translated at definition time when no caller exists, so the
  function inside a UDF body folds to ``"anonymous"`` permanently.
  Closing this requires a UDF-rewrite-at-call-time pass that's
  scope-comparable to the original ADR 0038 work; deferring.

## Expected impact

* No new SQL rules. The 92-rule translator handles every
  construct used by the new tests.
* No conformance corpus regressions — the SESSION_USER / GENERATE_UUID
  exclusions in
  [``tests/conformance/_surface_inventory.py``](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/_surface_inventory.py)
  (ADR 0022 §1.2) stand; ``CURRENT_USER`` and ``@@session.user``
  join the same non-deterministic-exclusion family with
  ``SESSION_USER``.
* Conformance-coverage-matrix re-generated with the new tier-2
  entries for the two new functions.

## References

* [ADR 0022 — Conformance corpus design](0022-conformance-corpus-design.md) §1.2 (non-determinism exclusions).
* [ADR 0038 — ``SESSION_USER()`` pre-translator substitution](0038-session-user.md).
* [BigQuery security-functions reference](https://cloud.google.com/bigquery/docs/reference/standard-sql/security_functions).
