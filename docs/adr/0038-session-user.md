# ADR 0038: ``SESSION_USER()`` — caller-identity-bound pre-translator substitution

- **Status**: Accepted

## Context

BigQuery's [`SESSION_USER()`](https://cloud.google.com/bigquery/docs/reference/standard-sql/security_functions)
returns the email address of the user running the query —
``alice@example.com`` for a user principal,
``sa@project.iam.gserviceaccount.com`` for a service account. The
canonical production use case is "tenant isolation by email domain"
in row-access policy filters:

```sql
CREATE ROW ACCESS POLICY tenant_by_session_user ON dataset.tenants
GRANT TO ('allAuthenticatedUsers')
FILTER USING (REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = tenant_id);
```

The pattern resolves the caller's email, extracts the domain, and
matches it against a per-row tenant key — so a caller from
``@example.com`` sees only the ``example.com`` rows without any
explicit grantee list (which would otherwise need to enumerate every
authenticated user). Real BigQuery users rely on this; the emulator
must support it.

Pre-PR audit findings (verified at the start of the work):

* Zero implementation under ``src/bqemulator/sql/`` — the function
  was never wired.
* Zero tests anywhere (``grep -rn 'SESSION_USER' tests/`` returned
  only the surface-inventory entry).
* The surface-inventory entry (``tests/conformance/_surface_inventory.py``)
  rendered in the conformance-coverage matrix as
  ``⚪ Excluded by ADR 0022 §1.2 — session-state dependent.
  Exercised at the unit tier.`` The last sentence was aspirational —
  the unit-tier coverage didn't exist.
