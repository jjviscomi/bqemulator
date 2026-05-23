# ADR 0023: Conformance divergence baseline (slice 2 close)

- **Status**: Accepted
- **Date**: (last revised — scope-expansion
  #15, `RANGE_SESSIONIZE` closure ratcheted the corpus from 641 to
  644 fixtures; same day, P3.a error-message-shape parity added 20
  more fixtures bringing the corpus to 664 — xfail count still 9,
  pinned to the same ADR 0019 spheroidal + BIGNUMERIC entries).
  ADR 0023 governs only the ten slice-2-close buckets A–J; the
  P3.a error-shape contract lives in ADR 0022 §3 ``Error parity``.

## Context

Slice 2 of Phase 11 initially recorded 288 canonical conformance
fixtures against real BigQuery and replayed each against the
in-process emulator; the diff surfaced 87 fixtures (≈30%) whose
result did not match the recorded baseline, splitting cleanly
into **nine** categorical buckets — *not* one-off oddities but
well-defined emulator-vs-real-BigQuery divergences sharing a root
cause. Mid-slice the corpus was expanded to **641** fixtures
(see §3 below for the post-expansion count breakdown); the wider
function coverage surfaced a tenth bucket (J — uncovered builtin
translations), bringing the slice-2-close registry to **199**
xfail entries across 10 buckets. Four closure sessions landed on
Bucket A first (199 → 175), then Bucket C
(175 → 167), then Bucket D (167 → 163), then Bucket E
(163 → 162); four further sessions closed Bucket F
(162 → 159), Bucket J (159 → 116), Bucket B (116 → 82), and
Bucket G (82 → 61); two final sessions closed
Bucket I (61 → 18) and Bucket H (18 → 11). All ten ADR 0023
buckets (A–J) are now closed. §3 carries the post-closure
pass-rate calculation, and the per-bucket sections below carry
their respective closure notes.

ADR 0022 §4 mandates that every fixture pinned to
`xfail(strict=True)` carries a rationale rooted in an ADR. This ADR
is that anchor. It enumerates the divergences observed at slice 2
close (and the subsequent closure notes as buckets land) so each
entry in
[`tests/conformance/divergences.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py)
can name a bucket and a closure plan.

This ADR is *descriptive*, not *prescriptive*: it does not lock new
behaviour into the emulator. It is the catalogue future slices
shrink as the gaps are closed. Four buckets closed 
in consecutive parity-closure sessions: Bucket A (199 → 175),
Bucket C (175 → 167), Bucket D (167 → 163), and Bucket E
(163 → 162). Four further buckets closed:
Bucket F (162 → 159), Bucket J (159 → 116), Bucket B
(116 → 82), and Bucket G (82 → 61). Two final closures on
Bucket I (61 → 18) and Bucket H (18 → 11).

## Decisions

### 1. Ten divergence buckets

Each bucket has a one-line definition, a root-cause analysis, and a
named future slice that closes it. Fixtures pinned to a bucket use
the bucket's rationale string in `divergences.py`. Buckets A–I were
established at the initial slice-2 close (288-fixture corpus);
Bucket J was added when the corpus was expanded to 641 fixtures
later in slice 2 and surfaced a new failure mode (uncovered builtin
functions). Buckets A, C, D and E were closed in
consecutive parity-closure sessions; Buckets F, J, B, and G all
closed; Buckets I and H closed.
**All ten ADR 0023 buckets (A–J) are now closed** — see the
respective §1.A through §1.J closure notes below.

#### Bucket A — REPEATED-row wire-format shape — Closed

**Status.** Closed. The fix lives in
[`src/bqemulator/jobs/executor.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/executor.py)
(`build_response_schema` now derives `mode=REPEATED` from Arrow
list types and recursively emits nested `fields` for STRUCTs) and
[`src/bqemulator/storage/arrow_bridge.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/storage/arrow_bridge.py)
(NULL Arrow values for list-typed columns render as `[]`, not
`null`). Regression coverage:
[`tests/unit/api/test_arrow_type_to_bq.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/api/test_arrow_type_to_bq.py)
and
[`tests/unit/storage/test_arrow_bridge.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/storage/test_arrow_bridge.py).

**Definition (historical).** Any fixture whose schema contained a
`REPEATED` column triggered a `TypeError: list indices must be
integers or slices, not str` deep in the
`google-cloud-bigquery` row deserialiser.

**Root cause (historical).** BigQuery's wire format for a REPEATED
column carries `mode: "REPEATED"` and the *element* type on the
schema entry (`{type: "INTEGER", mode: "REPEATED"}` for
`ARRAY<INT64>`); rows render as
`{"v": [{"v": "1"}, {"v": "2"}]}`. The emulator's pre-closure
shape was `{type: "RECORD", mode: "NULLABLE"}` on the schema entry
with the element values already wrapped on the row side — the
`google-cloud-bigquery` parser then dispatched into the RECORD
branch and tried to index a list with the string `"f"`, crashing.

**Outcome at closure.** Sixteen of the twenty-two originally-pinned
fixtures flipped to XPASS once the schema renderer matched
BigQuery's wire shape and NULL REPEATED cells rendered as `[]`.
The fix incidentally also closed six Bucket I entries
(`select_struct_literal`, `select_struct_nested`,
`dml_insert_array_value`, `empty_array_in_struct`,
`empty_struct_field`, `rw_array_subquery`) and two Bucket J
entries (`agg_array_agg_empty`, `rw_array_agg_with_filter`) which
were rooted in the same renderer gap. Six former Bucket A entries
remain xfail'd against their *true* root cause:
`generate_range_array_date` moved to Bucket G (RANGE literal
parsing); `agg_array_agg_ordered`, `json_lax_bool`,
`json_lax_int64`, `json_lax_string` moved to Bucket J (uncovered
builtin translation); `json_to_json_array` moved to Bucket I
(JSON-type round-tripping). The registry shrinks from 199 to 175
entries; conformance metrics move from 442 passed + 199 xfailed to
**466 passed + 175 xfailed** (24 net XPASS).

**Affected fixtures (historical).** Array-returning queries —
`ARRAY_AGG`, `ARRAY_CONCAT`, `ARRAY_REVERSE`, `GENERATE_ARRAY`,
`GENERATE_DATE_ARRAY`, `JSON_VALUE_ARRAY`, `JSON_QUERY_ARRAY`,
`SPLIT`, `REGEXP_EXTRACT_ALL`, `LAX_*`,
`GENERATE_RANGE_ARRAY`, `select_array_literal`, and any aggregate
that returns an array.

#### Bucket B — Numeric type promotion (FLOAT64 ↔ NUMERIC) — Closed

**Status.** Closed. The closure ships six coordinated
fixes plus a new catalog-schema-aware pass in the translator:

1. **Decimal-literal pre-translator** —
   [`src/bqemulator/sql/rewriter/decimal_literals.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/decimal_literals.py)
   rewrites every bare BigQuery decimal literal (``3.25``, ``-1.5``,
   ``3.14159``) to scientific notation (``3.25e0``) before the SQLGlot
   transpile. DuckDB types ``3.25e0`` as ``DOUBLE`` so the Arrow column
   surfaces as ``FLOAT`` on the REST wire — matching BigQuery's
   ``FLOAT64`` literal-typing rule. String literals (``is_string=True``,
   including the bodies of ``NUMERIC '…'`` / ``BIGNUMERIC '…'`` / ``DATE
   '…'`` typed-literal casts) are untouched.

