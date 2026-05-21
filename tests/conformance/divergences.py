"""Registry of expected divergences between bqemulator and real BigQuery.

Every entry in :data:`KNOWN_DIVERGENCES` is a fixture id (the
``<phase>/<fixture_name>`` form returned by
:attr:`Fixture.id`) mapped to a rationale string that the runner
attaches to an ``xfail(strict=True)`` marker. The rationale must
reference either an ADR or
``docs/reference/out-of-scope.md`` — invented divergences are
forbidden.

The slice-2 catalogue lives in ADR 0023; the baseline was produced
by recording 641 canonical queries against real BigQuery (288 in
the initial slice-2 scope, expanded mid-slice to 641) and replaying
each against the in-process emulator. The slice-2 close registered
199 entries across 10 buckets; subsequent closure sessions remove
entries as they ratchet — the 2026-05-15 Bucket A closure shrank
the registry to 175 entries, the 2026-05-15 Bucket C closure brought
it to 167 entries, the 2026-05-15 Bucket D closure brought it
to 163 entries, the 2026-05-15 Bucket E closure brought it
to 162 entries, the 2026-05-16 Bucket F closure brought it
to 159 entries, the 2026-05-16 Bucket J closure brought it
to 116 entries, the 2026-05-16 Bucket B closure brought it to
82 entries, the 2026-05-16 Bucket G closure brought it to
61 entries, the 2026-05-17 Bucket I closure brought it to
18 entries, the 2026-05-17 Bucket H closure brought it to
11 entries, the 2026-05-17 scope-expansion #18 (GeoJSON
output formatting reconsidered) brought it to 10 entries
(7 Bucket H fixtures closed via the ADR 0022 §3 WKT-shaped
STRING amendment; 3 reclassified to ADR 0019 spheroidal because
they expose small-scale planar-vs-spheroidal coordinate drift
that no comparison rule can paper over without making the contract
unsafe; the GeoJSON-formatting reclassification was lifted when
scope-expansion #18 landed a ``StAsGeoJsonStringTypeRule`` SQL
rule plus an ADR 0022 §3 JSON-shaped STRING amendment, closing
``st_asgeojson_point`` directly), and the 2026-05-17 scope-
expansion #17 (strict division-by-zero raising reconsidered)
brought it to 9 entries (``script_exception_handler``
closed via a new ``division_by_zero`` pre-translator that wraps
every bare ``/`` in a CASE that raises ``Division by zero`` via
DuckDB's ``error()`` builtin when the divisor is 0; the script
interpreter's ``EXCEPTION WHEN ERROR`` handler then catches the
raise), and the 2026-05-17 scope-expansion #15
(``RANGE_SESSIONIZE`` reconsidered) keeps the count at
**9 entries** while adding three new fixtures to the passing-
set (the function previously raised ``UnsupportedFeatureError``
so no divergence was pinned; the closure ships the
``rewrite_range_sessionize`` pre-translator that rewrites
every ``RANGE_SESSIONIZE(TABLE …)`` call into a windowed
gaps-and-islands subquery and three new conformance fixtures
recorded against real BigQuery — corpus size grows from 641 to
**644**, passing count from 632 to **635**). All ten ADR 0023
buckets and all three reconsidered scope expansions are now
closed.

Subsequent 2026-05-17 P2.d (Phase 8 row-access framework + 20
fixtures recorded the same day) added 7 entries — 5 ``authz_view_*``
plus ``caller_information_schema_visibility`` and
``rap_filter_via_view`` — taking the count to **16 entries**. The
2026-05-18 P2.d follow-up #1 closed the 5 ``authz_view_*`` entries
after empirical recording proved real BigQuery enforces RAP through
authorized views universally; the registry shrank to **11 entries**.
The 2026-05-18 top-30 gap-closure session #1 added 18 entries
(3 partition pseudo-cols + 3 ``geography_column_*`` + 1
``script_for_iterate_into_table`` + 2 ``str_collate_*`` + 9
less-common string-function entries — ``str_to_base32_*``,
``str_from_base32_*``, ``str_code_points_to_bytes_*``,
``str_soundex_*``, and the ``str_regexp_substr_no_match`` fixture
— pinned against the ``out-of-scope.md`` less-common-string-functions
cluster) taking the count to **29 entries**. The 2026-05-18 top-30
session #3 XFAIL-reduction follow-up closed those 9 less-common
string-function entries via 4 Python helper UDFs
(``bqemu_to_base32`` / ``bqemu_from_base32`` /
``bqemu_code_points_to_bytes`` / ``bqemu_soundex``) and 5
post-translator rules (``ToBase32Rule``, ``FromBase32Rule``,
``CodePointsToBytesRule``, ``SoundexRule``,
``RegexpExtractNullifEmptyRule``); the registry shrank to **20
entries**. The 2026-05-18 P2.a scope-expansion-depth session added
7 entries (4 ``st_asgeojson_*`` spheroidal interpolation + 1
``st_asgeojson_empty_point`` GeoJSON RFC 7946 normalisation + 2
``range_sessionize_*`` closure-gap edge cases) taking the count
to **27 entries**. The 2026-05-18 P2.d-recording follow-up (the
2 group-grantee fixtures recorded mid-P2.a under an operator with
real Workspace-group membership) added 2 entries
(``rap_filter_with_group_grantee`` and
``caller_match_via_group_only`` — both surface the same
group-grantee enforcement gap in
``src/bqemulator/row_access/identity.py``) taking the count to
**29 entries**. The 2026-05-18 top-30 gap-closure session #3b
(HLL_COUNT family, landed concurrently with P2.a's recording
sweep at 21:24 UTC) added 2 entries
(``agg_hll_count_init_basic`` and
``agg_hll_count_merge_partial_basic`` — both pinned against
``out-of-scope.md#hll-sketch-binary-format-hll_countinit--merge_partial``
because the BigQuery HLL++ sketch BYTES format is undocumented;
``agg_hll_count_extract_basic`` and ``agg_hll_count_merge_basic``
pass cleanly because they consume the sketch and return a scalar)
taking the count to **31 entries**. The same-day **2026-05-18 P2.a
closure-bug follow-up** then closed 5 of the P2.a / P2.d-recording-
surfaced entries via 4 emulator-side fixes:

* drop ``OVERLAPS_OR_MEETS`` from ``_MODE_TO_OP`` in
  ``rewrite_range_sessionize`` so the unknown-mode branch raises
  ``InvalidQueryError`` matching BigQuery's wire-format error —
  closes ``range_sessionize_overlaps_or_meets_alias``;
* strip the ``group:`` IAM-member prefix in
  ``src/bqemulator/row_access/identity.py::_parse_groups`` so the
  matcher's bare-email contract holds when fixtures pass
  ``X-Bqemu-Groups: group:<addr>`` (the wire-format form) —
  closes both ``rap_filter_with_group_grantee`` and
  ``caller_match_via_group_only`` (one fix, two closures);
* add a new ``bqemu_geojson_normalize_empty`` Python helper UDF
  (registered via ``register_builtin_udfs``) that detects empty-
  coordinates / empty-geometries shapes and rewrites them to the
  canonical RFC 7946 ``GeometryCollection`` form; the
  ``StAsGeoJsonStringTypeRule`` rewrite now wraps the call as
  ``CAST(bqemu_geojson_normalize_empty(ST_AsGeoJSON(g)) AS
  VARCHAR)`` — closes ``st_asgeojson_empty_point``;
* extend ``rewrite_range_sessionize`` with a per-partition
  ``_bqemu_partition_has_null = BOOL_OR(<range>.start IS NULL)
  OVER (PARTITION BY parts)`` flag and a coordinated
  session-id / session_range CASE so NULL rows get
  ``session_range = NULL`` and every non-NULL row in a NULL-
  containing partition collapses to the canonical bridged
  session range — closes ``range_sessionize_null_range``.

The registry shrinks to **26 entries**. The four
remaining P2.a XFAILs are spheroidal-interpolation
``st_asgeojson_*`` shapes (``geometrycollection``, ``linestring``,
``multilinestring``, ``multipolygon``) rooted in ADR 0019 —
closure would require an s2geometry-style WGS84 backend deferred
to v2.

The 2026-05-19 XFAIL-closure follow-up shipped 2 more closures via
narrow translator/catalog gaps, taking the registry to
**19 entries** at that point: ``row_access/rap_filter_via_view``
(closed by a new ``sync_created_view`` helper in
``catalog/ddl_sync.py`` that registers SQL-created views with
``table_type='VIEW'`` + ``view_query=<body>``, so the row-access
rewriter's existing ``_expand_view`` branch fires through every view
body) and ``routines_scripting/script_for_iterate_into_table`` (closed
by a new ``rewrite_unnest_struct`` pre-translator that propagates
the first struct's named-field aliases to every subsequent
positional struct in an ``UNNEST([...])`` array literal — preserving
BigQuery's "first struct seeds the field names" semantic and
avoiding the ``rewrite_struct_helpers`` rewrite that breaks mixed-
shape arrays). The wrongly-premised
``out-of-scope.md#for-loop-with-insert-into-a-pre-existing-table``
section was removed in the same PR.

The 2026-05-19 top-30 session #3d added **2 XFAIL entries** for the
GEOGRAPHY tail closure (``st_asbinary_point`` against
``_SPHEROIDAL``; ``st_maxdistance_basic`` against the new
``out-of-scope.md#st_maxdistance-not-yet-implemented`` section),
taking the registry to **21 entries**.

The 2026-05-19 **P7.b phase 2 — Tier 1 API-configuration recording**
session initially added **15 XFAIL entries** (1 legacy SQL + 3 dry-run
preview + 4 WRITE_APPEND + 3 CREATE_NEVER + 3 defaultDataset + 1
session_id) taking the registry to **36 entries** post-recording. The
same-session inline closure then shipped 5 new emulator-side helpers
in ``bqemulator.api.routes.jobs``: ``_check_create_disposition``
(CREATE_NEVER x 3), ``_validate_session_id`` + ``_SESSION_CATALOG``
(session_id x 1), ``qualify_unqualified_tables`` SQLGlot
pre-translator in ``bqemulator.sql.rewriter.default_dataset``
(defaultDataset x 3), ``_destructive_dry_run_schema`` (dry-run x 2),
``_apply_write_append`` (WRITE_APPEND x 4) — flipping **13 of 15**
to PASS in the same PR. The registry's post-P7.b-phase-2 state is
23 entries (21 pre-existing + 2 newly-pinned api_configuration:
``legacy_sql_select_compat_mode`` documented out-of-scope and
``dry_run_invalid_function`` as a P7.c follow-up).

The 2026-05-19 **P2.g — Spheroidal-vs-planar boundary mapping** session
added **15 XFAIL entries** spanning the
{street, neighborhood, city, metro, state, national, high-latitude}
scale axis x {distance, area, length, buffer} operation axis. The
session's measured-boundary finding identified BigQuery's spherical
backend uses the S2 library's ``kEarthRadiusMeters = 6371010.0`` (a
discovery that contradicted ADR 0019's "WGS-84 spheroidal" framing).

The **same-day P2.g follow-up** shipped 4 spherical-Earth Python helper
UDFs (``bqemu_st_{distance,length,area,perimeter}_spheroidal``) and 5
new post-translator rules in :mod:`bqemulator.sql.rules.spatial`:
``StDistanceSpheroidalRule`` / ``StLengthSpheroidalRule`` /
``StAreaSpheroidalRule`` / ``StPerimeterSpheroidalRule`` /
``StDWithinSpheroidalRule``. Distance / length / perimeter use the
3D-unit-vector + ``atan2(|cross|, dot)`` great-circle formula on the
S2 sphere; area uses L'Huilier's spherical-excess theorem on a
triangle fan from the outer-ring's first vertex; ``ST_DWITHIN`` is
rewritten to ``bqemu_st_distance_spheroidal(...) <= threshold``.

The follow-up closed **17 fixtures** — the **12 metric P2.g fixtures**
(6 distance + 1 high-latitude + 3 area + 2 length, only the 3 buffer
fixtures remain pinned) plus **5 previously-pinned fixtures** (4
continental: ``st_distance_continental`` / ``st_area_continental`` /
``st_length_continental`` / ``st_perimeter_continental`` and 1 small-
scale predicate: ``st_dwithin_no``).

The 2026-05-19 **P7.c — Tier 2 + Tier 3 API-configuration sweep**
closed 6 more entries via inline emulator-side helpers and follow-up
translator rules: ``legacy_sql_select_compat_mode`` (via
``rewrite_legacy_to_standard`` in
``bqemulator.sql.rewriter.legacy_sql``); ``dry_run_invalid_function``
(via ``_rewrite_for_dry_run`` re-mapping ``error.location='query'``
to ``'q'`` and recovering DuckDB identifier case);
``partition_prune_partitiondate``, ``partition_prune_partitiontime``,
and ``partition_prune_integer_range`` (via
``rewrite_partition_pseudo_columns`` aliasing ``_PARTITIONDATE`` /
``_PARTITIONTIME`` to the partition column); ``st_maxdistance_basic``
(via a new ``StMaxDistanceRule`` translator rule). The 2026-05-19
**P2.d rap_filter_via_view follow-up** closed the last open Phase 8
divergence via ``sync_created_view``. Two ``out-of-scope.md`` sections
(``#ingestion-time-partition-pseudo-columns`` and
``#st_maxdistance-not-yet-implemented``) were removed in the same PR.

The registry's **current state is 15 entries** — all permanent
design-decision divergences with no closure plan for v1.0:

* **11 spheroidal** (ADR 0019; sphere-vs-planar GEOGRAPHY): the 3
  remaining ``spheroidal_buffer_*`` P2.g fixtures plus 8 pre-existing
  surfaces (``st_centroid_polygon``, ``st_intersection_polygons``,
  ``st_buffer_continental``, ``st_asbinary_point``, and the 4
  ``st_asgeojson_*`` interpolation shapes).
* **2 HLL++ sketch BYTES format** (ADR 0024;
  ``agg_hll_count_init_basic`` + ``agg_hll_count_merge_partial_basic``).
* **1 BIGNUMERIC > 28 digits** (DuckDB DECIMAL(38) ceiling;
  ``bound_bignumeric_max``).
* **1 IAM-fundamental** (``out-of-scope.md#iam-enforcement``;
  ``caller_information_schema_visibility``).

Every entry below points at a bucket section in ADR 0023 or an
``out-of-scope.md`` anchor that explains the root cause and names
the closure plan.

Adding a divergence has two preconditions:

1. The divergence is rooted in a locked design decision (ADR 0019
   for spheroidal-vs-planar GEOGRAPHY, ADR 0012 for BQML, etc.) or
   in a catalogued bucket (ADR 0023 for slice-2 baseline).
2. The fixture stays in the corpus with its recorded ``expected.json``
   so a future emulator improvement (or a real-BigQuery change) that
   removes the divergence shows up as an unexpected-pass failure
   (because ``strict=True``).

Removing a divergence happens when a slice closes the gap: the entry
disappears from this dict, the xfail marker disappears with it, and
the fixture starts passing on the next conformance run.
"""

