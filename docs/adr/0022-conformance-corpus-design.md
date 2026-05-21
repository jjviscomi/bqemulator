# ADR 0022: Conformance corpus selection criteria, divergence policy, and tolerance contract

- **Status**: Accepted
- **Amendment**: §3 STRING tolerance now routes WKT-shaped
  values through the GEOGRAPHY whitespace-normalisation rule. See
  ADR 0023 §1.H closure note for the rationale.
- **Amendment**: §3 STRING tolerance now also routes
  JSON-shaped values (stripped value opens with ``{`` or ``[``)
  through a ``json.loads`` parse-equal check. Closes the
  ``ST_AsGeoJSON`` output formatting divergence (scope-expansion #18,
  reconsidered from ``out-of-scope.md``). Genuine semantic differences
  in JSON content still surface as mismatches; only key-order,
  ``int``/``float`` and whitespace drift are absorbed.
- **Amendment**: §3 grows a new ``Error parity``
  subsection. A fixture's ``expected.json`` may carry an optional
  ``error`` envelope alongside (and exclusive of) the existing
  ``schema`` + ``rows`` payload. The runner branches on the field's
  presence: success fixtures (no ``error``) follow the row + schema
  diff; error fixtures expect the emulator to raise a matching
  ``GoogleAPIError`` and diff ``reason`` / ``location`` /
  ``http_status`` (exact equality) plus a regex match against a
  recorded ``message_pattern``. The recorder is the sole producer
  of error envelopes — Phase 11 non-negotiable #8 still binds.
  ``fixture_version`` bumps to **2** for newly-recorded fixtures;
  pre-existing v1 payloads stay backward-compatible (no ``error``
  field ⇒ runner treats as success-expected). Closes P3.a in
  `docs/roadmap/v1-confidence-plan.md`.
- **Amendment**: §2 relaxes the
  "``${DATASET}`` is the only placeholder" rule to allow five
  additional UPPER-CASE placeholders. The fixture directory grows
  two optional files beyond ``setup.sql`` + ``query.sql`` +
  ``expected.json``: ``setup_rest.json`` (ordered list of REST
  API operations) and ``headers.json`` (per-canonical-query HTTP
  headers). New placeholders ``${PROJECT}`` and ``${DATASET_ID}``
  carry the split halves of ``${DATASET}`` for REST URL templates;
  ``${PRINCIPAL}`` and ``${GROUP}`` carry IAM-member identities so
  the recorder can substitute the operator's real ADC identity
  (via ``BQEMU_CONFORMANCE_PRINCIPAL`` / ``BQEMU_CONFORMANCE_GROUP``
  env vars) while the runner uses a deterministic placeholder.
  ``${OTHER_PRINCIPAL}`` (added during P2.d recording)
  carries a real-but-non-caller IAM member for "denied"-pattern
  fixtures, because real BigQuery validates RAP grantees and
  rejects fake placeholders like ``user:nobody@example.com``;
  recorder reads ``BQEMU_CONFORMANCE_OTHER_PRINCIPAL`` (typically
  the project's default compute service account).
  The substituter still rejects unknown tokens at runtime. The
  recorder and runner both auto-track datasets created via
  ``POST /bigquery/v2/projects/<p>/datasets`` and delete them on
  teardown so secondary datasets used by authorized-view fixtures
  don't leak. Closes the framework half of P2.d in
  `docs/roadmap/v1-confidence-plan.md`;
  recording (18 of 20 fixtures) landed.
- **Amendment**: pre-translator rewriter at
  [`src/bqemulator/sql/rewriter/create_table_schema_ctas.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/create_table_schema_ctas.py)
  closes a BigQuery parity gap surfaced during P2.d recording: real
  BigQuery accepts ``CREATE [OR REPLACE] TABLE x (schema) AS
  SELECT …`` in one statement, but DuckDB's parser rejects the
  combined form. The rewriter strips the schema clause and wraps
  each SELECT projection in ``CAST(<value> AS <declared-type>)``
  so the bare CTAS form DuckDB accepts produces a table with the
  user's declared column types. One conformance fixture at
  [`tests/conformance/sql_corpus/rest_crud/ctas_with_schema_clause/`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/rest_crud/ctas_with_schema_clause)
  recorded against real BigQuery pins the wire-format parity.
- **Amendment**: §8 grows a seventh ``TIMEZONE``
  variation tag. Workstream **P8.e** found that zero pre-P8.e
  fixtures touched ``AT TIME ZONE``, and timezone arithmetic is
  one of the most operationally critical and divergence-prone
  surfaces (BQ uses IANA zone names natively; DuckDB's ICU build
  is uneven, and ``EXTRACT(part FROM ts AT TIME ZONE 'X')``
  applies the zone to ``part`` extraction in BQ but treats the
  zone as a render hint in DuckDB). The seven-tag set is again
  locked; see §8 below for the detection contract.

## Context

Phase 11's ship criterion includes a conformance corpus — a set of
canonical queries whose recorded output from real BigQuery is the
ground-truth baseline the emulator is diffed against. The corpus
needs to be large enough to exercise the shipped SQL surface
meaningfully but disciplined enough that "85% pass rate" means
something concrete:

1. Which queries qualify for inclusion?
2. Which queries do we expect to *not* match real BigQuery, and how
   are those divergences declared?
3. What tolerance is applied to each scalar type before the runner
   declares a row mismatch?
4. How is "recorded against real BigQuery" enforced — what stops a
   developer from hand-tuning ``expected.json`` until the emulator
   passes?
5. How does the corpus deal with queries whose output is inherently
   non-deterministic (``CURRENT_TIMESTAMP()``, ``RAND()``, session
   state)?

Slice 1 (chaos tier) shipped its ADR 0021 design-contract; the
conformance tier needs the equivalent before slice 2 closes.

## Decisions

### 1. Inclusion criteria — three properties every fixture must have

A query qualifies for the corpus iff:

1. **It exercises a surface in Phases 1–9** — the static-SQL surface
   the v1.0.0 release commits to. Phase 10 (admin / import / export
   / backup) is metadata HTTP, not SQL, and has no conformance
   counterpart. Phase 11's own scope (perf / chaos / fuzz) is not in
   the corpus by construction.
2. **Its output is fully determined by the recorded SQL alone** — no
   ``CURRENT_TIMESTAMP()``, ``CURRENT_DATE()``, ``RAND()``,
   ``SESSION_USER()``, ``GENERATE_UUID()``, ``BQ.JOBS`` reads, or
   any other wall-clock / session-state input. Time-travel queries
   that reference relative timestamps (``FOR SYSTEM_TIME AS OF
   TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 SECOND)``) are
   excluded; static time-travel against a literal pre-seeded
   snapshot stays in scope but is also excluded in this slice
   because of the same setup-determinism issue.
3. **Its setup is idempotent.** ``setup.sql`` uses
   ``CREATE OR REPLACE TABLE`` and INSERT statements that produce
   the same final table state on every run; the recorder discards
   the per-fixture dataset between fixtures so prior state never
   leaks.

A consequence of (2) is that admin endpoints, IAM-policy stores,
job-history queries, and dry-run cost estimation are excluded from
the corpus. They are exercised in unit / integration / E2E tiers
where determinism is handled by the test harness rather than the
fixture file.

### 2. ``${DATASET}`` is the only placeholder

> **Amendment (P2.d).** The original rule was *one
> placeholder only*. The Phase 8 row-access fixtures need REST URL
> templates and caller-identity values that the
> ``project.dataset`` form cannot express, so the substituter
> additionally accepts ``${PROJECT}``, ``${DATASET_ID}``,
> ``${PRINCIPAL}``, ``${GROUP}``, and ``${OTHER_PRINCIPAL}`` (the
> last added during recording when real BigQuery rejected fake
> ``user:nobody@example.com`` grantees). See the
> ``Caller identity and REST setup (P2.d)`` subsection
> below for the full contract. The decision below explains the
> original constraint and is kept for the audit trail.

Each fixture's ``query.sql`` and optional ``setup.sql`` may reference
``${DATASET}`` exactly where a fully-qualified ``project.dataset``
name is needed. The runner and recorder substitute this token with
a per-fixture temp dataset on the side they're targeting. Any other
``${...}`` placeholder is a fixture-authoring bug and raises at
substitution time (the substitution function in
``tests/conformance/_corpus.py`` rejects unknown tokens).

Options considered:

1. **No placeholder; hard-code dataset names** — rejected, because
   the recorder and runner target different projects (real BigQuery
   vs the emulator) and need different dataset names.
2. **Multiple placeholders** (``${PROJECT}``, ``${DATASET}``,
   ``${RUNID}``) — rejected at the original decision,
   because the corpus did not need to distinguish project from
   dataset (a fully-qualified ``project.dataset`` is one
   substitution) and runs always isolate via dataset, not project.
   This rejection was **revisited** when Phase 8
   row-access fixtures showed REST URL templates need the split
   forms; ``${PROJECT}`` and ``${DATASET_ID}`` are now accepted
   alongside ``${DATASET}``.
3. **``${DATASET}`` only (originally selected; later relaxed)**
   — one placeholder, one substitution rule, easy to reason about.
   Encoded into the substitution function so a typo
   (``${dataset}`` lower-case) fails loudly.

#### Caller identity and REST setup (P2.d)

Phase 8 RAP enforcement and authorized-view delegation cannot be
expressed in pure SQL on the emulator (the emulator manages
``rowAccessPolicies`` and dataset ``access`` arrays via REST, not
DDL). Two new optional per-fixture files extend the corpus shape:

- ``setup_rest.json`` — a top-level JSON list of REST operations
  applied **after** ``setup.sql``. Each entry has ``method``,
  ``path``, and an optional ``body``; placeholders inside both the
  path and the body are expanded recursively. The recorder issues
  these against ``https://bigquery.googleapis.com`` via the BQ
  client's ``AuthorizedSession`` (so ADC is applied automatically);
  the runner issues them against the in-process emulator via
  plain ``httpx`` (no auth required). Both sides track any
  ``POST /bigquery/v2/projects/<p>/datasets`` operations and tear
  the secondary datasets down on teardown so authorized-view
  fixtures that span two datasets don't leak.
- ``headers.json`` — a top-level JSON object whose entries are
  HTTP header name → value pairs applied to the **canonical**
  ``query.sql`` request only (setup steps run under the default
  identity). The runner constructs an ``AuthorizedSession`` with
  the headers and passes it to the BigQuery client as ``_http``;
  real BigQuery ignores the ``X-Bqemu-*`` headers because BigQuery
  uses ADC.

Five new placeholders join ``${DATASET}``:

- ``${PROJECT}`` and ``${DATASET_ID}`` — the split halves of the
  ``project.dataset`` form. Used by ``setup_rest.json`` URL paths
  (``/bigquery/v2/projects/${PROJECT}/datasets/${DATASET_ID}/...``).
- ``${PRINCIPAL}`` — IAM-member string for caller identity. The
  recorder substitutes with ``BQEMU_CONFORMANCE_PRINCIPAL`` (its
  ADC identity); the runner substitutes with
  ``user:alice@example.com`` by default. This is the mechanism
  that lets the recorded baseline match the emulator's RAP
  enforcement under different identities — both sides see the
  same grantee string in the policy AND the same caller string in
  the header, so the same rows are returned.
- ``${GROUP}`` — IAM ``group:`` member used by group-grantee
  fixtures. Recorder reads ``BQEMU_CONFORMANCE_GROUP``; runner
  defaults to ``group:engineering@example.com``.
- ``${OTHER_PRINCIPAL}`` — IAM-member for "denied"-pattern
  fixtures where the policy grants a non-caller principal so the
  caller sees zero rows (added during recording when
  real BigQuery rejected the original ``user:nobody@example.com``
  placeholder because BQ validates grantees as real IAM
  principals). Recorder reads ``BQEMU_CONFORMANCE_OTHER_PRINCIPAL``
  (typically the project's default compute service account);
  runner defaults to ``serviceAccount:other@example.com``.

The substituter still rejects unknown tokens; any
``${...}`` outside this set is a fixture-authoring bug and raises.

This relaxation does **not** apply to the broader corpus — fixtures
outside ``row_access/`` MAY use the new placeholders but
have no reason to (the legacy ``${DATASET}`` form is sufficient).
The decision is recorded as a relaxation rather than a rewrite to
preserve the original rationale for the audit trail.

#### Query parameters (P2.e)

Production apps issue queries through
``QueryJobConfig.query_parameters``, which the BQ Python client
serialises to the REST ``QueryRequest.queryParameters`` body field.
The original corpus shape had no way to exercise this wire-format —
every fixture's ``query.sql`` was submitted as a plain string. A
single new optional per-fixture file extends the corpus shape:

- ``parameters.json`` — a top-level JSON object with two required
  keys:
 - ``"mode"`` — ``"named"`` (``@<name>`` placeholders) or
   ``"positional"`` (``?`` placeholders).
 - ``"parameters"`` — a list of parameter entries. Each entry
   carries ``"type"`` (a scalar type name like ``"INT64"`` or a
   compound dict like ``{"type": "ARRAY", "arrayType": {...}}``)
   and ``"value"`` (a JSON-native value or ``null`` for typed
   NULLs). Named entries also carry ``"name"``.

Both the recorder and the runner build the parameters into a
``bigquery.QueryJobConfig`` via the shared
[`tests/conformance/_parameters.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/_parameters.py)
helper and pass it to ``client.query(sql, job_config=...)``. The
BQ Python client serialises the parameters to the REST body
field, so the wire-format is exercised end-to-end on both sides.
**String-interpolating parameter values into the SQL is forbidden**
— it would defeat the entire point of this fixture surface.

Placeholders inside parameter values (``${PRINCIPAL}`` etc.) pass
through ``substitute_in_json`` for consistency with
``setup_rest.json``; none of the P2.e fixtures use this but the
plumbing is symmetric so a future parameter-bound RAP fixture can
reference ``${PRINCIPAL}`` directly.

### 3. Tolerance contract — per-type matching rules

The comparison helper in ``tests/conformance/_comparison.py`` applies
these tolerances:

| Type | Rule | Rationale |
|---|---|---|
| ``INT64`` / ``BOOL`` / ``STRING`` / ``BYTES`` / ``DATE`` | Exact equality. | No representational drift possible. |
| ``STRING`` (WKT-shaped) | WKT whitespace normalisation, same as the ``GEOGRAPHY`` rule. | DuckDB's ``ST_AsText`` inserts a space between the geometry-type keyword and the opening paren (``POINT (1 2)``); BigQuery's omits it (``POINT(1 2)``). The value is declared ``STRING`` on the wire, not ``GEOGRAPHY`` — so the helper detects WKT shape (``^(POINT\|LINESTRING\|POLYGON\|MULTIPOINT\|MULTILINESTRING\|MULTIPOLYGON\|GEOMETRYCOLLECTION)\s*\(``, case-insensitive, anchored) and applies the same whitespace + capitalisation normalisation as the ``GEOGRAPHY`` rule. Closes ADR 0023 §1.H without changing the emulator's WKT output. |
| ``STRING`` (JSON-shaped) | ``json.loads`` parse-equal: both sides are parsed and compared with Python's unordered ``==``. | DuckDB-spatial's ``ST_AsGeoJSON`` emits ``{"coordinates": [3.0, 4.0], "type": "Point"}`` where BigQuery emits ``{ "type": "Point", "coordinates": [3, 4] } `` — semantically equivalent JSON objects but with different key order, ``int`` vs ``float`` coordinates, and inter-token whitespace. A STRING-typed value whose stripped form opens with ``{`` or ``[`` is treated as JSON-shaped and parses through ``json.loads``; the parsed objects compare with Python's unordered ``==`` (which treats ``3`` and ``3.0`` as equal). Either side failing to parse falls back to exact equality so a malformed-JSON divergence still surfaces. Closes scope-expansion #18 (GeoJSON output formatting, reconsidered from ``out-of-scope.md``). |
| ``NUMERIC`` / ``BIGNUMERIC`` | Decimal equality after parsing both sides through:class:`decimal.Decimal`. | Catches trailing-zero serialisation differences (``"1.0"`` ≡ ``"1"``). |
| ``FLOAT64`` | ``math.isclose(rel_tol=1e-12, abs_tol=1e-15)``. | IEEE-754 doubles share representation but differing compute paths (DuckDB vs BigQuery) can yield ULP-scale drift; ``rel_tol=1e-12`` is tight enough to catch real divergences while absorbing harmless last-bit noise. |
| ``TIME`` / ``DATETIME`` / ``TIMESTAMP`` | ``abs(a - b) ≤ 1 µs``. | BigQuery's wire format is microsecond-precision; sub-microsecond drift would imply a real precision divergence. |
| ``GEOGRAPHY`` | WKT string equality after whitespace normalisation. | DuckDB-spatial and BigQuery both emit canonical WKT; whitespace and capitalisation differ but coordinate values do not — unless the query is spheroidal (see #4). |
| ``RANGE`` | Equality on the ``{"start", "end"}`` JSON shape. | RANGE is a STRUCT under the wire; both sides serialise it the same way once inner DATE/DATETIME/TIMESTAMP are normalised. |
| ``INTERVAL`` | Canonical ``YEAR TO SECOND`` string equality. | Both sides normalise compound intervals to the same canonical form. |
| ``JSON`` | Parsed equality (``json.loads`` both sides, then ``==``). | Whitespace and key-order drift is uninteresting; semantic equality is what matters. |
| ``ARRAY`` | Length equality + ordered element-wise comparison using the element type's rule. | BigQuery preserves array order; arrays returned in different orders are a real semantic divergence. |
| ``STRUCT`` | Per-field comparison using each field's declared type. | Same logic as scalars, recursive on declared sub-fields. |

Options considered for WKT-shaped STRING (added with the
Bucket H closure):

1. **Emulator-side fix — patch DuckDB-spatial's WKT formatter** —
   rejected, because the fix would land in a third-party C++
   extension on an unpredictable schedule and force the emulator to
   pin to a future DuckDB version. The conformance contract is the
   right layer for a pure stringification difference.
2. **Emulator-side fix — wrap every ``ST_*`` output in a Python UDF
   that re-formats the WKT** — rejected, because (a) the cost is
   per-row VARCHAR rewriting on every spatial query, (b) DuckDB's
   own WKT output already round-trips through every spatial function
   correctly within the engine, and (c) the comparison layer is
   already responsible for tolerance normalisation; spreading it
   into the SQL pipeline would split the contract.
3. **Comparison-helper extension (selected)** — detect WKT-shaped
   STRING values via an anchored type-keyword regex and route them
   through the existing ``_normalise_wkt`` helper. The regex is
   tight (one of seven WKT type keywords followed by an optional
   whitespace then ``(``) so unrelated STRING columns are
   untouched.

Options considered for JSON-shaped STRING (added with
the scope-expansion #18 GeoJSON closure):

1. **Bundle a custom GeoJSON formatter that emits BigQuery's exact
   shape** — rejected, because (a) BigQuery's formatter behaviour
   isn't precisely documented (the spaces, key order, and integer
   vs float coord output were inferred from recorded outputs), (b)
   maintaining a Python re-implementation that perfectly mirrors a
   third-party service's serialisation creates ongoing drift risk,
   and (c) the comparison layer already owns this kind of tolerance
   contract for other types (FLOAT64 ULP drift, GEOGRAPHY WKT
   whitespace).
2. **Relax the schema comparator to accept STRING ≡ JSON** —
   rejected, because the schema-type alias would mask real wire-
   format-type divergences elsewhere (e.g., a column that genuinely
   should be STRING but the emulator surfaces as JSON would silently
   pass). The SQL-pipeline fix (``StAsGeoJsonStringTypeRule`` wraps
   ``ST_AsGeoJSON(g)`` in ``CAST(... AS VARCHAR)``) keeps the
   schema-type check strict.
3. **SQL-pipeline CAST wrap + comparison-helper parse-equal
   (selected)** — the ``StAsGeoJsonStringTypeRule`` translation rule
   forces the wire-format schema to STRING (matching BigQuery), and
   the comparison helper detects JSON-shaped STRING content
   (stripped value opens with ``{`` or ``[``) and applies
   ``json.loads`` parse-equal. Genuine semantic divergence in JSON
   still surfaces — only shape-level rearrangement is absorbed.

Options considered for FLOAT64:

1. **Bit-exact equality** — rejected, because two correct
   implementations can produce different last-bit values from the
   same input (e.g., FMA vs separate mul-add).
2. **``math.isclose(rel_tol=1e-9)``** — rejected, because the
   relative tolerance is too loose and could mask real divergences
   on small-magnitude floats.
3. **``rel_tol=1e-12, abs_tol=1e-15`` (selected)** — ULP-scale on
   IEEE-754 doubles. Absorbs harmless compute-path noise; surfaces
   any divergence worth investigating.

#### Error parity (added with P3.a)

A fixture whose ``expected.json`` carries an ``error`` envelope is
expected to *fail* against the emulator with a matching error shape.
The fixture shape is::

 {
 "fixture_version": 2,
 "recorded_at": "2024-…",
 "bigquery": { "project": …, "job_id": …, "location": …,
 "total_bytes_processed": 0,
 "total_bytes_billed": 0,
 "duration_ms": … },
 "error": {
 "reason": "invalidQuery",
 "location": "query",
 "http_status": 400,
 "message_pattern": "Syntax error: Unclosed parenthesis at \\[\\d+:\\d+\\]",
 "message_sample": "Syntax error: Unclosed parenthesis at [1:15]"
 },
 "duration_class": "fast"
 }

The runner branches on the optional ``error`` field. When present:

* The emulator-side query is expected to raise
  ``google.api_core.exceptions.GoogleAPIError``. The runner catches
  the error, normalises it via:func:`tests.conformance._comparison.extract_actual_error`,
  and diffs:
 * ``reason`` — exact equality. BigQuery's ``ErrorProto.reason`` is
   a closed enum (``invalidQuery``, ``notFound``, ``duplicate``,
   ``outOfRange``, ``invalid``, …); semantic equivalence between
   two reasons does not exist.
 * ``location`` — exact equality. BigQuery sets this to the
   structural element that failed validation (``query``,
   ``jobReference.projectId``, …) or omits it.
 * ``http_status`` — exact equality. Client try/except patterns
   key off the HTTP code.
 * ``message`` — regex match against ``message_pattern`` via
   ``re.search`` (DOTALL mode so multi-line messages survive). The
   recorder writes the pattern with the per-fixture dataset FQDN
   substituted to a dataset-shaped wildcard (``[\w\-\.:]+``) and
   line:column markers (``[12:34]``) substituted to a digit-range
   pattern. ``message_sample`` carries the raw recorded BigQuery
   wording for audit and survives re-recordings unchanged in
   structure.
* If the emulator succeeds where BigQuery raised (or raises where
  BigQuery succeeded), the test fails with a clear "kind mismatch"
  message before any per-field diff runs.

When absent (the existing 644 v1 fixtures): the runner runs the
existing rows + schema diff. The framework is backward-compatible
with v1 by construction — only newly-recorded fixtures carry the
``error`` field, and only newly-recorded fixtures bump
``fixture_version`` to 2.

Options considered for the error envelope (added with
P3.a):

1. **Match BigQuery's error JSON wire-format byte-for-byte** —
   rejected, because the BQ error JSON envelope includes volatile
   fields (``debugInfo``, retry hints, structured error details that
   reference internal Google product names) that drift across BQ
   service versions even when the user-facing error is stable. A
   four-field exact-shape contract gives clean semantics.
2. **Literal-string match on the error message** — rejected,
   because BigQuery's message wording is documented to vary by
   account, region, and date. The regex pattern with dataset and
   line:column wildcards is the right level of abstraction.
3. **Mark error fixtures in a separate registry instead of a
   payload field** — rejected, because every other corpus-discovery
   path (recorder, runner, divergences) is rooted in ``expected.json``
   shape. A separate registry would split the contract across two
   files and require a re-record run to keep the two in sync.
4. **Optional ``error`` field on ``expected.json``, branching at
   runner load time (selected)** — keeps all fixture metadata in
   one file; runner-side branch is two lines; backward-compatible
   with the 644 v1 fixtures; ``fixture_version`` bump documents the
   shape change without forcing a re-record of pre-existing
   payloads.

### 4. Known divergences live in ``divergences.py``

Every fixture expected to diverge from real BigQuery has an entry in
``tests/conformance/divergences.py`` mapping its id (the
``<phase>/<name>`` form) to a rationale string rooted in an ADR or
in ``docs/reference/out-of-scope.md``. The runner reads this dict
and attaches ``@pytest.mark.xfail(strict=True, reason=…)`` to the
corresponding parametrised test.

``strict=True`` is load-bearing. It means:

* If the emulator *and* real BigQuery agree (xfail "unexpected pass"):
  the test fails. This signals that the divergence we documented no
  longer exists — the entry should be removed.
* If the emulator and real BigQuery disagree (xfail expected fail):
  the test passes. The divergence is still real.

This gives the corpus a *forcing function*: an emulator improvement
that closes a known divergence shows up as an unexpected-pass
failure that the next slice cleans up.

Initial divergences (slice 2):

| Fixture id | ADR / scope link |
|---|---|
| ``specialized_types/st_distance_continental`` | ADR 0019; spheroidal-vs-planar |
| ``specialized_types/st_area_continental`` | ADR 0019; spheroidal-vs-planar |
| ``specialized_types/st_length_continental`` | ADR 0019; spheroidal-vs-planar |
| ``specialized_types/st_perimeter_continental`` | ADR 0019; spheroidal-vs-planar |
| ``specialized_types/st_buffer_continental`` | ADR 0019; spheroidal-vs-planar |

The list grows as the recorder reveals new divergences during
slice 2 and shrinks as future slices close them.

### 5. Pass-rate gate — Option A (per-fixture xfail with ``strict=True``)

Slice 2's prompt offered two pass-rate gate options. We pick
**Option A**: each divergent fixture is xfail'd with ``strict=True``;
the conformance pytest run is green iff zero non-xfail'd fixtures
fail AND zero xfail'd fixtures pass.

We rejected Option B (a 15%-tolerant wrapper script) because:

* The phase-level review can't tell which 15% are divergent vs
  flaky vs broken without consulting per-fixture annotations
  anyway.
* Option A surfaces emulator improvements (a closed divergence
  becomes an "unexpected pass") without an opt-in re-grade.
* The standard pytest tooling already understands
  ``xfail(strict=True)``; no custom gating logic is required in CI.
* The 85% ship criterion in the Phase 11 doc is the floor for
  pass-rate of the *non-divergent* portion of the corpus — i.e.,
  ≥85% of the queries we expect to match must in fact match. Option
  A lets pytest enforce that directly: a non-divergent fixture
  that fails fails the suite.

### 6. Recorder is the sole producer of ``expected.json``

Phase 11 non-negotiable #8 says "Conformance values recorded against
real BigQuery, not invented." We operationalise this by:

* The recorder writes the BigQuery ``job_id`` of the producing job
  into the fixture's payload. The runner reads it for the
  diagnostic message but does not validate against a registry of
  trusted job ids — instead, anybody auditing the corpus can
  ``grep "job_id"`` to confirm a real job stands behind every
  baseline.
* The recording script logs every (fixture, job_id, bytes,
  duration_ms) tuple at INFO level so a recording session is
  reproducible from its log alone.
* Re-recording requires ``--force`` so a stray run doesn't silently
  drift baselines.
* The recorder enforces a per-fixture byte-scan cap (default 1 GiB)
  before writing. A fixture that would exceed the cap is logged
  and skipped — *no* ``expected.json`` is written, so a re-run
  notices the gap.
* Error fixtures (P3.a): when BigQuery raises a
  ``GoogleAPIError`` during the canonical ``query.sql`` (setup
  failures are still recorder bugs, not recordable outcomes), the
  recorder writes an ``error`` envelope to ``expected.json``
  instead of ``schema`` + ``rows``. The recorder rewrites the
  caught BigQuery message into a stable regex
  (:func:`scripts.record_conformance_fixtures._build_message_pattern`)
  — substituting the per-fixture dataset FQDN to a wildcard and
  collapsing line:column markers to a digit-range pattern — so
  re-recordings against a different BQ project do not require an
  author to refresh the pattern by hand. The raw recorded message
  survives in ``error.message_sample`` for audit.

### 7. Time-dependent queries are excluded by design

The conformance tier exercises the static SQL surface. Queries that
depend on wall-clock state — ``CURRENT_TIMESTAMP()``,
``CURRENT_DATE()``, ``RAND()``, ``SESSION_USER()``,
``GENERATE_UUID()``, ``FOR SYSTEM_TIME AS OF`` with a relative
timestamp — would have non-reproducible baselines and so cannot live
in the corpus.

The dynamic time-travel surface is exercised at the integration tier
(``tests/integration/test_time_travel.py``); session state is
exercised at the unit tier; randomness is property-tested via
Hypothesis.

This is a clean exclusion, not deferral: future re-record attempts
on such a fixture would always drift, and pinning a snapshot in
``expected.json`` would defeat the corpus's "recorded against real
BigQuery" contract.

### 8. Variation taxonomy — seven locked tags

Amendment (workstream **P8.a**) after the conformance-depth
audit found that **63% of deterministic surface items sit in the 🟡
Sampled tier** (1 to 2 fixtures each): wire-shape and result-value
verified for the happy path, but the typical BigQuery-vs-DuckDB
divergence (NULL propagation, empty inputs, ±Inf / NaN, timezone
arithmetic, Unicode case-folding, error-shape parity) lives in
scenarios a one-or-two-fixture smoke test reliably misses. The
[conformance coverage matrix](../reference/conformance-coverage-matrix.md)
measured fixture count per surface item — broad exposure — but
carried no signal about **what kind of variation** the fixtures
exercised.

> **Amendment (workstream P8.e).** A seventh ``TIMEZONE``
> tag joined the locked set when timezone arithmetic became its own
> variation-depth surface. The P8.a six-tag set treated
> "timezone arithmetic" as one of several divergence-prone surfaces
> the *boundary_value* / *null_input* heuristics indirectly captured,
> but the conformance audit run at the end of P8.d found that **zero
> pre-P8.e fixtures touched ``AT TIME ZONE``** and the implicit
> coverage assumption was unsafe. The P8.e sweep (20 fixtures) is
> the first deliberate dive into BigQuery's timezone semantics and
> requires a dedicated tag so the matrix's variation-depth report
> tracks the surface independently.

Every fixture is now classified into one or more of **seven locked
variation tags** in
[`tests/conformance/_corpus.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/_corpus.py):

| Tag | Means | Detection heuristic |
|---|---|---|
| `happy_path` | the surface's primary use case, no nulls / empties | default; no other tag fires |
| `null_input` | at least one input is NULL or the query asserts NULL semantics | fixture name contains ``null`` OR ``query.sql`` matches ``\bIS NULL\b | \bIS NOT NULL\b | \bNULL\b`` |
| `empty_input` | an empty array literal, empty string, or ``LIMIT 0`` source | fixture name contains ``empty`` OR query has ``LIMIT 0`` OR ``[]`` literal OR ``''`` literal |
| `boundary_value` | min / max integer, ±Inf, NaN, overflow | fixture name token in {``max``, ``min``, ``inf``, ``nan``} OR fixture name substring in {``boundary``, ``overflow``} OR query has an integer literal ≥ 15 digits OR ``'inf(inity)?'``/``'nan'`` string literal |
| `unicode` | non-ASCII identifier, value, or collation | fixture name contains ``unicode`` OR query has any non-ASCII codepoint |
| `error_path` | ``expected.json`` carries an ``error`` envelope (recorded BQ error) | ``expected.json`` has top-level ``error`` key (the P3.a error-shape contract) |
| `timezone` | the fixture exercises an IANA zone name, a numeric offset literal, or the ``AT TIME ZONE`` operator | fixture name starts with ``tz_`` OR query matches ``\bAT\s+TIME\s+ZONE\b`` OR query carries an ``'Area/Location'``-shaped IANA literal OR query carries a ``'[+-]HH:MM'`` offset literal |

A single fixture can carry multiple tags. ``HAPPY_PATH`` is
mutually exclusive with the other five — it fires only when *no*
other tag matches, so every fixture is classified into at least
one bucket.

The classifier
:func:`tests.conformance._corpus.classify_variation` returns a
``frozenset[VariationTag]`` and is **pure I/O-free** in its
runtime model: it reads only fields already on the
:class:`tests.conformance._corpus.Fixture` instance (name,
``query_sql``) plus the on-disk ``expected.json`` for the
``error_path`` check. No DuckDB, no parser, no network.

**Boundary keyword tokenisation.** The boundary heuristic uses
exact snake-case token matching for ``max`` / ``min`` / ``inf`` /
``nan`` (and substring matching for the long-enough-to-be-safe
``boundary`` / ``overflow``). The token convention avoids the most
egregious substring false positives: ``information_schema`` does
**not** match ``inf``, ``unterminated`` does **not** match ``min``,
``maxdistance`` does **not** match ``max``. Some false positives
remain (``select_min_max`` exercises MIN / MAX aggregates yet
gets tagged ``boundary_value``); these are accepted noise — the
report's purpose is to surface broad-but-shallow surfaces, and
over-tagging a happy-path fixture as boundary only narrows the
report's candidate list, not widens it.

**The seven tags are locked.** Adding an eighth tag requires an
ADR amendment. The frozen set keeps the matrix's Variation column
compact (so a row fits one line in PR review) and prevents
fixture-author indecision over which bucket a new fixture belongs
in. When no existing tag fits, fall back to ``happy_path`` and let
fixture *depth* — not tag breadth — capture the new variation. The
P8.e seventh-tag amendment is the only growth the set has seen
since P8.a's six-tag baseline and was justified by the conformance
audit finding zero pre-P8.e fixtures touched ``AT TIME ZONE``.

**The matrix surfaces both axes.** The auto-generated coverage
matrix now carries:

1. A per-row **Variation** column on every per-category table,
   rendering the histogram as ``happy×3 / null×1 / empty×1`` etc.
2. A top-level **"Variation depth — broad-but-shallow surfaces"**
   report enumerating every deterministic surface item with
   **≥ 3 fixtures** whose union of variation tags is exactly
   ``{HAPPY_PATH}``. These are the surfaces that *look* well
   covered by fixture count but reliably miss the typical
   divergence — the picklist workstreams P8.b through P8.e
   read when authoring edge-case fixtures.

The threshold of 3 was picked to exclude surfaces already
flagged as 🟡 Sampled (1–2 fixtures) or 🔴 Uncovered (0) by the
existing depth tier — those rows already carry a "needs more
fixtures" signal; flagging them again for variation gaps would
double-count the noise.

## Consequences

- **Positive.** The 85% pass-rate gate has a concrete meaning: at
  least 85% of non-divergent fixtures pass on the emulator. Every
  divergence below that line is a real engineering miss; the rest
  are catalogued in ``divergences.py`` with ADR-backed reasons.

- **Positive.** Hand-editing ``expected.json`` is a one-line
  ``grep`` away from being caught (no ``job_id`` ⇒ not recorded).
  The recorder is the only path, by construction.

- **Positive.** The ``${DATASET}`` placeholder makes fixtures
  bidirectional: the same SQL files work against real BigQuery (via
  the recorder) and against the emulator (via the runner) without
  per-side branches.

- **Negative.** Spheroidal-vs-planar GEOGRAPHY divergences are
  permanent under ADR 0019 — we can't ever close them without
  shipping s2geometry, which is out-of-scope for v1.0.0. The
  conformance tier therefore caps at a ceiling slightly below 100%.

- **Negative.** Re-recording requires a working
  ``GOOGLE_APPLICATION_CREDENTIALS`` and a project the operator can
  bill. The slice-2 recording lived in
  ``your-bigquery-project`` and cost an estimated $1 (most
  fixtures scan kilobytes; the byte-scan cap stops a runaway
  query before it bills).

- **Negative.** The corpus does not cover the dynamic surface
  (time, RNG, session). That surface is exercised in adjacent tiers
  — but a reader who searches the corpus for ``CURRENT_TIMESTAMP``
  will find no fixtures and might miss that the emulator does
  implement it. The compatibility matrix (slice 7) will cover this
  gap.

## Implementation notes

- The corpus discovery / placeholder substitution / row encoding
  helpers live in ``tests/conformance/_corpus.py``,
  ``tests/conformance/_comparison.py``, and
  ``tests/conformance/_row_encoding.py``. They are imported by both
  the runner (``test_corpus.py``) and the recorder
  (``scripts/record_conformance_fixtures.py``) so the two sides
  produce identical JSON.
- Error-shape parity (P3.a) lives in
  ``tests/conformance/_comparison.py``'s :func:`compare_error` and
  :func:`extract_actual_error`; the recorder's matching helpers are
  ``_build_message_pattern`` and ``_build_error_payload`` in the
  recorder script. Unit coverage is in
  ``tests/unit/conformance/test_compare_error.py`` and
  ``tests/unit/conformance/test_recorder_error_payload.py``
  (32 cases pinning extraction, comparison, pattern synthesis, and
  envelope assembly).
- **Recording is a local action**, never run from CI. The operator
  invokes ``scripts/record_conformance_fixtures.py`` on their
  workstation against a real BigQuery project they control,
  reviews the diff in their editor, and commits the changed
  ``expected.json`` files in a normal PR. The CI workflow
  ``.github/workflows/conformance.yml`` runs only the runner
  against the in-process emulator and stores no GCP credentials.
- ``Makefile``'s ``test-conformance`` target still requires
  ``GOOGLE_APPLICATION_CREDENTIALS`` to be set on local invocation
  — preserving the invariant that *local* re-record runs are
  opt-in and intentional, even though CI itself never reads
  credentials.

## References

- [Tier 5 in the testing-strategy doc](../architecture/testing-strategy.md)
- Phase 11 roadmap doc — conformance section
- [ADR 0019](0019-specialized-types.md) — spheroidal-vs-planar
  GEOGRAPHY divergence that anchors the slice-2 xfail set.
- [ADR 0021](0021-chaos-tier-design-contract.md) — sibling
  testing-tier design contract, used as the template for this ADR.
- [`docs/reference/out-of-scope.md`](../reference/out-of-scope.md) —
  enumerates the v1.0.0 exclusions some xfail rationales reference.