2. **Arrow → BigQuery type-mapping widening** —
   [`src/bqemulator/jobs/executor.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/executor.py)'s
   ``_arrow_type_to_bq_type`` now matches every integer width via
   ``pa.types.is_integer`` (was ``int64`` + ``int32`` only). DuckDB's
   ``SIGN(INT)`` returns ``TINYINT`` (Arrow ``int8``) and several
   smaller-width arithmetic shortcuts emit ``SMALLINT``; all five
   widths plus the unsigned variants land on ``INTEGER``. The decimal
   branch additionally inspects the Arrow scale: any DECIMAL with
   ``scale > 9`` surfaces as ``BIGNUMERIC`` (BigQuery NUMERIC has
   fixed scale 9; BIGNUMERIC carries up to scale 38).

3. **HUGEINT override via DuckDB metadata** — ``_resolve_bq_type``
   reads the ``bqemu.duckdb_type`` field metadata (set by
   ``DuckDBEngine.fetch_arrow``); a value of ``HUGEINT`` surfaces as
   ``INTEGER``. DuckDB's ``SUM(BIGINT)`` and ``COUNT_IF(…)`` aggregates
   promote to HUGEINT, which Arrow encodes as ``decimal128(38, 0)`` —
   without the override the column would land on NUMERIC even though
   BigQuery returns INTEGER.

4. **DATE_TRUNC QUARTER / WEEK rules** —
   [`src/bqemulator/sql/rules/iso_date_parts.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/iso_date_parts.py)
   gains ``DateTruncQuarterRule`` and ``DateTruncWeekRule``. QUARTER
   wraps the call in ``CAST(... AS DATE)`` so the TIMESTAMP-typed
   DuckDB result becomes DATE; WEEK rewrites the call to
   ``CAST(d - INTERVAL DAYOFWEEK(d) DAY AS DATE)`` so the result is
   the most-recent Sunday on-or-before the input (BigQuery defaults
   to Sunday-start week; DuckDB's WEEK truncation is Monday-start).
   Both rules only fire when the operand is provably DATE-typed —
   ``CAST(... AS DATE)`` (the form ``DATE '…'`` typed literals
   collapse to) or ``CURRENT_DATE()``.

5. **PARSE_NUMERIC / PARSE_BIGNUMERIC translation rules** —
   [`src/bqemulator/sql/rules/numeric_types.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/numeric_types.py)
   matches SQLGlot's typed ``ParseNumeric`` / ``ParseBignumeric``
   nodes (DuckDB ships neither builtin). ``PARSE_NUMERIC(s)`` becomes
   ``CAST(s AS DECIMAL(38, 9))``; ``PARSE_BIGNUMERIC(s)`` routes
   through the ``bqemu_to_bignumeric`` Python UDF below so the wire
   type tag lands on BIGNUMERIC.

6. **`bqemu_to_bignumeric` UDF and scale-aware rewriter** —
   [`src/bqemulator/sql/builtin_udfs.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/builtin_udfs.py)
   adds a Python-backed scalar UDF that parses via:class:`Decimal`
   and is registered with DuckDB return type ``DECIMAL(38, 10)``. The
   scale of 10 is the marker the schema renderer uses to distinguish
   BIGNUMERIC from NUMERIC (fix 2 above). The pre-translator at
   [`src/bqemulator/sql/rewriter/numeric_literals.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/numeric_literals.py)
   is now scale-aware: ``BIGNUMERIC 'literal'`` with ≥ 10 fractional
   digits (and total digit count ≤ 38) is cast directly to
   ``CAST('literal' AS DECIMAL(38, S))`` with ``S = literal_scale``
   — preserving the high-precision fractional case (e.g. 0 integer
 + 38 fractional digits round-trips at full ``DECIMAL(38, 38)``
   precision). BIGNUMERIC literals with ≤ 9 fractional digits route
   through the UDF and land on ``DECIMAL(38, 10)`` — the UDF's
   28-integer-slot capacity is enough for every wide-integer
   BIGNUMERIC literal the slice-2 corpus exercises (e.g. the
   20-integer-digit fixture the prior ``CAST AS DECIMAL(38, 38)``
   rewrite could not represent). NUMERIC literals keep the
   ``CAST AS DECIMAL(38, 9)`` rewrite unchanged.

7. **Catalog-schema-aware AVG-decimal preservation** — A new
   [`src/bqemulator/sql/catalog_schema.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/catalog_schema.py)
   helper walks the BQ-side AST, resolves every ``exp.Table`` against
   the catalog repository, and emits a ``{table: {col: type}}`` dict
   shaped for SQLGlot's ``annotate_types`` pass. The executor
   ([`src/bqemulator/jobs/executor.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/executor.py))
   passes this dict through to ``SQLTranslator.translate(bq_sql,
   schema=…)``; the translator runs ``qualify`` + ``annotate_types``
   so AST nodes carry resolved ``.type`` attributes. The new
   [`AvgDecimalRule`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/aggregate_types.py)
   reads the AVG operand's type and wraps ``AVG(decimal_col)`` in
   ``CAST(... AS DECIMAL(38, 9))`` when the operand is DECIMAL;
   integer / float operands flow through unchanged so the existing
   ``AVG(INT64) → FLOAT64`` contract is preserved. For windowed
   ``AVG(x) OVER (…)`` the cast wraps the whole windowed expression
   (DuckDB rejects ``CAST AS T OVER`` placement) — the rule dispatches
   on the parent ``Window`` node when its child is an Avg.

The annotate-types pass is best-effort: parse / qualify failures
fall through silently so the legacy un-annotated path stays the
default for queries the catalog cannot annotate (CTEs, subquery
aliases, tables not yet registered).

Regression coverage:
[`tests/unit/sql/rewriter/test_decimal_literals.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/rewriter/test_decimal_literals.py)
(14 cases covering the literal-rewrite predicate's positive +
negative paths plus DuckDB-side type assertions),
[`tests/unit/sql/rules/test_numeric_types.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/rules/test_numeric_types.py)
(PARSE_NUMERIC / PARSE_BIGNUMERIC / BIGNUMERIC literal — 9 cases),
[`tests/unit/sql/rules/test_aggregate_types.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/rules/test_aggregate_types.py)
(7 cases including window-AVG and nested ROUND/AVG),
[`tests/unit/sql/rules/test_iso_date_parts.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/rules/test_iso_date_parts.py)
gains 5 new cases for QUARTER + WEEK (2 + 3),
[`tests/unit/sql/test_catalog_schema.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/test_catalog_schema.py)
(7 cases — three-part ref, two-part ref + default project,
wire-format alias normalisation, bare-ref skip, missing-table /
parse-failure / unmappable-type drop-through), and
[`tests/unit/sql/test_builtin_udfs_bignumeric.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/test_builtin_udfs_bignumeric.py)
(9 cases). The Arrow → BQ type-helper test
[`tests/unit/api/test_arrow_type_to_bq.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/api/test_arrow_type_to_bq.py)
gains parametrised cases for every integer width and the DECIMAL
scale-marker rule plus two explicit HUGEINT / BIGNUMERIC metadata
overrides.

**Definition (historical).** A literal or arithmetic expression
that BigQuery types as NUMERIC the emulator types as FLOAT64 (or
vice-versa). Manifests as ``schema[0].type: expected='NUMERIC'
actual='FLOAT'`` or the reverse, or as a ``Conversion Error``
when DuckDB's default ``DECIMAL(18, 3)`` cannot represent a wide
BIGNUMERIC literal.

**Root cause (historical).** BigQuery's literal-type inference
treats fixed-point decimals (e.g. ``3.25``) as FLOAT64; DuckDB
treats them as DECIMAL. Aggregates over NUMERIC (``AVG`` is the
critical one; ``SUM`` preserves DECIMAL correctly) follow the same
drift — BigQuery preserves NUMERIC, DuckDB's ``AVG`` always
promotes to DOUBLE. ``SUM(BIGINT)`` and ``COUNT_IF`` promote to
HUGEINT, which Arrow encoded as decimal128(38, 0) and the
emulator's renderer surfaced as NUMERIC. ``SIGN(INT)`` returns
TINYINT (Arrow int8) which fell through to the STRING fallback.
``DATE_TRUNC(date, QUARTER/WEEK)`` returned TIMESTAMP instead of
DATE; the WEEK form additionally truncated to Monday rather than
Sunday. ``PARSE_NUMERIC`` and ``PARSE_BIGNUMERIC`` had no DuckDB
analogue. ``BIGNUMERIC '…'`` typed literals lacked a rewrite path
that preserved the BIGNUMERIC type tag without sacrificing
integer-digit capacity.

**Outcome at closure.** All 22 originally-pinned Bucket B
fixtures XPASSed and were removed from ``divergences.py``. Twelve
further fixtures incidentally XPASSed via the same closure:
``st_geogfromtext_multipoint``, ``st_isring_line``,
``st_npoints_line``, ``st_numpoints_polygon``, ``st_pointn_line``
(Bucket H — ST_NPOINTS / ST_NUMPOINTS / ST_ISRING / ST_POINTN
return narrow-width integers the widened Arrow mapper now
renders as INTEGER), and ``agg_bit_count_scalar``,
``agg_sum_empty``, ``agg_sum_null_col``, ``empty_array_aggsum``,
``rw_case_in_aggregate``, ``rw_session_count``,
``str_regexp_instr`` (Bucket I — DuckDB's ``BIT_COUNT(…)``,
``SUM(BIGINT)``, ``COUNT_IF(…)``, and ``regexp_count`` all
surface as HUGEINT / narrow-width INTEGER that the renderer now
correctly maps). Conformance metrics move from **525 passed +
116 xfailed** to **559 passed + 82 xfailed** (34 net XPASS — 22
direct + 12 incidental). Pass rate of non-divergent fixtures
stays at 100%.

#### Bucket C — Wildcard table expansion — Closed

**Status.** Closed. The fix widens the
wildcard-expander predicate in
[`src/bqemulator/sql/rewriter/wildcard_expander.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/wildcard_expander.py)
to match every wildcard reference shape BigQuery accepts — bare 1-
or 2-part, fully-qualified 3-part (``project.dataset.events_*``),
and either-or-both backticked — and replaces ``re.search`` with
``re.sub`` so self-joins expand every occurrence (not just the
first). Hyphenated project ids (``test-project``) flow through
unchanged because the project-segment character class widens to
``[\w-]``; each expanded reference is re-emitted backticked so
the downstream SQLGlot parser accepts the hyphen. An explicit
``AS <alias>`` on the original reference is preserved in the
replacement (the synthetic ``AS __wildcard`` is omitted to avoid
double-aliasing).

The closure also threaded storage-level table discovery through
the catalog: a new ``list_storage_tables(project_id, dataset_id)``
method on
[`CatalogRepository`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/catalog/repository.py)
introspects DuckDB's ``information_schema.tables`` so wildcard
expansion sees every shard a conformance fixture creates via
``CREATE TABLE … AS SELECT`` — the catalog cache only tracks
REST-registered tables, and the conformance setup uses SQL DDL.
The ephemeral-mode ``MemoryCatalogRepository`` now takes an
optional engine so it can answer the storage query without
upgrading the server to ``DuckDBCatalogRepository``.

Finally, the REST schema renderer
([`build_response_schema`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/executor.py))
now deduplicates column names with a ``_<n>`` suffix
(``_TABLE_SUFFIX``, ``_TABLE_SUFFIX_1`` …) — the
``wildcard_join_self`` fixture's self-join projects
``a._TABLE_SUFFIX`` and ``b._TABLE_SUFFIX`` and BigQuery uniquifies
those names; DuckDB leaves the duplicates in place, so the
renderer post-processes the schema to match the wire format.

Regression coverage:
[`tests/unit/sql/test_wildcard_expander.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/test_wildcard_expander.py)
gains four new cases — fully-qualified 3-part with whole-ref
backticks, suffix-equality pushdown on a 3-part reference,
3-part project-from-SQL precedence over the caller's project,
and self-join with explicit aliases.

**Definition (historical).** `FROM \`<dataset>.events_*\`` queries
failed with `Catalog Error: Table with name events_* does not
exist`.

**Root cause (historical).** The wildcard-table rewriter shipped
in Phase 3 with a predicate that only looked at the trailing
identifier shape; project-qualified references and self-joins
slipped past untouched. Compounding factors surfaced during the
closure: SQL DDL never updated the catalog cache so even the
2-part fix would not have engaged, and DuckDB does not dedupe
duplicate column names on the join projection.

**Outcome at closure.** All 8 originally-pinned fixtures
(``wildcard_aggregate``, ``wildcard_count_per_table``,
``wildcard_groupby_suffix``, ``wildcard_join_self``,
``wildcard_table_basic``, ``wildcard_table_count``,
``wildcard_table_suffix``, ``wildcard_with_date_filter``)
XPASSed and were removed from `divergences.py`. The registry
shrinks from 175 to 167 entries; conformance metrics move from
466 passed + 175 xfailed to **474 passed + 167 xfailed**.

#### Bucket D — Unqualified routine reference — Closed

**Status.** Closed. The fix is a new script-local
TEMP-function registry,
[`src/bqemulator/udf/temp_registry.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/udf/temp_registry.py),
owned by each
[`ScriptInterpreter`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/scripting/interpreter.py)
for the duration of one script run. ``CREATE TEMP FUNCTION foo(...)``
with a single-part identifier routes through
``_exec_create_temp_function`` instead of the catalog-backed path —
the routine is materialised under a registry-unique synthetic
dataset id (``_bqemu_temp_<uuid-hex>``) so concurrent scripts on
the same engine never collide. ``_resolve_ref`` checks the registry
first for single-part references (ADR 0023 §1.D's local-scope
lookup pass) and falls through to the existing 2/3-part check if
nothing matches. ``_run_query`` and its parameterised siblings call
``TempRoutineRegistry.rewrite_calls`` before the rest of the
pipeline so a bare ``foo(args)`` is rewritten to the qualified flat
name SQLUDFRuntime materialised the routine under — DuckDB then
finds the macro on the first lookup. The interpreter's
``run`` method drops every materialised TEMP macro in a ``finally``
arm, preserving the [ADR 0014](0014-udf-materialization-strategy.md)
scope guarantee: TEMP functions never leak into the catalog nor
across script invocations.

Regression coverage:
[`tests/unit/udf/test_temp_registry.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/udf/test_temp_registry.py)
adds ten cases — synthetic-dataset uniqueness per instance,
register/resolve round-trip, unknown-name returns None, dataset
mismatch rejection, materialised macro is callable, rewrite of
``Anonymous`` calls, passthrough for unregistered names with an
empty registry, passthrough for an unregistered name when the
registry is non-empty (no-change path), passthrough for
unparseable SQL, and cleanup-then-deregister idempotency.

**Definition (historical).** `CREATE TEMP FUNCTION foo(...);
SELECT foo(...)` failed with `Routine reference must have 2 or 3
parts: foo`.

**Root cause (historical).** The emulator's routine resolver
required a fully-qualified `project.dataset.routine` reference.
Real BigQuery treats single-part identifiers as TEMP-function
references when they resolve in the script's local scope. The
emulator's resolver did not search the local scope first, and
``_exec_create_function`` itself rejected the single-part
``CREATE TEMP FUNCTION`` name before any registration could occur.

**Outcome at closure.** All 4 originally-pinned fixtures
(``sql_udf_int_to_int``, ``sql_udf_string_param``,
``sql_udf_returns_array``, ``sql_udf_returns_struct``) XPASSed and
were removed from `divergences.py`. The registry shrinks from 167
to 163 entries; conformance metrics move from 474 passed + 167
xfailed to **478 passed + 163 xfailed**.

#### Bucket E — Multi-statement scripting result column naming — Closed

**Status.** Closed. The fix lives in
[`src/bqemulator/scripting/interpreter.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/scripting/interpreter.py)
(`_rewrite_vars_to_params` now wraps the placeholder in an
``Alias`` whenever a bare script-variable reference is a top-level
SELECT projection — preserving BigQuery's "single identifier →
use as column name" inference). The check uses SQLGlot's
``col.parent is exp.Select`` + ``col.arg_key == "expressions"``
predicate so the alias is only applied at the projection slot —
columns nested inside arithmetic (``SELECT n + 1``) and columns
already wrapped in an explicit ``AS`` (``SELECT label AS x``) are
left untouched. Regression coverage:
[`tests/unit/scripting/test_interpreter.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/scripting/test_interpreter.py)
gains a ``TestProjectionNameInference`` class with four cases —
bare-variable propagation, explicit-alias passthrough, complex
expression *non*-propagation, and multi-projection round-trip.

**Definition (historical).** Multi-statement scripts that end in
a `SELECT` of a single computed expression returned a result with
a placeholder column name (`$1`, `_col0`) instead of the inferred
name from the final SELECT.

**Root cause (historical).** The scripting interpreter's
``_rewrite_vars_to_params`` rewriter replaces every bare
script-variable reference in the SQL with a bound parameter
(``@1``, ``@2``, …). For a final ``SELECT label`` that
replacement erased the source identifier, so DuckDB emitted the
default ``$1`` column name; BigQuery would name the column
``label`` because the projection is a single identifier without
``AS``. The interpreter's last-statement-result projection had
no compensating step that re-applied the inferred name.

**Outcome at closure.** 1 of the 2 originally-pinned fixtures
(``script_if_then``) XPASSed once the alias was propagated.
``script_exception_handler`` carried a second, independent
divergence — the fixture's expected ``outcome='caught'`` value
requires ``EXECUTE IMMEDIATE 'SELECT 1 / 0'`` to raise so the
``EXCEPTION WHEN ERROR`` handler fires. DuckDB returns ``Inf``
for ``1 / 0`` instead of raising, so even with the correct
column name the script's ``outcome`` stays ``'ok'``. That
secondary divergence is a SQL operator semantic (not a
scripting interpreter concern), and the fixture has been
reclassified to Bucket I — see §1.I. Bucket E's closure
shrinks the registry from 163 to 162 entries; conformance
metrics move from 478 passed + 163 xfailed to **479 passed +
162 xfailed**.

#### Bucket F — Multi-statement DDL extra-row surface — Closed

**Status.** Closed. The fix has three coordinated parts:

1. **Per-statement versioning-DDL dispatch in the script interpreter.**
   [`src/bqemulator/scripting/interpreter.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/scripting/interpreter.py)'s
   ``_exec_sql`` now checks each statement against
   ``is_versioning_ddl`` and routes matches through
   ``execute_versioning_ddl`` (the same path single-statement DDL
   already used). DuckDB therefore never sees ``CREATE SNAPSHOT
   TABLE`` / ``CREATE TABLE … CLONE`` / ``CREATE MATERIALIZED
   VIEW`` syntax inside a script — the snapshot, clone, and MV
   managers handle the catalog-and-storage side effects directly.
   The matching regex anchors (``^…\s*;?\s*$``) require a single
   statement to match, so the per-statement dispatch is essential
   to avoid the multi-statement greedy capture that was masking
   the bug.

2. **Top-level executor gates the versioning-DDL fast path on
   single-statement input.** [`src/bqemulator/jobs/executor.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/executor.py)'s
   ``execute_query_job`` now parses the script first and only
   calls ``_maybe_run_versioning_ddl`` when ``len(script.statements)
   == 1`` and the lone statement is a ``SqlStmt``. Multi-statement
   scripts go straight to the interpreter (which dispatches versioning
   DDL per-statement). Without this guard, the
   ``_CREATE_MATERIALIZED_VIEW_RE`` regex's lazy ``.+?\s*;?\s*$``
   greedy-matched the trailing ``SELECT`` into the captured
   ``view_query``, causing ``extract_base_tables`` to flag the MV
   target as one of its own base tables → 404.

3. **CREATE TABLE → catalog auto-sync.** A new helper module,
   [`src/bqemulator/catalog/ddl_sync.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/catalog/ddl_sync.py),
   introspects plain ``CREATE [OR REPLACE] TABLE …`` outputs after
   DuckDB executes them and upserts a matching ``TableMeta`` in the
   catalog. ``execute_query_job`` and ``ScriptInterpreter._exec_sql``
   both call ``sync_created_table`` after a successful DDL run.
   The conformance fixtures' setup statements (``CREATE OR REPLACE
   TABLE source_table AS …``) now leave the source table catalog-
   visible so the versioning managers' ``catalog.get_table``
   precondition lookups succeed. VIEW, MATERIALIZED VIEW, CLONE,
   and SNAPSHOT forms are skipped — those route through their own
   managers, which already register their outputs.

   The detection is SQLGlot-based (``isinstance(tree, exp.Create)``
 + ``tree.kind == 'TABLE'`` + ``tree.args.get('clone') is None``)
   so VIEW / MV / CLONE / SNAPSHOT forms cleanly fall through; an
   unparseable statement (``Command``) is also ignored. The schema
   is introspected by running ``SELECT * FROM <ref> LIMIT 0`` and
   mapping Arrow types to BigQuery type names.

4. **Last-statement-with-output rule.** The interpreter's
   ``_exec_sql`` now only updates ``_final_table`` when the
   executed statement is ``isinstance(tree, exp.Query)``
   (``SELECT`` / ``WITH`` / ``UNION`` / ``INTERSECT`` /
   ``EXCEPT`` / ``Subquery``). DDL and DML still execute but
   contribute no rows — matching BigQuery's "last statement with
   output wins" semantic. Today's tests didn't exercise the
   subtle case (``SELECT …; INSERT …`` ending in DML), but the
   contract is now explicit rather than depending on DuckDB's
   ``Count: int64`` placeholder happening to look empty.

Regression coverage:
[`tests/unit/scripting/test_interpreter.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/scripting/test_interpreter.py)
gains a ``TestLastStatementWins`` class with five cases —
``CREATE TABLE AS …; SELECT``, DDL-only scripts (``final_table``
is ``None``), and a per-DDL-type case for SNAPSHOT, CLONE, and
MV inside a script. The new
[`tests/unit/catalog/test_ddl_sync.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/catalog/test_ddl_sync.py)
exercises the sync helper directly with ten cases — plain
``CREATE TABLE AS``, column-only ``CREATE TABLE``, ``OR REPLACE``
idempotency, VIEW / MV / CLONE / SNAPSHOT all skipped, SELECT /
DML / unparseable all no-op, and the missing-dataset silent
no-op path.

**Definition (historical).** ``CREATE SNAPSHOT TABLE …; SELECT
…`` and the analogous CLONE / MATERIALIZED VIEW patterns failed
under the emulator with a parser error from DuckDB (``CREATE
SNAPSHOT TABLE`` / ``CLONE`` / ``MATERIALIZED VIEW`` syntax is not
recognised). Real BigQuery returned the trailing SELECT's rows;
the recorder captured that, so every replay failed.

**Root cause (historical).** The scripting interpreter sent each
statement through ``_run_query``, which always hands the SQL to
the DuckDB-flavoured translator → DuckDB. Versioning DDL never
reached the matching ``SnapshotTableManager`` /
``CloneManager`` / ``MaterializedViewManager``. Compounding
factors surfaced during closure: the top-level executor's
``_maybe_run_versioning_ddl`` did fire for multi-statement
scripts, but the MV regex greedy-matched across statement
boundaries; and the setup tables were created via SQL DDL that
never updated the catalog cache, so the versioning managers'
``catalog.get_table`` precondition lookups would have failed
even if the dispatch had been correct.

**Outcome at closure.** All 3 originally-pinned fixtures
(``versioning/clone_basic``,
``versioning/mv_basic``, ``versioning/snapshot_basic``)
XPASSed and were removed from `divergences.py`. The registry
shrinks from 162 to 159 entries; conformance metrics move from
479 passed + 162 xfailed to **482 passed + 159 xfailed**.

#### Bucket G — RANGE / INTERVAL wire format — Closed

**Status.** Closed. The closure ships three coordinated
fixes:

1. **RANGE literal pre-translator.** The pre-translator at
   [`src/bqemulator/sql/rewriter/specialized_types.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/specialized_types.py)
   gains a `_rewrite_range_literals` pass. SQLGlot parses
   ``RANGE<T> '[start, end)'`` typed literals as
   ``Cast(literal, RANGE<T>)``; DuckDB rejects ``CAST(... AS
   RANGE(T))`` because RANGE is not a DuckDB type. The pass walks
   the AST, parses the ``[start, end)`` body via a single anchored
   regex (``^\[\s*(?P<start>[^,]+?)\s*,\s*(?P<end>[^,]+?)\s*\)$``),
   and replaces each occurrence with ``STRUCT(CAST(<start> AS T) AS
   start, CAST(<end> AS T) AS end)``. ``UNBOUNDED`` endpoints
   become ``CAST(NULL AS T)`` so DuckDB's struct typing stays
   uniform across rows (without the NULL cast the field type
   defaults to ``BIGINT`` and the struct rejects subsequent typed
   values). Element-type dispatch mirrors SQLGlot's parser fold:
   ``DType.DATE`` → ``DATE``, ``DType.TIMESTAMP`` (SQLGlot's
   parse of BQ ``DATETIME``) → BQ ``DATETIME`` →
   ``DType.DATETIME`` re-emitted (BQ serializer → ``DATETIME`` →
   DuckDB ``TIMESTAMP`` naive), and ``DType.TIMESTAMPTZ`` (SQLGlot's
   parse of BQ ``TIMESTAMP``) → BQ ``TIMESTAMP`` →
   ``DType.TIMESTAMPTZ`` (DuckDB ``TIMESTAMPTZ``). The pre-translator
   guard short-circuits on the absence of ``RANGE<`` in the source
   SQL so the common case stays free of the SQLGlot reparse.

2. **RANGE wire-format detection.** A new
   ``detect_range_element(duckdb_type)`` helper in
   [`src/bqemulator/types/range_type.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/types/range_type.py)
   parses the canonical ``STRUCT("start" T, "end" T)`` and
   ``STRUCT("start" T, "end" T)[]`` (REPEATED) DuckDB column-type
   strings and returns ``(bq_element_type, is_repeated)`` or
   ``None``. Both the REST schema renderer in
   [`src/bqemulator/jobs/executor.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/executor.py)
   (``_maybe_range_schema_entry``) and the row renderer in
   [`src/bqemulator/storage/arrow_bridge.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/storage/arrow_bridge.py)
   (``_bq_range_metadata``) consult the helper. RANGE columns
   surface on the wire as
   ``{type: "RANGE", mode, rangeElementType: {type: T}}`` (never
   ``RECORD`` with nested ``fields``), and each cell value becomes
   the canonical ``[start, end)`` string the BigQuery Python
   client's ``_RANGE_PATTERN`` parses. ``UNBOUNDED`` round-trips:
   the renderer emits the literal token, and the client maps it
   back to ``None``. Endpoint formatting matches the Python
   client's element-type parsers: DATE → ISO ``YYYY-MM-DD``,
   DATETIME → ISO ``YYYY-MM-DDTHH:MM:SS[.ffffff]`` with the ``T``
   separator the client's ``_RFC3339_*`` strptime expects, and
   TIMESTAMP → microseconds-since-epoch integer string (the form
   ``timestamp_to_py`` parses via ``int(value)``).

3. **INTERVAL schema-type fix and GENERATE_RANGE_ARRAY type
   preservation.** ``_arrow_type_to_bq_type`` in
   ``executor.py`` gains a ``pa.types.is_interval(arrow_type) →
   "INTERVAL"`` branch so DuckDB's ``month_day_nano_interval``
   columns surface on the wire as ``INTERVAL`` rather than
   STRING; the existing canonical ``Y-M D H:M:S`` renderer in
   ``arrow_bridge`` was already correct, but without the schema-
   type fix the column landed on the STRING fallback and the
   BigQuery Python client treated the value as an opaque string.
   The ``GenerateRangeArrayRule`` in
   [`src/bqemulator/sql/rules/range_rules.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/range_rules.py)
   gains a ``_detect_range_struct_element`` helper that recovers
   the inner element type from the rng argument's
   ``STRUCT(Cast(_, T), Cast(_, T))`` AST shape (the pre-
   translator's output for both ``RANGE<T> '[…]'`` literals and
   ``RANGE(a, b)`` constructor calls). The lambda's start/end
   endpoints are wrapped in ``CAST(... AS T)`` so DuckDB's
   widening of ``DATE + INTERVAL`` to ``TIMESTAMP`` is undone for
   DATE-typed ranges. The trailing sub-range is clipped to the
   outer range's end via ``LEAST(x + step, rng."end")`` —
   BigQuery returns ``[2024-01-07)`` for a 2-day step
   over ``[…)``, not ``[2024-01-07)``.

Regression coverage:
[`tests/unit/sql/test_specialized_types_rewriter.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/test_specialized_types_rewriter.py)
gains a ``TestRangeLiteralRewrite`` class with seven cases (DATE
/ DATETIME / TIMESTAMP element types, both ``UNBOUNDED`` sides,
arrays of RANGE literals, and RANGE literals embedded in
function-call positions);
[`tests/unit/types/test_range_type.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/types/test_range_type.py)
gains a ``TestDetectRangeElement`` class with twelve cases (six
positive shapes including REPEATED, plus a case-insensitive case
and five negatives — empty string, wrong field names,
heterogeneous inner types, an unrelated inner type, and an
extra-field struct);
[`tests/unit/api/test_arrow_type_to_bq.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/api/test_arrow_type_to_bq.py)
adds a ``TestRangeSchemaEntry`` class with four cases (DATE /
DATETIME / REPEATED-DATE positive, plus a fallthrough case for an
unrelated STRUCT) and a parametrised
``pa.month_day_nano_interval()`` → ``INTERVAL`` row in
``TestArrowTypeToBqType``;
[`tests/unit/storage/test_arrow_bridge.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/storage/test_arrow_bridge.py)
adds a ``TestArrowToBqRowsRangeWireFormat`` class with seven
cases (DATE / DATETIME / TIMESTAMP scalar, ``UNBOUNDED``
endpoints, REPEATED RANGE, NULL RANGE, and an INTERVAL
canonical-string row); and
[`tests/unit/sql/rules/test_range_rules.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/rules/test_range_rules.py)
updates ``TestGenerateRangeArray`` with two cases pinning the
DATE-preserving + end-clipping semantic against a live DuckDB
connection. The pre-existing integration test
[`tests/integration/test_interval.py::test_justify_hours`](https://github.com/jjviscomi/bqemulator/blob/main/tests/integration/test_interval.py)
is updated to assert the BigQuery Python client now parses the
``INTERVAL`` cell as a ``dateutil.relativedelta`` (the pre-closure
STRING form has been retired).

**Definition (historical).** Queries returning RANGE or INTERVAL
values diverged in either schema shape or value serialisation.

**Root cause (historical).** BigQuery's REST wire format encodes
RANGE as a single ``[start, end)`` string with the schema entry
carrying ``type=RANGE`` plus ``rangeElementType: {type: T}``, and
INTERVAL as a canonical ``Y-M D H:M:S[.ffffff]`` string with the
schema entry carrying ``type=INTERVAL``. The pre-closure emulator
modelled RANGE as a STRUCT both in storage *and* on the wire —
surfacing the column as ``RECORD`` with nested ``fields`` —
and emitted INTERVAL columns with the STRING type fallback
because ``_arrow_type_to_bq_type`` had no
``pa.types.is_interval`` branch; the row value was already in the
canonical Y-M D H:M:S form, but the schema mismatch defeated the
Python client's INTERVAL parser. DuckDB further rejected ``CAST(...
AS RANGE(T))`` because RANGE is not a DuckDB type, so every
``RANGE<T> '[…]'`` literal query crashed at the SQL compile stage.

**Outcome at closure.** All 20 originally-pinned Bucket G fixtures
XPASSed and were removed from `divergences.py`. One further
fixture incidentally XPASSed via the same closure:
``specialized_types/interval_zero`` (previously Bucket I —
``SELECT INTERVAL 0 DAY`` whose expected value is
``relativedelta()`` and which only matched once the wire-format
schema reported INTERVAL instead of STRING). Conformance metrics
move from **559 passed + 82 xfailed** to **580 passed + 61
xfailed** (21 net XPASS — 20 direct + 1 incidental). Pass rate
of non-divergent fixtures stays at 100%.

#### Bucket H — GEOGRAPHY WKT whitespace — Closed

**Status.** Closed. The closure ships an amendment to
[ADR 0022 §3](0022-conformance-corpus-design.md) (a new WKT-shaped
STRING sub-rule under the STRING tolerance contract) plus a
six-line extension to
[`tests/conformance/_comparison.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/_comparison.py)'s
`_compare_scalar`: a STRING-typed cell whose value matches the
anchored regex
``^(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|GEOMETRYCOLLECTION)\s*\(``
(case-insensitive) routes through the existing `_normalise_wkt`
helper before equality comparison. Both sides must match the WKT
shape to trigger the rule — a one-sided WKT vs. non-WKT pair still
falls through to exact equality so genuine divergences cannot hide.
Regression coverage is in
[`tests/unit/conformance/test_comparison_wkt_string.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/conformance/test_comparison_wkt_string.py)
(20 unit cases pinning the new contract — pure whitespace drift,
case-insensitive keyword match, coordinate-drift NOT masked,
non-WKT STRING values untouched, NULL handling, REPEATED-mode element
normalisation).

**Definition (historical).** `ST_ASTEXT` and related stringifying
functions return WKT with extra whitespace (`POINT (1 2)`) compared
to BigQuery's compact form (`POINT(1 2)`).

**Root cause (historical).** DuckDB's spatial-extension WKT
formatter inserts a space between the geometry-type keyword and the
opening paren; BigQuery's does not. The comparison helper
normalises WKT for cells declared `GEOGRAPHY`, but `ST_ASTEXT`
returns `STRING` (not GEOGRAPHY) — so the helper applied STRING's
exact-equality rule and reported a mismatch.

**Outcome at closure.** 7 of the 11 originally-pinned Bucket H
fixtures closed directly via the new STRING/WKT comparison rule:

* `st_astext_point`
* `st_geogfromtext_point`
* `st_geogfromtext_linestring`
* `st_geogfromtext_polygon`
* `st_geogfromwkb_point`
* `st_geogfromgeojson_point`
* `st_geogpoint`

Each produced a `POINT (1 2)`-style string where BigQuery emitted
`POINT(1 2)`, and the new sub-rule absorbs that divergence.

4 of the 11 carried a second-order divergence the
WKT-whitespace fix cannot cover and were reclassified in the same
session:

* `specialized_types/st_centroid_polygon` —
  spheroidal-vs-planar coordinate drift (the spheroidal centroid of
  the unit square sits at `(2.00000000000004, 2.00040218892024)`
  where the planar centroid is exactly `(2, 2)`). Reclassified to
  ADR 0019 spheroidal-vs-planar.
* `specialized_types/st_intersection_polygons` —
  spheroidal-vs-planar coordinate drift (the spheroidal
  intersection's edges follow geodesics and bulge by ~1.2e-3
  degrees relative to the planar straight-edge intersection).
  Reclassified to ADR 0019 spheroidal-vs-planar.
* `specialized_types/st_dwithin_no` —
  spheroidal-vs-planar threshold flip
  (planar Euclidean distance over the `(0, 0) ↔ (0, 90)` pair is
  90 coordinate units, where spheroidal distance is ~10⁷ metres;
  with a 100-metre threshold the truth values flip). Reclassified
  to ADR 0019 spheroidal-vs-planar.
* `specialized_types/st_asgeojson_point` —
  DuckDB-spatial's `ST_AsGeoJSON` emits a typed JSON column whose
  serialisation orders `coordinates` before `type` and prints
  floats for whole-number coords; BigQuery emits a STRING column
  with the inverse key order, integer coords, and an inter-token
  whitespace style the rest of its formatter does not. Closing
  this would either require a custom GeoJSON formatter plus a SQL
  rule to flip the schema type to STRING, or a JSON-shape STRING
  tolerance plus a STRING ≡ JSON schema alias — both real
  engineering for a single fixture. Reclassified to
  [`out-of-scope.md#geojson-output-formatting`](../reference/out-of-scope.md)
  with the workaround documented (use `ST_AsText` for canonical
  serialisation; bridge to GeoJSON application-side).

**Option H.1 vs H.2 — selected H.1.** The session prompt offered
two paths: (H.1) extend the comparison helper to detect WKT-shaped
STRING values and apply the GEOGRAPHY normalisation rule, vs.
(H.2) patch DuckDB-spatial's WKT formatter upstream. H.1 is
self-contained, requires no upstream merge / DuckDB version bump,
and the ADR 0022 amendment is a clean addition to an existing
table. H.2 would have introduced an indefinite upstream dependency
for a pure stringification difference. The selected option keeps
the contract in the layer that already owns tolerance for type
drift (the comparison helper) rather than splitting it across
the SQL pipeline and the conformance runner.

**The slice-2-close count was 23**. Subsequent closures shrank it:
the Bucket B closure removed 5 (`st_geogfromtext_multipoint`,
`st_isring_line`, `st_npoints_line`, `st_numpoints_polygon`,
`st_pointn_line` — narrow-width integer types the widened
Arrow→BigQuery type-mapper now surfaces as INTEGER); the Bucket I closure removed 7 more
(`st_geometrytype_linestring`, `st_geometrytype_point`,
`st_geometrytype_polygon`, `st_geometrytype_multipoint`,
`st_convexhull_points`, `st_envelope_polygon`,
`st_makepolygon_from_ring` — closed by the new
`StGeometryTypeBqNameRule`); this closure handles the
final 11 (7 directly + 4 reclassified).

**Net delta**: conformance 623 passed + 18 xfailed → **630 passed +
11 xfailed** (-7 xfailed = 7 Bucket H direct closures; 4 Bucket H
reclassifications keep the same overall xfail count, just pointed
at the right ADR 0019 / out-of-scope.md anchor).

**Scope-expansion #18 follow-up (same day).** The
``st_asgeojson_point`` reclassification was lifted later the same
day via a scope-expansion that reconsidered the GeoJSON
output-formatting out-of-scope entry. The closure ships:

* A new ``StAsGeoJsonStringTypeRule`` in
  [`src/bqemulator/sql/rules/spatial.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/spatial.py)
  that wraps every ``ST_ASGEOJSON(g)`` BigQuery AST node in
  ``CAST(... AS VARCHAR)`` before SpatialRenameRule renames it. The
  rule registers BEFORE SpatialRenameRule so it fires on the
  unrenamed BQ-cased ``Anonymous(ST_ASGEOJSON)`` node (the post-order
  rule pass visits each node once and breaks after the first
  matching rule). DuckDB function names are case-insensitive, so the
  emitted ``CAST(ST_ASGEOJSON(...) AS VARCHAR)`` executes correctly
  without further renaming. The CAST forces the DuckDB *logical*
  column type from ``JSON`` to ``VARCHAR``, which the
  ``bqemu.duckdb_type`` field-metadata override (introduced in the
  Bucket J closure) reads and surfaces on the wire as ``STRING``
  — matching real BigQuery's wire-format schema.
* A new ADR 0022 §3 sub-rule for JSON-shaped STRING tolerance: a
  STRING-typed cell whose stripped value opens with ``{`` or ``[``
  is parsed via ``json.loads`` on both sides; the parsed objects
  compare via Python's unordered ``==`` (which treats ``3`` and
  ``3.0`` as equal). This absorbs the key-order, ``int`` vs
  ``float``, and inter-token-whitespace drift between DuckDB-spatial's
  ``{"coordinates": [3.0, 4.0], "type": "Point"}`` and BigQuery's
  ``{ "type": "Point", "coordinates": [3, 4] } ``. A genuine
  semantic divergence (different field values) still surfaces as a
  mismatch; either side failing to parse falls back to exact
  equality.
* Regression coverage:
  [`tests/unit/sql/rules/test_spatial.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/rules/test_spatial.py)
  gains a ``TestStAsGeoJsonStringType`` class (3 cases pinning the
  CAST-wrap behaviour and the idempotency guard);
  [`tests/unit/conformance/test_comparison_json_string.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/conformance/test_comparison_json_string.py)
  is new and adds 18 unit cases (parametrised) covering parse-equal,
  key order, int vs float, malformed-JSON exact-equality fallback,
  non-JSON STRING values unaffected, NULL handling, REPEATED-mode
  element-wise normalisation, and one-sided JSON shape exact
  equality.

The `st_asgeojson_point` fixture moves from XFAIL (out-of-scope
GeoJSON formatting) to PASS. The GeoJSON output formatting section
is removed from
[`docs/reference/out-of-scope.md`](../reference/out-of-scope.md)
entirely. Conformance metrics after scope-expansion #18:
**631 passed + 10 xfailed** (+1 passed, -1 xfailed).

**Scope-expansion #17 follow-up (same day).** The
``script_exception_handler`` reclassification was lifted later the
same day via a scope-expansion that reconsidered the strict
division-by-zero out-of-scope entry. The closure ships:

* A new pre-translator at
  [`src/bqemulator/sql/rewriter/division_by_zero.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/division_by_zero.py)
  that walks every ``exp.Div`` BigQuery AST node (before SQLGlot's
  BQ → DuckDB transpile) and replaces it with::

  CASE WHEN <divisor> = 0
  THEN error('Division by zero')
  ELSE <numerator> / <divisor>
  END

  DuckDB's ``error(VARCHAR)`` builtin raises ``Invalid Input
  Error: Division by zero``. The script interpreter's
  ``_run_statement_with_params`` / ``_run_query_with_params``
  wraps the exception as ``InvalidQueryError``, and the
  ``BEGIN... EXCEPTION WHEN ERROR THEN... END`` block then
  catches the raise in ``_exec_begin``. The CASE form is
  critical: DuckDB's ``IF(cond, then, else)`` evaluates both
  branches eagerly, so ``IF(b=0, error(...), a/b)`` would still
  trigger DuckDB's ``Inf`` return — ``CASE`` is short-circuited
  per SQL semantics.
* The walk snapshots every ``Div`` via ``find_all`` (pre-order
  DFS) and iterates the snapshot **in reverse** so child Divs
  are wrapped before their parents — when an outer ``(a / b) /
  c`` is rewritten, its ``this`` already points at the inner
  CASE and the new outer CASE's ELSE branch carries the
  inner-wrapped shape.
* The lone optimisation: when the divisor is a non-zero numeric
  literal (``a / 2``, ``a / -3.14``), the wrap is skipped — the
  AST stays simple and the very common divide-by-constant case
  in user queries doesn't expand. A literal ``0`` divisor *is*
  wrapped so the runtime CASE always raises.
* Function-call divides — ``SAFE_DIVIDE`` (parsed as Anonymous /
  typed at the BQ AST level), ``IEEE_DIVIDE`` (Anonymous; the
  Bucket J ``IeeeDivideRule`` emits its ``Div`` in the
  post-translate rule pass) — are opaque to the walk by
  construction, so their native NULL / Inf semantics survive
  untouched. The pipeline order in
  [`src/bqemulator/sql/translator.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/translator.py)
  registers the new pre-translator AFTER ``safe_helpers`` so
  ``SAFE.X(...)`` has already been rewritten to ``TRY(...)`` —
  any user-written ``a / b`` inside the SAFE prefix lands inside
  ``TRY``, and the CASE raise gets absorbed and yields ``NULL``
  (matching BigQuery's ``SAFE.X(a / 0) = NULL`` semantic).
* Regression coverage in
  [`tests/unit/sql/rewriter/test_division_by_zero.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/sql/rewriter/test_division_by_zero.py)
  adds 35 unit cases across nine test classes — bare-Div raise
  on integer / float / ``0/0`` / column divisor, const-divisor
  optimisation (parametrised over ``2``, ``3.14``, ``1.0``,
  ``-2``, ``-3.14``), zero-literal wrap (the one case the
  optimisation must NOT skip), ``SAFE_DIVIDE`` NULL,
  ``IEEE_DIVIDE`` Inf, ``SAFE.LN(-1)`` NULL (regression guard),
  nested ``(a/b)/c`` (outer raise + inner raise + non-zero),
  window ``SUM(a/b) OVER (...)``, aggregate ``SUM(a/b)``,
  WHERE-clause ``a/b > 0``, and parse-failure tolerance.

The `script_exception_handler` fixture moves from XFAIL (out-of-
scope strict div/0) to PASS. The Strict-division-by-zero section
is removed from
[`docs/reference/out-of-scope.md`](../reference/out-of-scope.md)
entirely. Conformance metrics after scope-expansion #17:
**632 passed + 9 xfailed** (+1 passed, -1 xfailed). Conformance
metrics after the same-day scope-expansion #15 (three new
``RANGE_SESSIONIZE`` fixtures recorded + windowed-subquery
rewrite): **635 passed + 9 xfailed** (corpus 641 → 644 fixtures).

#### Bucket I — Standard-function semantic differences — Closed

**Status.** Closed. The closure ships four new
pre-translator rewriter modules (`datetime_helpers`, `json_helpers`,
`struct_helpers`, `safe_helpers`) plus a 4-argument-INSTR
extension to the existing `string_helpers` rewriter; one new
post-translate rule module (`datetime_semantics`, ten rules) plus
a `StGeometryTypeBqNameRule` addition to the existing `spatial`
module and an `UpperUnicodeRule` addition to the existing
`string_helpers` rule module; a `DateTruncQuarterRule` →
`DateTruncCalendarUnitRule` generalisation in the existing
`iso_date_parts` module; three builtin Python UDF changes —
one replacement (`bqemu_farm_fingerprint` swaps a SHA-256
stand-in for a bit-exact pure-Python port of FarmHash
``Fingerprint64``) and two new helpers (`bqemu_upper_unicode`,
`bqemu_instr_occurrence`); and a fix to the wire-format
renderer's TIMESTAMP encoder so the boundary survives
without float-precision drift.

**Sub-session I-a — date/time + FORMAT/PARSE (18 fixtures closed).**
A new
[`src/bqemulator/sql/rules/datetime_semantics.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/datetime_semantics.py)
module plus a companion pre-translator at
[`src/bqemulator/sql/rewriter/datetime_helpers.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/datetime_helpers.py)
together close every date/time / FORMAT / PARSE fixture:

* `DATE_ADD(date, INTERVAL n DAY)`, `DATE_SUB(date, INTERVAL n DAY)`,
  and `DATE_FROM_UNIX_DATE(n)` wrap in `CAST(... AS DATE)` at the
  BigQuery AST level so the function-call forms preserve their DATE
  return type. The literal `DATE '...' + INTERVAL` operator form
  (BigQuery returns DATETIME) is left alone — distinguishing the
  two forms after the SQLGlot transpile is impossible because both
  collapse to the same `Add(Cast, Interval)` shape.
* `DATE_TRUNC(date, DAY|MONTH|QUARTER|YEAR)` over a DATE-typed
  operand wraps in CAST AS DATE post-translate; the existing
  `DateTruncWeekRule` already handled WEEK's Sunday-start
  truncation, and the new generalized `DateTruncCalendarUnitRule`
  in `iso_date_parts.py` covers the remaining calendar units.
* `EXTRACT(DATE FROM ts)` rewrites to `CAST(ts AS DATE)` —
  DuckDB rejects the `DATE` specifier outright.
* `EXTRACT(DAYOFWEEK FROM x)` adds 1 to match BigQuery's
  1-indexed convention (Sun=1, Sat=7) vs DuckDB's 0-indexed
  (Sun=0, Sat=6).
* `EXTRACT(WEEK FROM x)` computes the Sunday-start Gregorian week
  via a closed-form `(DOY - 1 + DAYOFWEEK(date_trunc('year', x))) // 7`.
  The companion `ExtractIsoweekRule` still routes `ISOWEEK` through
  DuckDB's native `WEEK` (ISO 8601).
* `LAST_DAY(x, WEEK)` pre-translates to
  `DATE_ADD(x, INTERVAL 7 - EXTRACT(DAYOFWEEK FROM x) DAY)` so the
  result is the Saturday closing the Sunday-start week (BigQuery's
  semantic), wrapped in CAST AS DATE.
* `TIMESTAMP_MICROS(n)` / `TIMESTAMP_MILLIS(n)` pre-translate to
  `TIMESTAMP_ADD(TIMESTAMP '1970-01-01 00:00:00+00', INTERVAL n
  MICROSECOND|MILLISECOND)` so the result lands on TIMESTAMPTZ
  matching BigQuery's TIMESTAMP wire-format.
  `TIMESTAMP_SECONDS(n)` is left alone — SQLGlot transpiles it to
  `TO_TIMESTAMP(n)` which already returns TIMESTAMPTZ.
* `FORMAT(fmt, args)` post-translate routes through DuckDB's
  `printf` for true C-style format specifiers (`%05d`, `%.3f`,
  `%x`, `%-10s`).
* `PARSE_TIME(fmt, value)` post-translate emits
  `CAST(strptime(value, fmt) AS TIME)`; `PARSE_TIMESTAMP` wraps
  the `strptime` call in `timezone('UTC', …)` so the column
  type lands on TIMESTAMPTZ.

The `arrow_bridge` TIMESTAMP renderer switched from
`int(ts.timestamp() * 1_000_000)` to integer `timedelta`
arithmetic so the boundary survives without
float-precision drift.

**Sub-session I-b — JSON + STRUCT (5 fixtures closed).** A new
[`src/bqemulator/sql/rewriter/json_helpers.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/json_helpers.py)
pre-translator wraps `JSONFormat(to_json=True)` (`TO_JSON(...)`)
in `CAST(... AS JSON)` so the wire column lands on JSON. SQLGlot's
default transpile collapses both `TO_JSON` and `TO_JSON_STRING` to
`CAST(TO_JSON(...) AS TEXT)`, so the JSON variant has to be
re-tagged before the transpile runs. The post-translate
`JsonTypeLowerRule` wraps every `JSON_TYPE(x)` call in `LOWER(...)`
to match BigQuery's lowercase return form (`object` vs DuckDB's
`OBJECT`). A second new pre-translator at
[`src/bqemulator/sql/rewriter/struct_helpers.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/struct_helpers.py)
replaces positional `STRUCT(value, …)` calls (no `AS` aliases)
with DuckDB's `ROW(…)` constructor so the struct aligns
*positionally* with its target — matching BigQuery's
name-from-context inference for INSERT VALUES and UNION ALL chains
where the first SELECT carries explicit field aliases. Named
structs (`STRUCT(value AS field)`) flow through unchanged.

**Sub-session I-c — hash + boundary + misc (12 fixtures closed +
2 to out-of-scope).** A new
[`src/bqemulator/sql/rewriter/safe_helpers.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/safe_helpers.py)
pre-translator unwraps every `SafeFunc(inner)` BigQuery AST node
into `TRY(inner)` so `SAFE.LN(-1)` / `SAFE.SQRT(-1)` /
`SAFE.LOG(...)` survive the table-rewriter's project-qualification
pass. The 4-arg `INSTR(haystack, needle, position, occurrence)`
pre-translates (via an extension to the existing
[`string_helpers.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/string_helpers.py))
to a `bqemu_instr_occurrence` Python helper. Two new
post-translate rules close the remaining standard-function gaps:
`ApproxCountDistinctExactRule` replaces
`APPROX_COUNT_DISTINCT` with the exact `COUNT(DISTINCT)` (DuckDB's
HyperLogLog stand-in returns 11 for a 10-distinct set);
`ApproxQuantilesDiscreteRule` routes `APPROX_QUANTILE` through
DuckDB's discrete `quantile_disc` aggregate so the per-quartile
values match BigQuery's sample-based `APPROX_QUANTILES` output.
`ConcatStringTypeRule` wraps every `||` DPipe in `CAST(... AS
VARCHAR)` so the wire-format column type stays STRING even when
one operand collapses to a typed NULL (DuckDB infers INTEGER for
an all-NULL projection). `StGeometryTypeBqNameRule` maps DuckDB's
uppercase WKT type names (`POINT`, `MULTIPOINT`, …) to BigQuery's
`ST_<PascalCase>` form (`ST_Point`, `ST_MultiPoint`, …) via an
inline CASE — the rule registers *before* `SpatialRenameRule` so
it fires on the unrenamed `ST_GEOMETRYTYPE` and the enclosing CASE
survives the post-order pass. The Python helpers
`bqemu_upper_unicode` (Python `str.upper` for the `ß` → `SS`
case-fold rule), `bqemu_instr_occurrence` (4-argument INSTR
semantics including negative-start and zero-occurrence edge
cases), and a pure-Python port of FarmHash `Fingerprint64`
(`bqemu_farm_fingerprint` — replaces the SHA-256 stand-in with a
bit-exact implementation covering 0-16, 17-32, 33-64, and 65+
byte input paths) close the remaining function-level fixtures.

One fixture lands in
[`docs/reference/out-of-scope.md`](../reference/out-of-scope.md)
rather than closing:

* `standard_functions/bound_bignumeric_max` — BIGNUMERIC value is
  39 integer + 38 fractional digits, exceeding DuckDB's
  `DECIMAL(38, …)` cap. Matching BigQuery's full BIGNUMERIC range
  requires either bundling a wide-decimal library or replacing
  DuckDB as the storage engine, both far beyond what a single
  fixture warrants.

`routines_scripting/script_exception_handler` was also pinned to
`out-of-scope.md` at the Bucket I close — BigQuery's `/` raises
on a zero divisor where the emulator mirrored DuckDB and returned
`Inf`. The out-of-scope reclassification was reconsidered the
same day as scope-expansion #17 and closed via a new
`division_by_zero` pre-translator. See the scope-expansion #17
closure note below for details.

**Affected fixtures**: 38 entries at the start of the closure
session, all triaged. 36 closed directly (the count includes
`null_date_add`, which is in sub-cluster I.8 but the I-a
``DATE_ADD``-cast pre-translate fired on its
``DATE_ADD(NULL, INTERVAL...)`` shape ahead of the I-c boundary
sub-session — it XPASSed during the I-a run); 7 Bucket H
``ST_GeometryType`` entries also XPASSed via the new
`StGeometryTypeBqNameRule`; 2 Bucket I entries pinned to
`out-of-scope.md` per the table above. See
[`tests/conformance/divergences.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py)
for the closure annotations.

**Net delta**: conformance 580 passed + 61 xfailed → **623
passed + 18 xfailed** (-43 xfailed = 36 Bucket I direct
closures + 7 Bucket H incidental closures; 2 Bucket I
out-of-scope remain pinned with revised rationales).

#### Bucket J — Emulator-side missing function translation — Closed

**Status.** Closed. The closure lands the SQLGlot
translation rules and three new pre-translator rewriters required
to ship the 44 BigQuery builtins the slice-2 corpus exercises. The
work spans:

1. **SAFE arithmetic** —
   [`src/bqemulator/sql/rules/safe_math.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/safe_math.py)
   now wraps the typed ``exp.SafeAdd`` / ``SafeSubtract`` /
   ``SafeMultiply`` / ``SafeNegate`` nodes in DuckDB's ``TRY(...)``
   so a BIGINT overflow surfaces as ``NULL`` instead of an
   ``OutOfRangeException``. ``SAFE_NEGATE`` uses ``TRY(0 - a)``
   rather than ``TRY(-a)`` so the ``INT64.MIN`` overflow (where
   DuckDB silently auto-promotes to HUGEINT) cleanly errors and
   ``TRY`` converts the error to ``NULL`` — matching BigQuery's
   ``SAFE_NEGATE(-9223372036854775808) = NULL`` semantic.

2. **JSON helpers** —
   [`src/bqemulator/sql/rules/json_helpers.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/json_helpers.py)
   adds rules for ``JSON_KEYS`` (DuckDB has it but SQLGlot
   mis-translates the name to ``J_S_O_N_KEYS_AT_DEPTH``),
   ``LAX_BOOL`` / ``LAX_INT64`` / ``LAX_FLOAT64`` / ``LAX_STRING``
   (route through ``TRY_CAST(json_extract_string(j, '$') AS T)``),
   ``BOOL(json)`` / ``FLOAT64(json)`` (cast to BOOLEAN / DOUBLE),
   and ``STRING(json)`` (rewrites the SQLGlot-generated
   ``CAST(json AS TEXT)`` to ``json_extract_string(j, '$')`` so the
   JSON quotes are stripped). ``JSON_REMOVE`` / ``JSON_SET`` /
   ``JSON_STRIP_NULLS`` route through Python helpers registered by
   the new
   [`src/bqemulator/sql/builtin_udfs.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/builtin_udfs.py)
   module, which DuckDB calls via
   ``DuckDBPyConnection.create_function`` at engine startup. A
   companion ``JSONExtractToStringRule`` wraps the DuckDB ``->``
   operator (which SQLGlot emits for BigQuery's ``JSON_QUERY``) in
   ``CAST(... AS VARCHAR)`` so the column lands as ``STRING`` — matching
   BigQuery's ``JSON_QUERY`` return type.

3. **String / bytes** —
   [`src/bqemulator/sql/rules/string_helpers.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/string_helpers.py)
   adds rules for ``OCTET_LENGTH`` / ``BYTE_LENGTH`` (``CASE TYPEOF``
   dispatch between ``strlen`` for VARCHAR and ``octet_length`` for
   BLOB), ``CODE_POINTS_TO_STRING`` (``array_to_string(list_transform(
   arr, x -> chr(x)), '')``), ``TO_CODE_POINTS``
   (``list_transform(string_split(s, ''), c -> ord(c))``), and
   ``SAFE_CONVERT_BYTES_TO_STRING`` (``TRY(DECODE(...))``).
   NORMALIZE / NORMALIZE_AND_CASEFOLD route through Python helpers via
   the new
   [`src/bqemulator/sql/rewriter/string_helpers.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/string_helpers.py)
   pre-translator rewriter — SQLGlot collapses the ``is_casefold``
   flag during the DuckDB transpile, so the dispatch must happen
   while the BigQuery AST still distinguishes the two forms.

4. **ISO date parts** —
   [`src/bqemulator/sql/rules/iso_date_parts.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/iso_date_parts.py)
   rewrites ``EXTRACT(ISOWEEK FROM x)`` to ``EXTRACT(WEEK FROM x)``
   (DuckDB's ``WEEK`` is already ISO-8601) and wraps
   ``DATE_TRUNC(date, ISOYEAR)`` in ``CAST(... AS DATE)`` so the
   column type lands on ``DATE`` (DuckDB's ``DATE_TRUNC`` returns
   ``TIMESTAMP`` without the cast).

5. **Aggregate variants** —
   [`src/bqemulator/sql/rewriter/aggregate_variants.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/aggregate_variants.py)
   adds a pre-translator pass for three DuckDB-incompatible
   aggregate shapes BigQuery accepts: ``ARRAY_AGG(x ORDER BY k
   LIMIT n)`` rewrites to ``array_slice(array_agg(x ORDER BY k),
   1, n)``; ``STRING_AGG(x, sep ORDER BY k LIMIT n)`` rewrites to
   ``array_to_string(array_slice(array_agg(x ORDER BY k), 1, n),
   sep)``; ``ARRAY_AGG(expr IGNORE NULLS …)`` rewrites to
   ``ARRAY_AGG(expr …) FILTER (WHERE expr IS NOT NULL)`` so the
   null-skipping behaviour is preserved through the SQLGlot
   transpile (which would otherwise silently drop ``IGNORE
   NULLS``).

6. **Numeric literal precision** —
   [`src/bqemulator/sql/rewriter/numeric_literals.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/numeric_literals.py)
   replaces ``NUMERIC 'literal'`` with ``CAST('literal' AS DECIMAL(38, 9))``
   and ``BIGNUMERIC 'literal'`` with ``CAST('literal' AS DECIMAL(38, 38))``
   *before* SQLGlot transpile so the explicit precision survives —
   without it, SQLGlot emits ``CAST(... AS DECIMAL)`` which DuckDB
   resolves to ``DECIMAL(18, 3)`` and rejects every literal over 18
   digits.

7. **Misc** —
   [`src/bqemulator/sql/rules/misc_helpers.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rules/misc_helpers.py)
   adds rules for ``IEEE_DIVIDE`` (``CAST(a AS DOUBLE) / CAST(b AS
   DOUBLE)`` — DuckDB's float division yields ``±Inf`` for zero
   divisors), ``FARM_FINGERPRINT`` (routes through
   ``bqemu_farm_fingerprint``), ``RANGE_BUCKET`` (rewrites to
   ``len(list_filter(boundaries, x -> x <= point))``), and
   ``APPROX_TOP_SUM`` (collapses to ``approx_top_k(value, k)``).

8. **Engine-side support** —
   [`src/bqemulator/storage/engine.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/storage/engine.py)
   gains a ``_register_builtin_udfs`` hook called from ``start`` so
   every connection carries the Python helpers, and ``fetch_arrow``
   now annotates each output column with its DuckDB-side type as
   ``bqemu.duckdb_type`` field metadata. The REST schema renderer
   ([`build_response_schema`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/executor.py))
   consults that metadata for ``JSON``-typed columns so
   ``PARSE_JSON`` / ``JSON_OBJECT`` / ``JSON_ARRAY`` outputs surface
   on the wire as ``type: "JSON"`` rather than the Arrow-derived
   ``STRING``.

**Outcome at closure.** All 44 originally-pinned Bucket J fixtures
have been triaged. 41 XPASSed and were removed from
`divergences.py`; 3 cascaded to Bucket I (the function now exists
but its output diverges in value or precision): ``agg_approx_quantiles``
(APPROX_QUANTILE algorithm differs), ``math_rand_ish_deterministic``
(``bqemu_farm_fingerprint`` is not bit-exact FarmHash), and
``bound_bignumeric_max`` (39-integer-digit BIGNUMERIC literal
exceeds DuckDB's DECIMAL(38, …) cap). Two further fixtures
incidentally XPASSed via the Bucket J + JSON-metadata path:
``standard_functions/bound_numeric_min`` (previously Bucket I —
NUMERIC negative-literal precision drift, closed by the numeric-
literal rewriter) and ``standard_functions/json_parse_basic``
(previously Bucket I — JSON-type round-tripping, closed by the
field-metadata + JSONExtract rules). Net registry delta: -45
entries (44 direct + 2 incidental − 1 reclassified-from-elsewhere).
Conformance metrics move from 482 passed + 159 xfailed to
**525 passed + 116 xfailed**.

**Definition (historical).** Any query that calls a BigQuery
builtin whose SQLGlot translation to a DuckDB equivalent (or a
DuckDB UDF the emulator registers) is missing. Manifests as a
DuckDB ``Catalog Error: Scalar Function with name X does not exist!``
percolating up as a 400 BadRequest.

**Root cause (historical).** The SQLGlot transpiler (Phase 1)
shipped translations only for the BigQuery functions exercised in
earlier phases. Functions that weren't on Phase 1–10's path —
``SAFE_ADD``, ``BYTE_LENGTH``, ``FARM_FINGERPRINT``, ``JSON_KEYS``,
``NORMALIZE``, ``IEEE_DIVIDE``, the ``STRING(...)`` / ``INT64(...)``
/ ``FLOAT64(...)`` JSON extractors, the ``LAX_*`` family, etc. —
never got a translation rule. Real BigQuery accepts them; the
emulator rejected them at the SQL compile stage.

### 2. Divergence registry shape

`tests/conformance/divergences.py` is the single source of truth
for the xfail list. Each entry is one line:

```python
"<phase>/<fixture_name>": "Bucket <X> — <one-line summary> (ADR 0023 §<bucket>)",
```

The rationale string always includes the bucket reference so a
reader can grep the ADR for the bucket's full analysis.

### 3. Pass-rate computation under xfail

The pass-rate gate (ADR 0022 §5, Option A) is the fraction of
non-xfail'd fixtures that pass. With the divergences listed in
`divergences.py` after the scope-expansion #15
(``RANGE_SESSIONIZE`` reconsidered, three new fixtures recorded),
the calculation is:

* Total fixtures: **644**
* Recorded baselines: 644
* Marked `xfail(strict=True)` via this ADR / ADR 0019 /
  out-of-scope.md: **9**
* Non-xfail'd fixtures: 635
* Passing among non-xfail'd: 635
* **Pass rate: 635 / 635 = 100%**

The 9 residual xfailed entries break down as:
* 8 ADR 0019 spheroidal-vs-planar GEOGRAPHY entries
  (5 continental + 3 small-scale reclassified from Bucket H);
* 1 out-of-scope ADR 0023 §1.I entry pinned in
  ``out-of-scope.md`` (``bound_bignumeric_max``).

The ``RANGE_SESSIONIZE`` entry closed via scope-
expansion #15. The closure added three new conformance fixtures recorded
against real BigQuery (``range_sessionize_basic`` /
``range_sessionize_grouped`` /
``range_sessionize_overlaps_option``) under
``specialized_types/``; xfailed stays at 9 and passed
grows by 3 (corpus size 641 → 644). The implementation ships a
new pre-translator at
[`src/bqemulator/sql/rewriter/range_sessionize.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/range_sessionize.py)
that rewrites every BigQuery ``RANGE_SESSIONIZE(TABLE <ref>,
'<range_col>', [<part_cols>] [, '<sessionize_option>'])`` call
into a windowed gaps-and-islands subquery; ``MEETS`` (default)
and the ``OVERLAPS_OR_MEETS`` alias use strict ``>`` for the
new-session predicate while ``OVERLAPS`` uses ``>=``. A second
pass ``_rewrite_range_data_types`` in
[`src/bqemulator/sql/rewriter/specialized_types.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/specialized_types.py)
extends the existing pre-translator to convert ``RANGE<T>``
column-type / non-literal-CAST references to ``STRUCT<\`start\`
T, \`end\` T>`` so DDL like ``CREATE TABLE t (col RANGE<DATE>)``
survives the DuckDB parser. The pre-existing
``RangeSessionizeRejectRule`` post-translate rule is removed.

(At slice-2 close the counts were 199 / 442; after Bucket A on they were 175 / 466; after Bucket C the same day they
moved to 167 / 474; after Bucket D the same day they moved to 163
/ 478; after Bucket E the same day they were 162 / 479; after
Bucket F they were 159 / 482; after the Bucket J
closure later that day they were 116 / 525; after the Bucket B
closure the same day they were 82 / 559; after the Bucket G
closure later that day they were 61 / 580; after the Bucket I
closure they were 18 / 623; after the Bucket H
closure later the same day they were 11 / 630; after scope-
expansion #18 (GeoJSON output formatting, reconsidered from
out-of-scope.md) closed `st_asgeojson_point` later the same day
they were 10 / 631; and after scope-expansion #17 (strict
division-by-zero raising, reconsidered from out-of-scope.md)
closed `script_exception_handler` later the same day they were
9 / 632; and after scope-expansion #15 (`RANGE_SESSIONIZE` TVF,
reconsidered from out-of-scope.md) landed three new conformance
fixtures and the windowed-subquery rewrite the same day, the
corpus grows from 641 to 644 fixtures with the counts at
**9 / 635**. The Bucket H delta was -7 (7 direct XPASSes + 4
reclassifications-out, of which 1 was the GeoJSON fixture that
subsequently closed via #18; the net effect of all four same-day closures (Bucket H + scope-#18 + scope-#17 +
scope-#15) on the xfail count is -9 and on passed is +5.)

The ≥85% gate is met with a margin: 635 / 644 ≈ 98.6% of the
corpus passes at the close of the ADR 0023 bucket-closure work,
with the residual 1.4% pinned to load-bearing design decisions
(ADR 0019 spheroidal-vs-planar GEOGRAPHY) or explicit
scope-budget exclusions (out-of-scope.md). The `strict=True` flag
ensures that any future closure of those divergences shows up as
an unexpected pass (XPASS), forcing a removal of the entry before
the next merge.

**Update (same day): P3.a error-message-shape parity**
landed as a separate workstream (governed by ADR 0022 §3, not this
ADR). It added 20 new error-shape fixtures to the corpus (644 →
664) and rewrote the emulator's error renderer to match BigQuery's
wire format; conformance ratcheted to 655 / 664 ≈ 98.6% non-
divergent pass with the same 9 xfailed entries as above (zero new
divergences pinned by the P3.a closure).

The 100% pass rate against the non-divergent corpus is meaningful
because the corpus is broad: 635 distinct SQL patterns across
literal handling, control flow, set operations, joins, subqueries,
CTEs, window functions, DML, partitioning, clustering, wildcard
tables, snapshots, views, GEOGRAPHY / RANGE / INTERVAL types,
≈400 standard functions including their NULL / empty / boundary
/ Unicode edge cases, and real-world patterns (multi-CTE chains,
complex windows, QUALIFY, PIVOT/UNPIVOT, a TPC-H Q1/Q3/Q5/Q6/Q10
subset).

### 4. ADR 0023 lifecycle

This ADR shrinks over time. Each Phase 11 follow-up slice that
closes a bucket:

1. Removes the affected entries from `divergences.py`.
2. Updates the corresponding bucket section here with a closure
   note (date, slice id, PR reference).
3. Notes whether the bucket is *fully* closed or only partially —
   partial closure keeps the bucket with a smaller fixture list.

When every bucket is closed, this ADR is marked `Status:
Superseded` with a pointer at the final all-passing conformance
report.

## Consequences

- **Positive.** The slice-2 divergences are catalogued, not hidden.
  Each future slice's work has a clear scope: close one or more
  buckets, then re-run the corpus, expect the corresponding entries
  to XPASS (which fails the strict gate, forcing the entry to be
  removed in the same PR). Buckets A, C, D and E all closed on
  — A removed 24 entries (16 direct XPASS + 6 Bucket I
 + 2 Bucket J incidental), C removed all 8 of its own, D removed
   all 4 of its own, and E removed 1 (the second fixture revealed a
   second-order divergence and was reclassified to Bucket I).
   Bucket F closed — all 3 of its own. Bucket J closed
   the same day — 41 direct XPASSes + 3 J→I cascades + 2 incidental
   Bucket I closures (`bound_numeric_min`, `json_parse_basic`).
   Bucket B closed the same day — 22 direct XPASSes + 5 incidental
   Bucket H closures + 7 incidental Bucket I closures. Bucket G
   closed the same day — 20 direct XPASSes + 1 incidental Bucket I
   closure (`specialized_types/interval_zero`). Bucket I
   closed — 36 direct Bucket I closures (the 36 includes
   `null_date_add`, which XPASSed early in the I-a run when the
   `DATE_ADD`-cast pre-translate fired on its NULL-operand shape)
   plus 7 incidental Bucket H closures via the new
   `StGeometryTypeBqNameRule`; 2 Bucket I entries pinned to
   `out-of-scope.md` with revised rationales (`bound_bignumeric_max`
   for DuckDB's `DECIMAL(38, …)` cap; `script_exception_handler` for
   the div/0 raise-scope budget). Bucket H closed — 7
   direct XPASSes via the ADR 0022 §3 WKT-shaped STRING amendment
 + 4 reclassifications: 3 small-scale spheroidal entries
   (`st_centroid_polygon`, `st_intersection_polygons`,
   `st_dwithin_no`) shifted under ADR 0019 because the divergence
   is geometric, not stringification; 1 GeoJSON-formatting entry
   (`st_asgeojson_point`) shifted to
   `out-of-scope.md#geojson-output-formatting` because closing it
   would need either a custom GeoJSON formatter or a
   schema-comparator relaxation (STRING ≡ JSON) larger than the
   fixture's value. All ten buckets are now closed; the closure
   loop worked end-to-end and the reclassification path caught
   mislabelled fixtures honestly.

- **Positive.** The 85% gate is met honestly: every non-xfail'd
  fixture actually matches BigQuery. At slice-2 close that was
  442/641 ≈ 69% of the corpus passing as non-divergent; after the
  Bucket A closure it was 466/641 ≈ 73%; after the
  same-day Bucket C closure it moved to 474/641 ≈ 74%; after the
  same-day Bucket D closure it moved to 478/641 ≈ 75%; after the
  same-day Bucket E closure it moved to 479/641 ≈ 75%; after the
  Bucket F closure it moved to 482/641 ≈ 75%; after
  the same-day Bucket J closure it was 525/641 ≈ 82%; after the
  same-day Bucket B closure it was 559/641 ≈ 87%; after the
  same-day Bucket G closure it was 580/641 ≈ 90%; after the
  Bucket I closure it moved to 623/641 ≈ 97%; after
  the same-day Bucket H closure it moved to 630/641 ≈ 98.3%; after
  the same-day scope-expansion #18 (GeoJSON output formatting,
  reconsidered) it was 631/641 ≈ 98.4%; after the same-day
  scope-expansion #17 (strict division-by-zero raising,
  reconsidered) it was 632/641 ≈ 98.6%; and after the same-day
  scope-expansion #15 (`RANGE_SESSIONIZE` reconsidered, three new
  fixtures recorded against real BigQuery) it is 635/644 ≈ 98.6%
  (corpus grew from 641 to 644).

- **Negative.** Slice 2 closed with 199 fixtures pinned to xfail
  (≈ 31% of the 641-fixture corpus); after all ten ADR 0023
  buckets (A through J) closed AND scope-expansions #18, #17,
  and #15 all landed, the count is **9** (≈ 1.4% of the now
  644-fixture corpus). The "useful information" of the corpus
  at any given checkpoint is the passing-fixture count (currently
  **635**). The xfail'd set documents what doesn't work —
  arguably more valuable in the long run — and the residual 9
  entries are a stable mix of permanent design divergences
  (ADR 0019 spheroidal-vs-planar) and explicit scope exclusions
  (out-of-scope.md). Ten buckets and three scope-expansions
  closed across three days demonstrates the closure loop works
  end-to-end.