* DuckDB's native ``SESSION_USER`` resolves to the literal
  ``'duckdb'`` (the DuckDB connection's OS identity), **not** the
  BigQuery caller — so a query like ``SELECT SESSION_USER()`` would
  silently return ``'duckdb'`` and any RAP filter built on it would
  match no real caller.
* The five-client E2E charter (AGENTS.md "Testing expectations":
  *"every new BigQuery surface item gets a SurfaceItem entry...
  never skip an e2e test language"*) was unmet for this surface.

This ADR closes that gap with both the implementation and the
end-to-end client coverage.

## Decision

Implement ``SESSION_USER()`` as a **pre-translator substitution
pass** that takes the per-query ``CallerIdentity`` and rewrites
every ``SESSION_USER()`` call site in the SQL to a string literal of
the caller's resolved email **before** SQLGlot's BigQuery → DuckDB
transpile runs. The substitution happens inside
``SQLTranslator.translate()``, so it covers every place the
translator runs: bare queries, scripts, RAP filter predicates
(inlined by ``rewrite_for_row_access``), view bodies (also inlined
by the same pass), and the gRPC Storage Read row-filter rewrite when
caller context is available.

### Three options considered

| Option | Shape | Decision |
|---|---|---|
| **A — SQL pre-translator** | New ``rewrite_session_user(sql, caller)`` AST walk substitutes ``SessionUser`` nodes with ``Literal(email)`` before SQLGlot transpiles. Needs ``caller`` plumbed into ``Translator.translate``. | ✅ **Chosen.** |
| **B — RAP-rewriter-only substitution** | Substitute only inside policy filters. Bare ``SELECT SESSION_USER()`` raises ``UnsupportedFeatureError``. | Rejected. Narrower than BigQuery's contract; surprises users. |
| **C — DuckDB scalar UDF reading a session variable** | Register a Python-side ``bqemu_session_user()`` UDF that reads per-query state from a context variable. Translator rewrites ``SESSION_USER()`` → ``bqemu_session_user()``. | Rejected. DuckDB's connection is shared across requests; per-call state plumbing through a UDF context is more invasive than threading ``caller`` directly. |

Option A wins on three axes:

1. **BigQuery semantic fidelity.** ``SESSION_USER()`` works
   *everywhere* SQL runs — not just inside RAP filters.
2. **Existing infrastructure reuse.** The row-access enforcement
   path already constructs ``CallerIdentity`` per-request and threads
   it through ``rewrite_for_row_access``; extending the same value
   one layer down (to the translator) is additive and uses an
   existing dataclass.
3. **Smallest concept surface.** Option C would add a new session-
   state primitive to the translator and require per-query DuckDB
   ``SET`` semantics; Option A is a single new rewriter module + an
   optional kwarg on one function.

The cost is plumbing ``caller: CallerIdentity | None`` through
five call sites (``jobs/executor.py``, three sites in
``scripting/interpreter.py``, one in ``grpc_api/read_servicer.py``).
Each is additive — the new parameter defaults to ``None``, which
folds to the unauthenticated fallback identity, so the change is
backward-compatible by construction.

### Resolution contract

The pure resolver
:func:`bqemulator.sql.rewriter.session_user.resolve_session_user`
maps a ``CallerIdentity`` to the literal string ``SESSION_USER()``
should return:

| Caller shape | Returned literal |
|---|---|
| ``is_authenticated == False`` (DEFAULT_CALLER) | ``"anonymous"`` (sentinel — RAP filters that compare against a tenant key deny every row) |
| ``user:alice@example.com`` | ``"alice@example.com"`` |
| ``serviceAccount:sa@project.iam.gserviceaccount.com`` | ``"sa@project.iam.gserviceaccount.com"`` |
| ``group:admins@example.com`` | ``"admins@example.com"`` (never appears via ``X-Bqemu-Caller`` in practice; tolerated defensively) |
| ``domain:example.com`` | ``"example.com"`` (same — defensive only) |
| ``allUsers`` / ``allAuthenticatedUsers`` / unknown | raw principal string (passthrough — these are grantee-side identifiers, not caller identifiers) |

### Unauthenticated fallback

Real BigQuery never invokes ``SESSION_USER()`` from an
unauthenticated session — every API call carries a credential. The
emulator's ``DEFAULT_CALLER`` (``user:anonymous@bqemulator.local``
with ``is_authenticated=False``) is purely a robustness fallback for
requests that arrive without an ``X-Bqemu-Caller`` header (most
common: integration tests that don't set the header explicitly).

For RAP filters comparing ``SESSION_USER()`` against a tenant key,
the ``"anonymous"`` sentinel ensures the filter denies every row,
which is the safe default. A documentation note in the
``SESSION_USER`` reference (function-mapping regen) calls out this
behavior so users don't mistake an un-headered test for a real
deny-everything bug.

### Sequencing of the pre-translator passes

``rewrite_session_user`` runs as the **first** pre-translator pass
(immediately after the unsupported-keyword reject), ahead of every
other pre-translator in ``Translator.translate``. The ordering
matters because:

* The row-access enforcement pass
  (``rewrite_for_row_access``) inlines policy filters into the
  user's query *before* the query reaches the translator. By the
  time ``rewrite_session_user`` runs, any ``SESSION_USER()`` inside
  a policy filter is already in the SQL string.
* Downstream pre-translators (``rewrite_safe_helpers``,
  ``rewrite_string_helpers``, ``rewrite_datetime_helpers`` …) all
  expect either valid BigQuery SQL or a partial rewrite — none of
  them see a leftover ``SESSION_USER`` because we substituted it to
  a plain ``STRING`` literal first.

## Rationale

### Why a pre-translator and not a DuckDB pass-through

DuckDB has a built-in ``SESSION_USER`` system function. If the
translator left ``SESSION_USER()`` alone, SQLGlot would transpile it
to DuckDB's ``SESSION_USER`` (verified — see
``tests/unit/sql/rewriter/test_session_user.py::test_translator_substitutes_session_user``
for the regression pin), which resolves to the literal ``'duckdb'``
— the DuckDB connection's OS-side identity, not the BigQuery
caller. Every RAP filter built on ``SESSION_USER()`` would then
silently deny every legitimate caller. Pre-translator substitution
is the only fix.

### Why store the substitution as a ``Literal`` and not a parameterised value

The natural alternative is to substitute ``SESSION_USER()`` with a
positional placeholder (``?``) and bind the email at query-execution
time. That would let DuckDB's parameter-binding layer carry the
type. But the literal approach is simpler:

* The translator already emits string literals for many other
  rewrites (``rewrite_aggregate_variants``, ``rewrite_decimal_literals``,
  etc.). A new ``Literal.string`` node is no special case.
* The pre-translator pass is already running with full caller
  context — there's no late-binding benefit.
* Parameter binding would require coordinating the new parameter
  with the existing positional-parameter pipeline
  (``bind_parameters`` at the bottom of ``jobs/executor.py``);
  literal substitution skips the orchestration entirely.

### Why not also rewrite ``CURRENT_USER`` / ``@@session.user``

BigQuery's ``CURRENT_USER`` is a deprecated legacy spelling of
``SESSION_USER``; ``@@session.user`` is the system-variable form
(useful in stored procedures). Each is functionally identical to
``SESSION_USER`` but adds parsing complexity (system variables in
particular use a different AST shape). Out-of-scope per chip prompt
— each is a separate follow-up PR if downstream users surface a
need.

### Why ``SESSION_USER`` stays excluded from the conformance corpus

ADR 0022 §1.2 documents the exclusion: the function is
session-state-dependent (its return value depends on which
authenticated principal made the API call, which can't be
deterministically captured in a recording). The integration suite
records the substituted SQL (with the literal email) at the
HTTP-request layer, which is reproducible — but the conformance
corpus's recording semantics expect the raw user SQL, not the
post-rewrite SQL. Keep the exclusion; the new unit + integration +
e2e × 4 coverage closes the "exercised at the unit tier" gap that
the surface-inventory entry promised.

## Consequences

### Positive

* The canonical RAP-with-caller-derived-filter production pattern
  works end-to-end through every conformance client.
* The surface-inventory promise ("Exercised at the unit tier") is
  now true — unit + integration + e2e × 4 coverage lands together.
* The pre-translator runs once and substitutes everywhere — no
  separate code path for RAP filters vs free-form queries vs script
  statements vs gRPC Storage Read row-restriction filters.
* The new ``caller`` kwarg on ``Translator.translate`` is the right
  shape for future context-dependent rewrites (e.g. ``@@project_id``
  if we ever add it) — the architecture is now reusable.

### Negative

* Five call sites had to pass ``caller`` (``jobs/executor.py``,
  three in ``scripting/interpreter.py``, one in
  ``grpc_api/read_servicer.py``). Future callers must follow
  the same pattern; documented in the function docstring.
* The Storage Read filter pre-pass at
  ``grpc_api/read_servicer.py:122`` (``_build_filter_sql`` for the
  ``row_restriction`` field) doesn't yet receive caller context —
  it lives below the route handler that resolves identity. A
  ``SESSION_USER()`` in a Storage Read ``row_restriction`` therefore
  folds to ``"anonymous"`` regardless of the actual caller. This is
  unusual (the canonical pattern is RAP filters, not Storage Read
  row_restriction) and is documented in the function docstring as
  a known limitation.
* ``SESSION_USER()`` inside a SQL UDF body is *not* re-substituted
  per-call (UDFs are pre-translated at definition time, when no
  caller exists). The function inside a UDF body folds to
  ``"anonymous"`` permanently — different from real BigQuery,
  which re-resolves the function per-invocation. Documented in
  the rewriter module docstring.

### Neutral

* The new ``CallerIdentity`` import in the translator uses
  ``TYPE_CHECKING`` to avoid a circular import (the
  ``row_access.identity`` module's transitive imports eventually
  pull in the translator); the runtime resolution of the fallback
  identity uses a function-scope ``import`` for the same reason.
  Future contributors touching ``Translator.translate`` should keep
  this shape.
* ``CallerIdentity`` is used in the translator as an opaque value
  type — only ``principal`` and ``is_authenticated`` are read. This
  keeps the translator decoupled from the row-access matcher
  internals.

## Alternatives considered

1. **Option B — RAP-rewriter-only substitution.** Smaller code
   surface (only ``rewrite_for_row_access`` touches caller identity)
   but raises ``UnsupportedFeatureError`` on bare ``SELECT
   SESSION_USER()``. Surprises users who'd reasonably expect the
   function to work in free-form queries. Rejected.
2. **Option C — DuckDB scalar UDF.** Register a Python-side
   ``bqemu_session_user()`` UDF and have the translator rewrite
   ``SESSION_USER()`` → ``bqemu_session_user()``. Per-call state
   would have to plumb through a context variable since DuckDB's
   ``SET SESSION`` is connection-scoped and the emulator shares the
   connection across requests. More invasive than threading
   ``caller`` directly. Rejected.
3. **Per-thread session variable.** Stash the caller in a
   ``contextvars.ContextVar`` and have a DuckDB scalar UDF read it.
   Works in async contexts where each request gets its own context,
   but it adds an implicit dependency that's invisible at the
   translator surface. Rejected — explicit ``caller`` kwarg is
   clearer.
4. **Skip ``SESSION_USER()`` entirely; document as unsupported.**
   Closes the gap quickly but fails the chip prompt's stated goal
   (close the five-client e2e charter for this surface). Rejected.
5. **Hand-authored conformance corpus fixture.** Mentioned in the
   chip prompt as optional; would require a ``${PRINCIPAL}``
   matrix-expander hook the corpus doesn't have today. The
   unit + integration + e2e × 4 tiers cover the surface; the
   conformance corpus exclusion (ADR 0022 §1.2) stands. No new
   fixture added in this PR.

## References

* [BigQuery `SESSION_USER()` reference](https://cloud.google.com/bigquery/docs/reference/standard-sql/security_functions#session_user)
* [ADR 0018](0018-caller-identity-and-row-access-enforcement.md) — the
  ``CallerIdentity`` dataclass + ``X-Bqemu-Caller`` header contract this ADR builds on.
* [ADR 0022](0022-conformance-corpus-design.md) §1.2 / §7 — the
  conformance-corpus exclusion for non-deterministic functions, which
  ``SESSION_USER()`` stays inside.
* [ADR 0035](0035-code-quality-gates.md) / [ADR 0036](0036-complexity-ratchet-to-c.md) /
  [ADR 0037](0037-openssf-scorecard.md) — the template / shape this ADR follows.
* AGENTS.md "Testing expectations" — the five-client e2e charter
  this ADR satisfies for the SESSION_USER surface.