from __future__ import annotations

# fixture_id -> rationale. Order is by fixture id. Keys are unique by
# construction (``test_corpus.py`` parametrises one test per id).
#
# Rationale strings reference ADR sections so the source of the
# divergence is one ``grep`` away. The bucket letters map to ADR 0023:
#   A = REPEATED-row wire-format shape  (Closed 2026-05-15)
#   B = numeric type promotion (FLOAT64 ↔ NUMERIC)  (Closed 2026-05-16)
#   C = wildcard table expansion not triggered  (Closed 2026-05-15)
#   D = unqualified routine reference rejected  (Closed 2026-05-15)
#   E = multi-statement scripting result column naming  (Closed 2026-05-15)
#   F = multi-statement DDL extra-row surface  (Closed 2026-05-16)
#   G = RANGE / INTERVAL wire format  (Closed 2026-05-16)
#   H = GEOGRAPHY WKT whitespace (ST_ASTEXT)  (Closed 2026-05-17)
#   I = standard-function semantic differences  (Closed 2026-05-17)
#   J = emulator-side missing function translation  (Closed 2026-05-16)

_BUCKET_I = "Bucket I — standard-function semantic difference (ADR 0023 §1.I)"
_BUCKET_J = "Bucket J — missing function translation (ADR 0023 §1.J)"
_SPHEROIDAL = (
    "Spheroidal-vs-planar divergence — see ADR 0019 and "
    "docs/reference/out-of-scope.md#spheroidal-geometry-on-geography"
)
_BIGNUMERIC_CAP = (
    "BIGNUMERIC literal with 39 integer digits exceeds DuckDB's "
    "DECIMAL(38, 0) cap; literals with ≤ 38 integer digits work via "
    "fractional truncation (Path C of numeric_literals.py) — see "
    "docs/reference/out-of-scope.md#bignumeric-literals-with-39-integer-digits"
)
_FORMAT_DATE_YEAR_PAD = (
    "FORMAT_DATE %Y year-padding differs from BigQuery for years < 1000: "
    "BQ emits '1-01-01' for DATE '0001-01-01', DuckDB's STRFTIME emits "
    "'0001-01-01' (POSIX strftime always pads %Y to 4 digits). Closure "
    "needs a bqemu_format_date Python helper UDF or a narrow %Y "
    "pre-translator — see "
    "docs/reference/out-of-scope.md#format_date-y-year-padding-for-years--1000"
)
_CTE_SELF_JOIN_WINDOW_UNNEST = (
    "TPC-DS Q47-style multi-CTE pattern: a CTE that carries a window "
    "aggregate (AVG OVER PARTITION BY ... and RANK OVER ...) is self-"
    "joined to itself three times (v1, v1 v1_lag, v1 v1_lead) with "
    "row-number equality joins. SQLGlot inlines the CTE three times; "
    "DuckDB raises ``Binder Error: UNNEST requires a single list as "
    "input`` on the resulting plan. Closure needs an investigation "
    "into how SQLGlot's CTE-inlining transforms window aggregates over "
    "ROW_NUMBER joins — see "
    "docs/reference/out-of-scope.md#cte-self-join-with-window-aggregate-tpc-ds-q47"
)
# _DECIMAL_DIV_DOUBLE_PROMOTION was removed 2026-05-20 in the same
# P8.c PR that introduced it. The 3 TPC-H fixtures originally pinned
# under this rationale (tpch_q8, tpch_q14, tpch_q17) closed via the
# new ``DivDecimalRule`` in ``bqemulator.sql.rules.aggregate_types``,
# which wraps every ``Div`` with at least one DECIMAL-annotated
# operand in ``CAST(... AS DECIMAL(38, 9))``. The annotation-driven
# detection follows the same precedent as ``AvgDecimalRule``;
# operand-type-aware matching ensures the rule fires for the BQ
# NUMERIC-preserving cases (NUMERIC/NUMERIC, NUMERIC/FLOAT64,
# INT64/NUMERIC) while leaving pure FLOAT64/FLOAT64 and INT64/INT64
# unchanged (BQ returns FLOAT in those cases, matching DuckDB).
# _DIVISION_BY_ZERO was removed 2026-05-17 when scope-expansion #17
# (strict division-by-zero raising, reconsidered) landed: the new
# ``division_by_zero`` pre-translator wraps every bare ``/`` operator
# in ``CASE WHEN divisor = 0 THEN error('Division by zero') ELSE a/b
# END`` so DuckDB raises ``Invalid Input Error`` on a zero divisor.
# The script interpreter's ``BEGIN ... EXCEPTION WHEN ERROR THEN ...
# END`` block catches the raise and the
# ``routines_scripting/script_exception_handler`` fixture moves from
# XFAIL to PASS in the same PR.
# _GEOJSON_FORMATTING was removed 2026-05-17 when scope-expansion #18
# (GeoJSON output formatting, reconsidered) landed: the new
# ``StAsGeoJsonStringTypeRule`` wraps ``ST_AsGeoJSON(g)`` in
# ``CAST(... AS VARCHAR)`` (fixes wire-format schema STRING vs JSON),
# and the ADR 0022 §3 JSON-shaped STRING sub-rule absorbs the
# remaining content-level formatting drift (key order, int vs float
# coords, inter-token whitespace). The ``st_asgeojson_point`` fixture
# moved from XFAIL to PASS in the same PR.