- **Positive (resolved).** Bucket H's closure required amending
  ADR 0022 §3 to add a WKT-shaped STRING sub-rule. The amendment
  landed in the same PR as the closure: a STRING-typed
  cell whose value matches the anchored WKT geometry-type regex
  routes through the existing GEOGRAPHY whitespace +
  capitalisation normalisation. The regex is tight enough that
  unrelated STRING values (URLs, JSON, ordinary prose) are untouched, and
  the rule only fires when both sides match the WKT shape — so
  one-sided drift surfaces as a real mismatch.

- **Positive (resolved).** Scope-expansion #18 (later the same
  day) reconsidered the GeoJSON output-formatting
  out-of-scope entry the Bucket H closure had just added. The
  closure required two coordinated changes: a new
  `StAsGeoJsonStringTypeRule` in
  `src/bqemulator/sql/rules/spatial.py` wraps every
  ``ST_AsGeoJSON(g)`` BigQuery AST node in
  ``CAST(... AS VARCHAR)`` so the wire-format schema lands on
  STRING (matching BigQuery), and a second ADR 0022 §3 amendment
  added a JSON-shaped STRING sub-rule that absorbs the content-
  level formatting drift via ``json.loads`` parse-equal. Both
  amendments are precisely scoped (the SQL rule fires only on
  ``ST_ASGEOJSON``; the comparison rule applies only when both
  sides open with ``{`` or ``[``), so neither has spillover
  effects on unrelated types. The ``st_asgeojson_point`` fixture
  XPASSed and the GeoJSON entry was removed from
  ``out-of-scope.md`` the same day. Conformance ratchet:
  630 → 631 passed, 11 → 10 xfailed.

- **Positive (resolved).** Scope-expansion #17 (later the same
  day) reconsidered the strict division-by-zero
  out-of-scope entry the Bucket I closure had just added. A new
  pre-translator at
  `src/bqemulator/sql/rewriter/division_by_zero.py` walks every
  `exp.Div` BigQuery AST node and wraps it in
  `CASE WHEN divisor = 0 THEN error('Division by zero') ELSE
  numerator / divisor END`. The walk iterates the
  ``find_all(exp.Div)`` snapshot in reverse so child Divs are
  rewritten before parents (nested ``(a/b)/c`` gets both wraps);
  a const-divisor optimisation skips the wrap when the divisor
  is a non-zero numeric literal. Function-call divides
  (`SAFE_DIVIDE`, `IEEE_DIVIDE`) are opaque ``Anonymous`` /
  typed nodes at the BigQuery AST level when this pre-translator
  runs, so their native NULL / Inf semantics survive untouched.
  The rewriter registers AFTER ``safe_helpers`` so any
  user-written ``a / b`` inside ``SAFE.X(...)`` lands inside a
  ``TRY`` shell — the CASE raise gets absorbed and yields
  ``NULL``. The `script_exception_handler` fixture XPASSed and
  the strict-div/0 entry was removed from ``out-of-scope.md``
  the same day. Conformance ratchet: 631 → 632 passed,
  10 → 9 xfailed.

- **Positive (resolved).** Scope-expansion #15 (later the same
  day) reconsidered the ``RANGE_SESSIONIZE`` TVF
  out-of-scope entry. A new pre-translator at
  `src/bqemulator/sql/rewriter/range_sessionize.py` rewrites
  every ``RANGE_SESSIONIZE(TABLE <ref>, '<range_col>',
  [<part_cols>] [, '<sessionize_option>'])`` call into a
  windowed-subquery gaps-and-islands sessionisation expansion.
  The rewrite operates on the raw SQL text because SQLGlot's
  BigQuery parser doesn't accept the ``TABLE <ref>`` keyword in
  TVF arguments. Mode dispatch matches the documented BigQuery
  semantics: ``MEETS`` (default) and the ``OVERLAPS_OR_MEETS``
  alias use strict ``>`` (touching ranges share a session);
  ``OVERLAPS`` uses ``>=`` (touching ranges form separate
  sessions). The expansion emits a ``RANGE(MIN OVER …,
  MAX OVER …)`` constructor that the existing
  ``rewrite_specialized_types`` pass picks up and converts to
  the canonical STRUCT shape; the resulting
  ``STRUCT("start" T, "end" T)`` column lands as ``RANGE<T>`` on
  the wire via the Bucket G ``detect_range_element`` path. A
  second pass ``_rewrite_range_data_types`` extends
  ``specialized_types`` to convert ``RANGE<T>`` column-type /
  non-literal-CAST references to ``STRUCT<\`start\` T, \`end\`
  T>`` so DDL like ``CREATE TABLE t (col RANGE<DATE>)`` survives
  the DuckDB parser (SQLGlot otherwise transpiles ``RANGE<DATE>``
  to the unimplemented ``RANGE(DATE)`` type). The pre-existing
  ``RangeSessionizeRejectRule`` post-translate rule is removed.
  Three new conformance fixtures recorded against real BigQuery
  (`range_sessionize_basic`, `range_sessionize_grouped`,
  `range_sessionize_overlaps_option`) all pass against the
  emulator. The ``RANGE_SESSIONIZE`` entry is removed from
  ``out-of-scope.md``. Conformance ratchet: 632 → 635 passed,
  xfailed unchanged at 9, corpus size 641 → 644.

- **All reconsidered scope expansions landed.** No further
  reconsiderations are scheduled pre-v1.0.

## References

- [ADR 0022](0022-conformance-corpus-design.md) — defines the
  divergence registry shape and the pass-rate gate.
- [ADR 0019](0019-specialized-types.md) — original spheroidal-vs-
  planar GEOGRAPHY divergence that anchors the slice-2 entries
  already in the registry at slice 1 close.
- Phase 11 roadmap doc — the
  list of remaining slices, each of which closes one or more
  buckets here.
- [`tests/conformance/divergences.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py) —
  the registry referenced by the ADR.