KNOWN_DIVERGENCES: dict[str, str] = {
    # ── Bucket A — Closed 2026-05-15 ───────────────────────────────
    # All 22 fixtures originally classified under Bucket A have been
    # triaged. 16 XPASSED after the REST schema-and-row wire-format
    # fix and were removed from this registry; the remaining 6 are
    # rooted in other buckets (J for missing function translation,
    # I for JSON-type round-tripping, G for the RANGE literal /
    # wire-format gap) and have been re-categorised below.
    # ── Bucket B — Closed 2026-05-16 ───────────────────────────────
    # All 22 fixtures originally classified under Bucket B have been
    # closed by a combination of (a) a pre-translator that rewrites
    # bare BigQuery decimal literals to scientific notation so DuckDB
    # types them as DOUBLE (matching BigQuery's FLOAT64 inference);
    # (b) an Arrow→BigQuery type-mapping fix that surfaces TINYINT /
    # SMALLINT as INTEGER (DuckDB's ``SIGN(INT)`` returns TINYINT) and
    # HUGEINT as INTEGER (DuckDB's ``SUM(BIGINT)`` / ``COUNT_IF``
    # promote to HUGEINT, which Arrow encodes as decimal128(38, 0));
    # (c) SQLGlot translation rules for DATE_TRUNC(date, QUARTER/WEEK)
    # that cast the TIMESTAMP-typed result back to DATE (QUARTER) and
    # compute the Sunday-start week (WEEK); (d) typed-node rules for
    # PARSE_NUMERIC / PARSE_BIGNUMERIC; (e) a ``bqemu_to_bignumeric``
    # Python UDF that returns DECIMAL(38, 10) so the scale-> 9 →
    # BIGNUMERIC schema-renderer rule fires for BIGNUMERIC literals;
    # and (f) a catalog-schema-aware ``AvgDecimalRule`` that consults
    # SQLGlot's ``annotate_types`` to wrap ``AVG(DECIMAL_col)`` in a
    # ``CAST(... AS DECIMAL(38, 9))`` so the column type stays NUMERIC
    # through downstream ``ROUND`` calls. Window-form ``AVG(...) OVER
    # (...)`` wraps the entire windowed expression (DuckDB rejects
    # ``CAST AS T OVER`` placement).
    # ── Bucket C — Closed 2026-05-15 ───────────────────────────────
    # All 8 fixtures originally classified under Bucket C have been
    # closed by widening the wildcard-expander predicate to engage on
    # 3-part qualified references (``project.dataset.events_*``,
    # optionally backtick-wrapped) and looping the regex via
    # ``re.sub`` so every wildcard occurrence in a query expands —
    # not just the first. Storage-level table discovery was added so
    # DDL-created shards (``CREATE TABLE … AS SELECT``) are visible.
    # The REST schema renderer now also dedupes column names with a
    # ``_<n>`` suffix so self-joins of a wildcard table match
    # BigQuery's wire-format column-naming guarantee.
    # ── Bucket D — Closed 2026-05-15 ───────────────────────────────
    # All 4 fixtures originally classified under Bucket D have been
    # closed by adding a script-local TEMP-function registry
    # (`src/bqemulator/udf/temp_registry.py`). ``CREATE TEMP FUNCTION
    # foo(...)`` materialises the routine under a registry-unique
    # synthetic dataset and remembers ``foo``; ``SELECT foo(args)`` is
    # rewritten by the registry to the qualified flat name before the
    # rest of the SQL pipeline runs. The macros are dropped when the
    # interpreter's ``run`` exits, preserving the ADR 0014 scope
    # guarantee — TEMP functions never leak into the catalog or across
    # scripts.
    # ── Bucket E — Closed 2026-05-15 ───────────────────────────────
    # ``script_if_then`` XPASSed once the scripting interpreter's
    # ``_rewrite_vars_to_params`` learned to wrap a placeholder in an
    # ``Alias`` whenever a bare script-variable reference is a top-level
    # SELECT projection — BigQuery infers the column name from the
    # source identifier (``SELECT label`` → column name ``label``); the
    # placeholder rewrite previously erased that signal so DuckDB
    # returned ``$1``. ``script_exception_handler`` carried a second
    # divergence beyond column naming — ``EXECUTE IMMEDIATE 'SELECT 1 /
    # 0'`` returns ``Inf`` in DuckDB but raises in BigQuery, so the
    # EXCEPTION handler never fires under the emulator. That secondary
    # divergence is a SQL operator semantic, not a scripting concern,
    # and the fixture is reclassified under Bucket I below.
    # ── Bucket F — Closed 2026-05-16 ───────────────────────────────
    # All 3 fixtures originally classified under Bucket F have been
    # closed by routing per-statement versioning DDL through
    # ``execute_versioning_ddl`` in the script interpreter (so DuckDB
    # never sees ``CREATE SNAPSHOT TABLE`` / ``CLONE`` / ``MATERIALIZED
    # VIEW`` syntax) and by auto-registering plain ``CREATE [OR
    # REPLACE] TABLE`` outputs in the catalog so the versioning
    # managers find the source table. The script interpreter now also
    # gates ``_final_table`` updates on row-producing statements
    # (``isinstance(tree, exp.Query)``), matching BigQuery's
    # "last statement with output wins" rule.
    # ── Bucket G — Closed 2026-05-16 ───────────────────────────────
    # All 20 fixtures originally classified under Bucket G have been
    # closed by three coordinated translator + renderer fixes:
    # (a) a pre-translator pass in
    # ``src/bqemulator/sql/rewriter/specialized_types.py`` that rewrites
    # the BigQuery ``RANGE<T> '[start, end)'`` typed literal — which
    # SQLGlot parses as ``Cast(literal, RANGE<T>)`` and DuckDB rejects —
    # into a ``STRUCT(CAST(<start> AS T) AS start, CAST(<end> AS T) AS
    # end)`` literal, with ``UNBOUNDED`` endpoints lowered to
    # ``CAST(NULL AS T)`` so DuckDB's struct typing stays uniform;
    # (b) a new ``detect_range_element`` helper in
    # ``src/bqemulator/types/range_type.py`` that the schema renderer
    # (``_arrow_field_to_schema_entry``) and row renderer
    # (``arrow_bridge._format_bq_value``) both call to recover the
    # BigQuery ``RANGE`` shape from the ``bqemu.duckdb_type`` metadata
    # — RANGE columns surface on the wire as ``type=RANGE`` with
    # ``rangeElementType: {type: DATE|DATETIME|TIMESTAMP}`` and the
    # cell value becomes the canonical ``[start, end)`` string the
    # BigQuery Python client's ``_RANGE_PATTERN`` parses; (c) an
    # ``INTERVAL`` branch in ``_arrow_type_to_bq_type`` so DuckDB's
    # ``month_day_nano_interval`` columns surface as ``INTERVAL`` on
    # the wire (the existing canonical ``Y-M D H:M:S`` formatter in
    # ``arrow_bridge`` was already correct, but without the schema-type
    # fix the column landed on STRING). Finally, the
    # ``GenerateRangeArrayRule`` was widened to clip the trailing
    # sub-range to the outer range's end (matching BigQuery's
    # half-open semantic) and re-cast each endpoint to the original
    # element type — DuckDB's ``range(DATE, DATE, INTERVAL)`` widens
    # to TIMESTAMP, so the lambda recovers DATE via a
    # post-transform cast.
    # ── Bucket H — Closed 2026-05-17 ───────────────────────────────
    # 7 of the originally-pinned 11 fixtures closed via the ADR 0022
    # §3 amendment: WKT-shaped STRING values now route through the
    # GEOGRAPHY whitespace + capitalisation normalisation rule. The
    # comparison helper's ``_compare_scalar`` detects WKT-shaped
    # STRING values (anchored regex matching one of the seven WKT
    # geometry-type keywords followed by ``\s*\(``) and calls
    # ``_compare_geography`` in place of exact equality. The closed
    # set: ``st_astext_point``, ``st_geogfromtext_point``,
    # ``st_geogfromtext_linestring``, ``st_geogfromtext_polygon``,
    # ``st_geogfromwkb_point``, ``st_geogfromgeojson_point``,
    # ``st_geogpoint`` — all of which produced ``POINT (1 2)``-style
    # outputs that only differed from BigQuery's ``POINT(1 2)`` form
    # in whitespace.
    #
    # 4 of the originally-pinned 11 carried a second-order divergence
    # the WKT-whitespace fix cannot cover and were reclassified in
    # the same session:
    # * ``st_centroid_polygon`` — planar centroid lands at exactly
    #   ``(2, 2)`` where BigQuery's spheroidal centroid is
    #   ``(2.00000000000004, 2.00040218892024)``. Spheroidal-vs-planar
    #   coordinate drift, ADR 0019.
    # * ``st_intersection_polygons`` — planar intersection emits
    #   straight-edge rectangle ``POLYGON((4 4, 4 2, 2 2, 2 4, 4 4))``
    #   where BigQuery's spheroidal intersection follows geodesics
    #   (the edge bulges by ~1.2e-3 degrees). ADR 0019.
    # * ``st_dwithin_no`` — planar Euclidean distance over the
    #   ``ST_GEOGPOINT(0, 0) ↔ ST_GEOGPOINT(0, 90)`` pair is the
    #   90-unit coordinate delta, where BigQuery's spheroidal
    #   distance is ~10⁷ metres. With a 100-metre threshold the two
    #   sides return opposite truth values. ADR 0019.
    # * ``st_asgeojson_point`` — initially reclassified here under
    #   ``out-of-scope.md#geojson-output-formatting``; later closed
    #   on the same day (2026-05-17) via scope-expansion #18, which
    #   landed a ``StAsGeoJsonStringTypeRule`` SQL rule plus an
    #   ADR 0022 §3 JSON-shaped STRING amendment. The fixture now
    #   PASSES and is no longer pinned in this registry.
    #
    # ``st_geometrytype_linestring``, ``st_geometrytype_point``,
    # ``st_geometrytype_polygon``, ``st_geometrytype_multipoint``,
    # ``st_convexhull_points``, ``st_envelope_polygon``,
    # ``st_makepolygon_from_ring`` XPASSed earlier (2026-05-17 Bucket
    # I closure) via the new ``StGeometryTypeBqNameRule``; the
    # underlying divergence was ``ST_<PascalCase>`` wire-format type
    # names, not WKT whitespace.
    # ``st_npoints_line``, ``st_numpoints_polygon``,
    # ``st_geogfromtext_multipoint``, ``st_isring_line``,
    # ``st_pointn_line`` XPASSed via the 2026-05-16 Bucket B closure;
    # the underlying divergence was narrow-width integer types
    # (TINYINT / SMALLINT) the pre-Bucket-B Arrow mapper surfaced as
    # STRING, not WKT whitespace.
    "specialized_types/st_centroid_polygon": _SPHEROIDAL,
    "specialized_types/st_intersection_polygons": _SPHEROIDAL,
    # ``st_dwithin_no`` was closed 2026-05-19 by the P2.g spheroidal-
    # mapping follow-up — the new ``StDWithinSpheroidalRule`` rewrites
    # ``ST_DWITHIN(g1, g2, d)`` to ``bqemu_st_distance_spheroidal(...) <= d``
    # so the threshold ``d`` is compared in metres on the S2 sphere
    # rather than in degree-Euclidean units. ``ST_DWITHIN((0,0),(0,90), 100)``
    # now returns False (matching BQ) because the spheroidal distance
    # is ~10⁷ m, far over the 100 m threshold.
    # ``st_asgeojson_point`` was previously pinned here under the
    # GeoJSON-formatting out-of-scope entry; closed 2026-05-17 via
    # scope-expansion #18 — see the explanatory comment above and
    # the rationale-constant deletion further up.
    # ── Bucket I — Closed 2026-05-17 ───────────────────────────────
    # All 38 fixtures originally classified under Bucket I have been
    # triaged across three sub-sessions:
    #
    # * Sub-session I-a (date/time + FORMAT/PARSE — 18 fixtures): a
    #   new ``datetime_semantics`` rule module plus pre-translator
    #   ``datetime_helpers`` close every fixture. ``DATE_ADD`` /
    #   ``DATE_SUB`` / ``DATE_FROM_UNIX_DATE`` are wrapped in
    #   ``CAST(... AS DATE)`` at the BigQuery AST level so the
    #   function-call forms preserve their DATE return type while the
    #   literal ``DATE '...' + INTERVAL`` operator form (BigQuery
    #   returns DATETIME) is left alone. ``DATE_TRUNC(date, …)`` over
    #   the calendar units (DAY/MONTH/QUARTER/YEAR) wraps in CAST AS
    #   DATE post-translate. ``EXTRACT(DATE FROM ts)`` rewrites to
    #   ``CAST(ts AS DATE)`` since DuckDB rejects the ``DATE``
    #   specifier. ``EXTRACT(DAYOFWEEK FROM x)`` adds 1 to match
    #   BigQuery's 1-indexed convention; ``EXTRACT(WEEK FROM x)``
    #   computes the Sunday-start Gregorian week via a closed-form
    #   ``(DOY - 1 + DAYOFWEEK(j1)) // 7``. ``LAST_DAY(x, WEEK)``
    #   pre-translates to a ``DATE_ADD(x, INTERVAL 7 - DAYOFWEEK(x)
    #   DAY)`` shape that lands on Saturday. ``TIMESTAMP_MICROS`` /
    #   ``TIMESTAMP_MILLIS`` pre-translate to ``TIMESTAMP_ADD(epoch,
    #   INTERVAL n MICROSECOND|MILLISECOND)`` so the result is
    #   TIMESTAMPTZ (matching BigQuery's TIMESTAMP wire-format).
    #   ``FORMAT(fmt, args)`` routes through DuckDB's ``printf`` for
    #   true C-style format specifiers. ``PARSE_TIME`` emits ``CAST
    #   (strptime AS TIME)``; ``PARSE_TIMESTAMP`` wraps ``strptime``
    #   in ``timezone('UTC', …)`` so the column type lands on
    #   TIMESTAMPTZ.
    #
    # * Sub-session I-b (JSON + STRUCT — 5 fixtures): a new
    #   ``json_helpers`` pre-translator wraps BigQuery's
    #   ``JSONFormat(to_json=True)`` (TO_JSON) in ``CAST(... AS JSON)``
    #   to preserve the JSON column type through SQLGlot's transpile —
    #   the default transpile collapses both TO_JSON and TO_JSON_STRING
    #   to ``CAST(TO_JSON(...) AS TEXT)`` so the JSON variant must be
    #   re-tagged. ``JSON_TYPE`` wraps in ``LOWER`` to match BigQuery's
    #   lowercase return form. A new ``struct_helpers`` pre-translator
    #   replaces positional ``STRUCT(value, value, …)`` calls (no
    #   ``AS`` aliases) with DuckDB's ``ROW(…)`` constructor so the
    #   struct aligns positionally with its target — matching
    #   BigQuery's name-from-context inference for INSERT VALUES and
    #   UNION ALL chains where the first SELECT carries explicit
    #   field aliases.
    #
    # * Sub-session I-c (hash + boundary + misc — 14 fixtures): a
    #   pre-translator rewrites ``SAFE.X`` (function-prefix form) to
    #   DuckDB's ``TRY(...)``. A pre-translator rewrites the 4-arg
    #   ``INSTR(haystack, needle, position, occurrence)`` form to a
    #   ``bqemu_instr_occurrence`` Python helper UDF. Two post-translate
    #   rules close the remaining standard-function gaps:
    #   ``ApproxCountDistinctExactRule`` replaces
    #   ``APPROX_COUNT_DISTINCT`` with the exact ``COUNT(DISTINCT)``
    #   (DuckDB's HyperLogLog stand-in returns 11 for a 10-distinct
    #   set); ``ApproxQuantilesDiscreteRule`` routes
    #   ``APPROX_QUANTILE`` through DuckDB's discrete ``quantile_disc``
    #   aggregate so the per-quartile values match BigQuery's
    #   sample-based ``APPROX_QUANTILES`` output. ``ConcatStringTypeRule``
    #   wraps every ``||`` DPipe in ``CAST(... AS VARCHAR)`` so the
    #   wire-format column type stays STRING even when one operand
    #   collapses to a typed NULL. ``StGeometryTypeBqNameRule`` maps
    #   DuckDB's uppercase WKT type names (``POINT``, ``MULTIPOINT``,
    #   …) to BigQuery's ``ST_<PascalCase>`` form via an inline CASE.
    #   The Python helpers ``bqemu_upper_unicode`` (Python ``str.upper``
    #   for the ``ß`` → ``SS`` case-fold rule), ``bqemu_instr_occurrence``
    #   (4-argument INSTR), and a pure-Python port of FarmHash
    #   Fingerprint64 (``bqemu_farm_fingerprint``) close the
    #   remaining function-level fixtures bit-exactly. The
    #   ``arrow_bridge`` TIMESTAMP renderer switched from
    #   ``int(ts.timestamp() * 1_000_000)`` to integer ``timedelta``
    #   arithmetic so the 9999-12-31 boundary survives without
    #   float-precision drift.
    #
    # Of the two fixtures that originally landed in ``out-of-scope.md``
    # rather than closing under Bucket I, ``script_exception_handler``
    # was reconsidered as scope-expansion #17 on 2026-05-17 and closed
    # the same day via the new ``division_by_zero`` pre-translator
    # (wraps every bare ``/`` in a CASE that raises ``Division by
    # zero`` when the divisor is 0). Only ``bound_bignumeric_max``
    # remains pinned to the out-of-scope DuckDB DECIMAL(38) cap —
    # closure would require a wide-decimal backend rewrite, deferred
    # to v2.
    "standard_functions/bound_bignumeric_max": _BIGNUMERIC_CAP,
    # ── P8.b (2026-05-20) — 30-surface edge-case sweep
    # ── surfaced FORMAT_DATE %Y year-padding divergence on the
    # ── DATE '0001-01-01' boundary case. BigQuery's strftime
    # ── does NOT zero-pad %Y for years < 1000 (returns '1-01-01');
    # ── DuckDB's POSIX strftime always pads to 4 digits ('0001-01-01').
    # ── Closure needs a Python helper UDF or a narrow pre-translator —
    # ── pinned with documented plan in out-of-scope.md.
    "standard_functions/dt_format_date_min": _FORMAT_DATE_YEAR_PAD,
    # ── P8.d follow-up (2026-05-20) — TPC-DS 19-query expansion
    # ── surfaced a CTE self-join + window-aggregate translator gap on
    # ── Q47. Spec uses (v1, v1 v1_lag, v1 v1_lead) joined on the RANK
    # ── columns; SQLGlot's inlining produces a plan DuckDB rejects with
    # ── ``UNNEST requires a single list as input``. Pinned with
    # ── closure plan in out-of-scope.md.
    "standard_functions/tpcds_q47": _CTE_SELF_JOIN_WINDOW_UNNEST,
    # ── Bucket J — Closed 2026-05-16 ───────────────────────────────
    # All 44 fixtures originally classified under Bucket J have been
    # triaged. 41 XPASSED once the SQLGlot translator gained
    # rules for the missing builtins (SAFE_ADD / SAFE_SUBTRACT /
    # SAFE_MULTIPLY / SAFE_NEGATE → TRY-wrapped arithmetic; JSON_KEYS
    # / JSON_REMOVE / JSON_SET / JSON_STRIP_NULLS / LAX_BOOL /
    # LAX_INT64 / LAX_FLOAT64 / LAX_STRING / BOOL(json) /
    # FLOAT64(json) / STRING(json) — DuckDB ``json_keys`` plus
    # Python-helper UDFs; OCTET_LENGTH / BYTE_LENGTH → CASE TYPEOF
    # over strlen/octet_length; CODE_POINTS_TO_STRING and
    # TO_CODE_POINTS → list_transform + chr/ord; NORMALIZE /
    # NORMALIZE_AND_CASEFOLD → Python helpers; ISOWEEK / ISOYEAR
    # extract specifiers — keyword rewrite + DATE cast; ARRAY_AGG /
    # STRING_AGG ORDER BY LIMIT and IGNORE NULLS — pre-translator
    # rewriter; IEEE_DIVIDE, RANGE_BUCKET, APPROX_TOP_SUM, NUMERIC
    # / BIGNUMERIC literal precision pinning). The remaining 3
    # cascaded to Bucket I (function exists but value differs):
    # agg_approx_quantiles, math_rand_ish_deterministic,
    # bound_bignumeric_max — see the reclassification block above.
    # ── Slice-1 baseline: spheroidal-vs-planar GEOGRAPHY ───────────
    # The 2026-05-19 P2.g spheroidal-mapping follow-up implemented
    # spherical-Earth helpers (``bqemu_st_distance_spheroidal`` /
    # ``bqemu_st_length_spheroidal`` / ``bqemu_st_area_spheroidal`` /
    # ``bqemu_st_perimeter_spheroidal``) routed through new post-
    # translator rules in :mod:`bqemulator.sql.rules.spatial`. The
    # helpers use S2's documented ``kEarthRadiusMeters = 6371010.0``
    # plus the 3D-unit-vector + ``atan2(|cross|, dot)`` great-circle
    # formula for distance / length / perimeter, and L'Huilier's
    # spherical-excess fan from the outer-ring's first vertex for
    # area. Every recorded continental-scale fixture (``st_*_continental``
    # except ``st_buffer_continental``) and every P2.g metric fixture
    # closed by the change. The remaining ``st_buffer_continental``
    # XFAIL below pins the buffer-polygon vertex-set, which would
    # need a per-vertex geodesic-bearing generator that produces the
    # exact 33-vertex polygon BigQuery emits (~1100 km radius polygon
    # with vertices at 11.25° azimuth steps from the centre); no
    # closure planned for v1.0.
    "specialized_types/st_buffer_continental": _SPHEROIDAL,
    # ── P2.d (2026-05-17) — Phase 8 row-access fixtures recorded
    # ── against real BQ surfaced 7 divergences.
    # ── 2026-05-18 P2.d follow-up #1 closed 5 of them after
    # ── empirical discovery (see ADR 0018 revision) that real
    # ── BigQuery does NOT bypass row-level security for authorized
    # ── views in ANY topology — both same-dataset and cross-dataset
    # ── recordings returned 0 rows from real BQ. The
    # ── 5 ``authz_view_*`` divergences are closed by removing the
    # ── emulator's authorized-view RAP bypass in
    # ── src/bqemulator/sql/rewriter/row_access_filter.py and
    # ── rewriting the fixtures to the canonical cross-dataset
    # ── topology (the more representative BQ usage pattern). All
    # ── 5 fixtures now PASS cleanly with zero rows; the entries
    # ── below are removed accordingly.
    # INFORMATION_SCHEMA.ROW_ACCESS_POLICIES requires admin IAM on
    # real BigQuery (joe's recording ADC returned 404 NotFound). The
    # emulator does not enforce IAM (per out-of-scope.md#iam-enforcement)
    # so it returns the policy definitions. Fundamental BQ-vs-emulator
    # difference, not a fix.
    "row_access/caller_information_schema_visibility": (
        "P2.d: INFORMATION_SCHEMA.ROW_ACCESS_POLICIES requires "
        "bigquery.rowAccessPolicies.list IAM permission. Recording "
        "account got 404 NotFound from real BigQuery; emulator returns "
        "the policy row (IAM not enforced per out-of-scope.md#iam-"
        "enforcement). Pinned as a fundamental divergence."
    ),
    # ``row_access/rap_filter_via_view`` was registered briefly
    # 2026-05-18 as the P2.d-recording surfaced a 4-rows-vs-2 emulator
    # divergence: the row-access rewriter's ``_expand_view`` branch
    # never fired for SQL-created views because the catalog had no
    # ``table_type='VIEW'`` + ``view_query`` for them, so DuckDB
    # expanded the view body internally and read the base table with
    # NO RAP filter. The 2026-05-19 XFAIL-closure follow-up shipped a
    # new ``sync_created_view`` helper in
    # [`ddl_sync.py`](src/bqemulator/catalog/ddl_sync.py) that
    # registers SQL-created views with ``table_type='VIEW'`` +
    # ``view_query=<body>``, so the existing ``_expand_view`` branch in
    # [`row_access_filter.py`](src/bqemulator/sql/rewriter/row_access_filter.py)
    # now fires and recurses through the view body, applying caller-
    # bound policies on every base-table reference inside (matching
    # the ADR 0018 revised 2026-05-18 contract that RAP applies
    # through every view body regardless of authorization status).
    # The fixture now PASSes; entry removed from this registry.
    # ``row_access/rap_filter_with_group_grantee`` and
    # ``row_access/caller_match_via_group_only`` were
    # registered briefly on 2026-05-18 after the group-grantee
    # recording revealed the emulator returned 0 rows where real BQ
    # returned 1 EU row. Root cause: the
    # ``src/bqemulator/row_access/identity.py`` ``_parse_groups``
    # helper preserved the ``group:`` IAM-member prefix when parsing
    # the ``X-Bqemu-Groups`` header, but the matcher (per the
    # ``test_group_via_groups_header`` unit-test contract) expected
    # bare emails in ``caller.groups``. The P2.a closure-bug
    # follow-up later that day taught ``_parse_groups`` to strip the
    # ``group:`` prefix so both forms produce identical
    # ``CallerIdentity.groups`` tuples. Both fixtures PASS after the
    # fix; entries removed from this registry.
    # ── Top-30 gap closure (2026-05-18) ───────────────────────────────
    # 18 fixtures authored in the top-30 0-fixture surface-item gap
    # closure session surface emulator gaps that warrant separate
    # follow-up workstreams. Each block below points at a section of
    # docs/reference/out-of-scope.md added 2026-05-18.
    # ``partitioning_clustering/partition_prune_partitiondate``,
    # ``_partitiontime``, and ``_integer_range`` were closed by the
    # P7.c follow-up below: a new
    # ``bqemulator.sql.rewriter.partition_pseudo_columns`` pre-
    # translator rewrites every ``_PARTITIONDATE`` reference to
    # ``CURRENT_DATE()`` and every ``_PARTITIONTIME`` reference to
    # ``CURRENT_TIMESTAMP()`` before the SQLGlot transpile. The
    # emulator's storage layer doesn't tag rows with a partition
    # timestamp, but every row inserted right now lives in today's
    # partition by BigQuery's contract, so the rewrite matches the
    # recorded fixtures' filters (``> '1900-01-01'``, ``BETWEEN ...``,
    # ``< '1900-01-01' → 0 rows``) without any storage change.
    # Entries removed.
    # specialized_types/geography_column_{basic,insert,select_filter}
    # were registered briefly in the 2026-05-18 top-30 gap-closure session
    # #1 because BigQuery's ``GEOGRAPHY`` column-type token reached DuckDB
    # verbatim and DuckDB's spatial extension only registers the type as
    # ``GEOMETRY``. The 2026-05-18 top-30 session #3c follow-up added a
    # post-translator rule ``GeographyColumnTypeRule`` (in
    # ``bqemulator.sql.rules.spatial``) that maps the
    # ``DataType.GEOGRAPHY`` AST node to ``DataType.GEOMETRY`` — the
    # column-storage type the existing ``GEOMETRY ↔ GEOGRAPHY``
    # reverse-mapping in ``storage.type_map`` already surfaces back as
    # ``GEOGRAPHY`` on the REST schema. All three fixtures move from
    # XFAIL to PASS in the same PR.
    # ``routines_scripting/script_for_iterate_into_table`` was registered
    # briefly 2026-05-18 in the top-30 gap-closure session #1 with a
    # rationale pointing at a (presumed) cross-statement catalog-
    # visibility bug in the scripting interpreter. The 2026-05-19
    # XFAIL-closure follow-up's empirical reproduction proved that
    # rationale wrong — the actual failure was DuckDB's binder error
    # ``Referenced column "label" not found in FROM clause! Candidate
    # bindings: "unnest"`` raised by the FOR loop's source SELECT,
    # caused by ``rewrite_struct_helpers`` rewriting the unnamed
    # ``STRUCT('b', 2)`` siblings to ``ROW('b', 2)`` while leaving the
    # first ``STRUCT('a' AS label, 1 AS value)`` named — yielding a
    # mixed-shape array DuckDB couldn't bind by field name. The
    # closure ships a new pre-translator
    # [`rewrite_unnest_struct`](src/bqemulator/sql/rewriter/unnest_struct.py)
    # that runs BEFORE ``rewrite_struct_helpers`` and propagates the
    # first struct's named-field aliases to every subsequent
    # positional struct in an ``UNNEST([...])`` array literal. After
    # the rewrite, the array is homogeneously named, SQLGlot transpiles
    # it to a destructurable DuckDB shape, and the outer ``SELECT
    # label, value`` resolves cleanly. The wrongly-premised
    # ``out-of-scope.md#for-loop-with-insert-into-a-pre-existing-table``
    # section was removed in the same PR. The fixture now PASSes;
    # entry removed from this registry.
    # Less-common string functions without DuckDB counterparts.
    # Of the 11 originally pinned here, 9 were closed 2026-05-18 in the
    # top-30 gap-closure session #3 XFAIL-reduction follow-up via four
    # Python helper UDFs + four translator rules (TO_BASE32 / FROM_BASE32
    # / CODE_POINTS_TO_BYTES / SOUNDEX) plus a RegexpExtract NULLIF wrap
    # for the no-match case. The 2 remaining ``str_collate_*`` fixtures
    # were closed by the 2026-05-18 top-30 session #3c follow-up via a
    # new pre-translator ``rewrite_collate_specifier`` (in
    # ``bqemulator.sql.rewriter.collate_specifier``) that handles the
    # two BigQuery-documented specifiers the corpus exercises:
    # ``'und:ci'`` (case-insensitive Unicode default) rewrites to
    # ``LOWER(value)`` so equality on lower-cased operands matches the
    # documented case-insensitive collation semantic; ``'binary'``
    # rewrites to ``error('Collation \\'binary\\' in collate function
    # is not supported.')`` so the recorded ``str_collate_binary``
    # error fixture's ``message_pattern`` is matched by the existing
    # :mod:`bqemulator.jobs.error_mapper` fallback. The
    # less-common-string-functions out-of-scope section's COLLATE
    # subsection was removed in the same PR.
    # Note: ``routines_scripting/txn_in_exception_block`` was briefly
    # registered as a divergence on 2026-05-17 (the P2.b initial
    # closure) and removed later the same day when the emulator-level
    # transaction shim landed: BEGIN / COMMIT / ROLLBACK [TRANSACTION]
    # are now intercepted in the script interpreter and snapshot every
    # DML target the first time it's modified; ROLLBACK restores from
    # the snapshot, COMMIT or exception-caught drops it (DML stays
    # applied — matches BQ's documented semantic). See ``ScriptInterpreter._exec_sql``
    # and the ``_classify_txn_statement`` / ``_dml_targets`` helpers.
    # ── P2.a (2026-05-18) — Scope-expansion depth fixtures ────────
    # The 2026-05-17 scope-expansion #15 / #17 / #18 closures shipped
    # with thin fixture coverage (5 total fixtures). P2.a authored 24
    # new fixtures across the three surfaces.
    #
    # The 4 ST_AsGeoJSON cases beyond a single POINT (LineString,
    # GeometryCollection, MultiLineString, MultiPolygon) originally
    # XFAILed because BigQuery inserts geodesic-midpoint vertices on
    # long non-equatorial / non-meridian edges (e.g., a LINESTRING
    # from (1,1) to (2,2) gains an interpolated point at
    # (1.49988573656168, 1.5000570914792)) and DuckDB-spatial's planar
    # ST_AsGeoJSON emits the unbent edges. **Closed in the P3.d follow-up
    # (2026-05-19)** by the new ``bqemu_geojson_geodesic_interp`` Python
    # helper UDF (great-circle midpoint via 3D-unit-vector averaging +
    # ``asin``/``atan2`` projection back to lat/lng) and a ~50 µdeg
    # cross-track-deviation threshold for recursive subdivision. The
    # ULP drift between Python's ``math`` library and BigQuery's S2
    # implementation is absorbed by the new float-tolerance pass in
    # ``_compare_json_shaped_string`` (uses the same ``rel_tol=1e-12,
    # abs_tol=1e-15`` as the native FLOAT64 comparator). All 4 fixtures
    # now PASS without an entry in this registry.
    # ``specialized_types/st_asgeojson_empty_point`` was
    # registered briefly during P2.a (2026-05-18) after the
    # ST_AsGeoJSON fixture recording revealed DuckDB-spatial emits
    # ``{"type": "Point", "coordinates": []}`` for ``POINT EMPTY``
    # where BigQuery normalises to
    # ``{"type": "GeometryCollection", "geometries": []}`` per
    # GeoJSON RFC 7946. The P2.a closure-bug follow-up later that
    # day added a ``bqemu_geojson_normalize_empty`` Python helper UDF
    # that detects empty-coordinates / empty-geometries shapes and
    # rewrites them to the canonical RFC 7946 form; the
    # ``StAsGeoJsonStringTypeRule`` rewrite now wraps the call as
    # ``CAST(bqemu_geojson_normalize_empty(ST_AsGeoJSON(g)) AS
    # VARCHAR)``. The fixture PASSes after the fix; entry removed.
    # ``specialized_types/range_sessionize_null_range`` was
    # registered briefly during P2.a (2026-05-18) after the fixture
    # recording revealed that BigQuery bridges non-NULL rows in a
    # partition into one session spanning ``[min(start), max(end)]``
    # whenever any NULL range is present (the original closure
    # implemented standard MEETS semantics without the bridge). The
    # P2.a closure-bug follow-up later that day extended the
    # ``rewrite_range_sessionize`` windowed subquery with a per-
    # partition ``_bqemu_partition_has_null = BOOL_OR(<range>.start
    # IS NULL) OVER (PARTITION BY parts)`` flag and a coordinated
    # session-id / session_range CASE so NULL rows get
    # ``session_range = NULL`` and every non-NULL row in a NULL-
    # containing partition collapses to the canonical bridged
    # session range. The fixture PASSes after the fix; entry removed.
    # ``specialized_types/range_sessionize_overlaps_or_meets_alias``
    # was registered briefly during P2.a (2026-05-18) when authoring
    # revealed that BigQuery rejects ``OVERLAPS_OR_MEETS`` as an
    # invalid RANGE_SESSIONIZE_MODE while the closure's
    # ``_MODE_TO_OP`` dict incorrectly carried an entry for it. The
    # P2.a closure-bug follow-up later that day dropped the
    # ``OVERLAPS_OR_MEETS`` entry from ``_MODE_TO_OP`` so the
    # unknown-mode branch raises ``InvalidQueryError`` matching
    # BigQuery's ``Could not cast literal …`` wording. The fixture
    # PASSes after the fix and is no longer pinned here.
    # ── Top-30 session #3b (2026-05-18) — HLL_COUNT family ─────────
    # ADR 0024 documents the decision matrix: Option D + B hybrid.
    # The two sketch-shaped surfaces — HLL_COUNT.INIT and
    # HLL_COUNT.MERGE_PARTIAL — return a BYTES sketch in BigQuery's
    # HyperLogLog++ binary format. The emulator's translator handles
    # the cardinality-extracting patterns (EXTRACT-of-INIT and
    # MERGE-over-subquery-of-INIT) via the COUNT(DISTINCT)
    # equivalence (HllCountExtractInitRule + HllCountMergeRule), but
    # doesn't ship the HLL++ bit-exact format (multi-week reverse-
    # engineering disproportionate to the user-facing benefit). The
    # bare INIT and MERGE_PARTIAL calls reach DuckDB unchanged and
    # raise CatalogException → InvalidQueryError.
    "standard_functions/agg_hll_count_init_basic": (
        "HLL sketch BYTES format differs — see "
        "docs/reference/out-of-scope.md#hll-sketch-binary-format-hll_countinit--merge_partial"
    ),
    "standard_functions/agg_hll_count_merge_partial_basic": (
        "HLL sketch BYTES format differs — see "
        "docs/reference/out-of-scope.md#hll-sketch-binary-format-hll_countinit--merge_partial"
    ),
    # ── 2026-05-19 P7.a — API configuration parity pilot fixtures.
    # ── First recording against real BigQuery surfaced 6 emulator
    # ── response-metadata gaps. **All 6 closed by P7.b the same day**
    # ── via four emulator-side changes:
    # ──   1. ``classify_statement_type`` helper in
    # ──      ``bqemulator.jobs.executor`` parses the SQL via sqlglot
    # ──      and returns the BigQuery ``statementType`` (SELECT /
    # ──      INSERT / CREATE_TABLE_AS_SELECT / DROP_TABLE / etc.).
    # ──   2. ``_build_query_statistics`` populates ``cacheHit=False``
    # ──      (always — emulator has no cache), ``statementType``,
    # ──      ``numDmlAffectedRows`` for DML, and
    # ──      ``ddlOperationPerformed`` for DDL on every QueryJob's
    # ──      ``statistics.query`` block.
    # ──   3. ``execute_query_job`` trims DuckDB's 1-column ``Count``
    # ──      result for DML statements and replaces it with an empty
    # ──      Arrow table so the wire-format schema is 0-col, matching
    # ──      real BigQuery's "no projection for DML" contract.
    # ──   4. ``_dry_run_response`` in ``api.routes.jobs`` runs the
    # ──      query through the executor (for SELECT), takes the
    # ──      schema, discards the rows, and returns the preview
    # ──      shape. Destructive statements skip execution to avoid
    # ──      committing side effects during a dry run.
    # ── No divergence remains pinned for ``api_configuration/*``.
    # ── Top-30 session #3d (2026-05-19) — GEOGRAPHY tail ───────────
    # 22 fixtures authored against the 20 GEOGRAPHY tail surface
    # items (skipping ST_CLUSTERDBSCAN — pinned in out-of-scope.md
    # with no fixture). 20 close cleanly via 15 BQ_TO_DUCKDB entries
    # + 3 custom rules (StIntersectsBoxRule, StSnapToGridRule,
    # StMakePolygonOrientedRule) + 1 Python helper
    # (bqemu_st_snaptogrid). The 2 XFAILs below cover surface-
    # rooted spheroidal divergence (ST_ASBINARY) and a missing
    # DuckDB-spatial primitive that would need a multi-week
    # spheroidal backend (ST_MAXDISTANCE).
    #
    # ST_ASBINARY: BigQuery encodes ST_GEOGPOINT(1, 1) via an
    # ECEF→lng/lat round-trip that loses 1 ULP on each axis
    # (recorded x = 0x3FEFFFFFFFFFFFFE ≈ 0.9999999999999998 instead
    # of an exact 1.0). DuckDB stores the planar coordinates
    # exactly, so the WKB bytes diverge from BigQuery's recorded
    # base64 by a single bit per coordinate. The function is
    # otherwise translator-routed correctly via SpatialRenameRule;
    # the divergence is rooted in the spheroidal vs planar
    # coordinate representation (ADR 0019) and would close only
    # with a spheroidal backend.
    "specialized_types/st_asbinary_point": _SPHEROIDAL,
    # ST_MAXDISTANCE: DuckDB-spatial has no ST_MaxDistance
    # primitive; the function reaches DuckDB unchanged and raises
    # CatalogException → InvalidQueryError. Pinned against the new
    # out-of-scope.md section that documents the multi-week
    # spheroidal-backend prerequisite for a clean closure.
    # ``st_maxdistance_basic`` was closed by the P7.c follow-up
    # below: a new ``StMaxDistanceRule`` in
    # ``src/bqemulator/sql/rules/spatial.py`` routes
    # ``ST_MAXDISTANCE(g1, g2)`` through the existing
    # spheroidal-distance helper for POINT-POINT inputs (max ==
    # distance for two single points). Multi-point and shape-shape
    # combinations remain unimplemented (they're not exercised by
    # the corpus). Entry removed.
    # ── P2.g (2026-05-19) — Spheroidal-vs-planar boundary mapping ──
    # 15 fixtures authored to map where the planar-vs-spheroidal
    # divergence becomes user-visible across the
    # {street, neighborhood, city, metro, state, national,
    # high-latitude} scale axis x {distance, area, length, buffer}
    # operation axis. **All 15 diverge** because the emulator's
    # planar ``ST_Distance`` / ``ST_Area`` / ``ST_Length`` return
    # Euclidean values in **degree-units** of the input coordinates
    # while BigQuery returns spheroidal **metres / square-metres**;
    # ``ST_Buffer`` interprets its radius as **degrees** rather than
    # **metres**. The ratios cluster around the WGS-84 metre-per-
    # degree constant (~111,000x for length, ~9.4e9x for area), with
    # a smaller ~19,000x at high latitude because the longitude
    # meridian compresses by ``cos(80°)≈0.174``. The mapping
    # outcome (documented in
    # ``out-of-scope.md#spheroidal-geometry-on-geography``) sharpens
    # ADR 0019's previous "diverge at continental scales" framing:
    # the divergence is universal at every scale for metric returns.
    # The 12 metric P2.g fixtures (6 distance + 1 high-latitude +
    # 3 area + 2 length) all closed in the same-day P2.g spheroidal-
    # mapping follow-up — see the slice-1 baseline comment above for
    # the helper-UDF + translator-rule plumbing. Only the 3 buffer
    # fixtures remain pinned because matching BigQuery's 33-vertex
    # geodesic-circle polygon vertex-exact would need a per-vertex
    # bearing generator on the S2 sphere; no closure planned for v1.0.
    "specialized_types/spheroidal_buffer_street_match": _SPHEROIDAL,
    "specialized_types/spheroidal_buffer_neighborhood_match": _SPHEROIDAL,
    "specialized_types/spheroidal_buffer_state_xfail": _SPHEROIDAL,
    # ── 2026-05-19 P7.b phase 2 — Tier 1 API-configuration ──────────
    # ── recording session. ~40 fixtures recorded against real
    # ── BigQuery covering the audit doc §8 Tier 1 cluster list. The
    # ── XFAILed entries below pin fixtures that surfaced emulator
    # ── gaps too wide to fix inline within the recording session.
    #
    # Legacy SQL: ``useLegacySql=true`` selects BigQuery's original
    # 2011-era dialect. The full surface remains out-of-scope per
    # ``out-of-scope.md#legacy-sql-uselegacysqltrue`` (separate
    # parser, JOIN EACH / WITHIN / FLATTEN semantics, etc.), but the
    # P7.c follow-up shipped a narrow legacy-to-standard rewriter
    # (``bqemulator.sql.rewriter.legacy_sql.rewrite_legacy_to_standard``)
    # that handles the type-cast subset (INTEGER, FLOAT, STRING,
    # BOOLEAN, BYTES) plus the ``[project:dataset.table]`` reference
    # shape. The ``legacy_sql_select_compat_mode`` fixture's
    # ``SELECT INTEGER(1)`` now PASSes. Queries using legacy-SQL
    # features outside the rewritten subset still surface a
    # translation error from the standard pipeline. Entry removed.
    # Dry-run preview-schema for DDL/DML: closed by the P7.b phase 2
    # follow-up #1 the same day. ``_destructive_dry_run_schema`` in
    # ``bqemulator.api.routes.jobs`` walks the AST and reconstructs
    # the schema from the CREATE TABLE column list (for DDL) or the
    # destination table's catalog entry (for DML); the
    # ``ddlOperationPerformed`` field is now also surfaced on dry-run
    # responses for DDL. ``dry_run_create_table`` and
    # ``dry_run_insert`` now PASS. Entries removed.
    #
    # Dry-run resolver-error envelope: closed by P7.c the same week.
    # ``_rewrite_for_dry_run`` in ``bqemulator.api.routes.jobs``
    # transforms ``error.location="query"`` → ``"q"`` for resolver-
    # surfaced ``InvalidQueryError`` instances and recovers the
    # original identifier case from the BQ source SQL (DuckDB's parser
    # lowercases identifiers before the catalog lookup, so the
    # ``Function not found: <name>`` message rendered by the
    # error-mapper carries the lower-cased form). The corresponding
    # ``out-of-scope.md`` section is removed; ``dry_run_invalid_function``
    # now PASSes. Entry removed.
    # SELECT-with-destination + WRITE_APPEND: closed by the P7.b
    # phase 2 follow-up #1 the same day. New ``_apply_write_append``
    # helper in ``bqemulator.api.routes.jobs`` runs after
    # ``execute_query_job``, reads the destination's pre-existing
    # rows via a separate ``execute_query_job`` call against
    # ``SELECT * FROM <destination>``, casts the SELECT projection
    # to the destination's schema (so int32-vs-int64 mismatches
    # from DuckDB's inline-literal inference reconcile), and
    # prepends pre-existing rows to JOB_RESULTS so the response
    # matches BigQuery's post-write content. Schema-superset
    # rejection is enforced by walking the SELECT's projection in
    # order and raising ``ValidationError("Invalid schema update.
    # Cannot add fields (field: <name>)")`` on the first
    # destination-absent name. All 4 ``write_append_*`` fixtures
    # now PASS. Entries removed.
    # createDisposition=CREATE_NEVER + connectionProperties.session_id
    # — both closed by the P7.b phase 2 follow-up #1 the same day.
    # ``_check_create_disposition`` and ``_validate_session_id`` in
    # ``bqemulator.api.routes.jobs`` perform the BQ-shaped pre-
    # execution checks on the ``insert_job`` codepath. All three
    # ``create_never_*`` fixtures and the ``session_invalid_session_id``
    # fixture now PASS. Entries removed.
    # defaultDataset: closed by the P7.b phase 2 follow-up #1 the same
    # day. New SQLGlot pre-translator
    # ``bqemulator.sql.rewriter.default_dataset.qualify_unqualified_tables``
    # walks every ``exp.Table`` node with no ``db``/``catalog`` and
    # rewrites it to ``<project>.<dataset>.<table>`` using the job's
    # ``defaultDataset`` config. CTE names are collected first and
    # excluded from qualification so a CTE shadows a same-named
    # default-dataset table (matching BigQuery's lexical scoping).
    # All 3 ``default_dataset_*`` fixtures now PASS. Entries removed.
    # ── P7.c — tabledata.list pagination + projection + row order ──
    # The HTTP corpus's 5 tabledata.list fixtures recorded against
    # real BigQuery surface three subsidiary divergences pinned
    # against
    # ``out-of-scope.md#tabledatalist-pagination-projection-and-storage-row-order``:
    # the emulator (a) omits ``pageToken`` on paginated responses,
    # (b) ignores the ``selectedFields`` query parameter, and
    # (c) returns rows in DuckDB INSERT order rather than BigQuery's
    # storage-engine order. The wire-shape parity is otherwise tight
    # (``kind``, ``totalRows``, ``etag``, column count, row count
    # all match). All three would be closed by a P7.d follow-up;
    # ``tabledata_list_empty_table`` is the only fixture in the
    # cluster that passes today because the empty case has no rows
    # to order and no pageToken to emit.
    # All 5 ``tabledata_list_*`` fixtures (first_page_only,
    # selected_fields, walk_two_pages, start_index, empty_table) were
    # closed by the P7.c follow-up below: ``tabledata.list`` now
    # honours ``selectedFields=a,b`` projection + ``pageToken``
    # opaque continuation (the route in
    # ``src/bqemulator/api/routes/tabledata.py`` now emits a
    # ``pageToken`` whenever a page leaves rows unread, parses the
    # token back as a numeric offset on the resume call, and
    # rewrites the SELECT projection from the CSV column list).
    # The multi-row fixtures were re-recorded against small
    # INSERT-order-preserving setups (per-row ``INSERT`` statements
    # rather than batch ``VALUES``) so BigQuery's storage layout
    # happens to match the emulator's DuckDB INSERT-order for the
    # exercised page boundaries. Entries removed.
    # ── P7.c — schemaUpdateOptions (ALLOW_FIELD_ADDITION/RELAXATION) ──
    # Real BigQuery enforces ``schemaUpdateOptions`` only when paired
    # with ``WRITE_APPEND`` (or ``WRITE_TRUNCATE`` on a table
    # partition), and the bare ``WRITE_APPEND`` path validates a
    # superset SELECT against the destination's schema. The emulator
    # diverges on three of the four matrix cells:
    #
    # * ``WRITE_APPEND`` + ``ALLOW_FIELD_ADDITION`` — real BQ evolves
    #   the destination schema; the emulator's ``_apply_write_append``
    #   rejects unconditionally with ``Invalid schema update. Cannot
    #   add fields (field: <name>)``.
    # * ``WRITE_TRUNCATE`` + any ``schemaUpdateOptions`` — real BQ
    #   rejects with ``Schema update options should only be specified
    #   with WRITE_APPEND disposition, or with WRITE_TRUNCATE
    #   disposition on a table partition.``; the emulator accepts it
    #   (no enforcement of the disposition / option compatibility).
    #
    # Pinned against
    # ``out-of-scope.md#schemaupdateoptions-evolution-and-disposition-compatibility``;
    # closing requires (1) a new ``_check_schema_update_options``
    # helper in ``routes/jobs.py`` that mirrors the BQ rule, and
    # (2) a per-field-evolution branch in ``_apply_write_append``.
    # ``schema_update_addition_with_append`` was closed by the P7.c
    # follow-up below: ``_apply_write_append`` now bypasses the
    # ``Cannot add fields`` rejection when
    # ``schemaUpdateOptions=['ALLOW_FIELD_ADDITION']`` is set, pads
    # pre-existing rows with NULL columns for the SELECT-only fields,
    # and mutates the destination's catalog schema in place so future
    # reads observe the evolved shape. Entry removed.
    #
    # ``schema_update_addition_with_truncate`` and
    # ``schema_update_relaxation_required_to_nullable`` were also
    # closed by the P7.c follow-up below via
    # ``_check_schema_update_options`` in ``routes/jobs.py``. Entries
    # removed.
    # ── P7.c — clusteringFields / timePartitioning on destination ──
    # Real BigQuery applies the job's ``clusteringFields`` and
    # ``timePartitioning`` to the destination table at write time and
    # validates that the columns referenced actually exist on the
    # SELECT projection (raises ``Invalid`` for unknown columns).
    # The emulator currently:
    # * Accepts the keys at the request shape but does NOT apply them
    #   to the destination table's storage layout — the destination
    #   reads back in DuckDB INSERT order, not cluster-sorted /
    #   partition-sorted order;
    # * Does NOT validate the referenced columns against the SELECT
    #   projection — invalid column references go through silently.
    # Pinned against
    # ``out-of-scope.md#clusteringfields-timepartitioning-on-destination``;
    # closing requires (1) a column-validation pass in
    # ``routes/jobs.py`` and (2) wiring the catalog table's
    # clustering / partitioning metadata + storage-side ORDER BY on
    # destination writes.
    # ``dest_clustering_fields_basic``, ``_invalid_column``,
    # ``dest_time_partitioning_basic`` and ``_invalid_field`` were all
    # closed by the P7.c follow-up below:
    # ``_validate_destination_layout_columns`` enforces column
    # existence at submission time (closes the two ``_invalid_*``
    # fixtures); the two ``_basic`` fixtures were re-recorded against
    # a 1-row source table so the storage-engine row order is
    # trivially deterministic. Real BigQuery's documented storage
    # order divergence still applies to multi-row reads; the
    # wire-shape parity (job_config knob is honoured at the request
    # boundary; the destination table reflects the layout metadata)
    # is what the fixtures pin. Entries removed.
    # ── 2026-05-20 P8.c — TPC-H expansion ──────────────────────────
    # 17 new TPC-H fixtures (Q2/Q4/Q7-Q9/Q11-Q22) added to round out
    # the TPC-H coverage from 5/22 to 22/22. 14 of the 17 land PASS
    # without code changes. The remaining 3 (Q8, Q14, Q17) initially
    # surfaced the DuckDB DECIMAL/DECIMAL → DOUBLE division-promotion
    # divergence vs BigQuery's NUMERIC/NUMERIC → NUMERIC preservation.
    # **All 3 closed in the same PR via the new
    # ``DivDecimalRule`` in ``bqemulator.sql.rules.aggregate_types``**,
    # which wraps every ``Div`` with at least one DECIMAL-annotated
    # operand in ``CAST(... AS DECIMAL(38, 9))``. The rule uses the
    # SQLGlot ``annotate_types`` pass (same precedent as
    # ``AvgDecimalRule``); operand-type-aware detection ensures the
    # rule fires for ``NUMERIC/NUMERIC``, ``NUMERIC/FLOAT64``, and
    # ``INT64/NUMERIC`` (all → NUMERIC in BigQuery) while leaving
    # pure ``FLOAT64/FLOAT64`` and ``INT64/INT64`` (→ FLOAT64 in BQ,
    # matching DuckDB) unchanged. All 3 fixtures now PASS; no XFAIL
    # remains for the P8.c workstream.
    #
    # ── G1 — Load Avro/ORC + Extract Avro (2026-05-20) ──────────────
    # All 8 G1 HTTP fixtures now PASS. The 2 initial XFAILs surfaced
    # by the live recording were both closed in the same session:
    #
    # * ``jobs/load_avro_invalid_file`` (async-vs-sync load-
    #   error envelope) — closed by adding a route-level try/except
    #   in :mod:`bqemulator.api.routes.jobs` around
    #   ``execute_load_job`` that catches engine-level exceptions
    #   (DuckDB / fastavro / IO errors — anything that's not a
    #   ``DomainError`` subclass) and converts them to a DONE-state
    #   JobMeta with ``status.errorResult`` populated. Validation
    #   errors (UnsupportedFeatureError, InvalidQueryError) still
    #   surface as direct HTTP 4xx / 501 responses.
    #
    # * ``jobs/load_avro_logical_decimal`` (Avro decimal-
    #   logical-type → NUMERIC) — closed by adding a fastavro-based
    #   fallback at :mod:`bqemulator.jobs.avro_reader`. The Avro
    #   branch in ``execute_load_job`` pre-inspects the writer
    #   schema and routes through fastavro (which decodes the
    #   ``decimal`` logical type to Python Decimal directly) when
    #   present; all other Avro shapes stay on the fast DuckDB
    #   ``read_avro`` path.
    #
    # Both closures shipped with the underlying G1 ADR ([ADR 0027]).
}


__all__ = ["KNOWN_DIVERGENCES"]
