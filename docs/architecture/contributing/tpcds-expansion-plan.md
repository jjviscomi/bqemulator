# TPC-DS expansion plan — closing the 29-query gap

The conformance corpus today carries **70 of 99** TPC-DS queries (and
22/22 TPC-H). This document tracks the planned expansion to **99/99**
and lives in the repo so the work survives session boundaries.

## What's missing

29 queries, enumerated below in numerical order with a one-line
**complexity hint** (table count + key SQL feature). "Easier" rows are
roughly 2-4 tables and a single GROUP BY or window function; "harder"
rows are 5+ tables, multi-CTE chains, or ROLLUP/GROUPING SETS.

| # | Q | Hint (tables / key feature) | Complexity |
|---|---|---|---|
| 1 | q10 | 8 tables: customer, c_address, c_demo, h_demo, store_sales, web_sales, catalog_sales, date_dim. EXISTS to 3 fact tables. | hard |
| 2 | q11 | 6 tables: customer, c_address, store_sales, web_sales, date_dim. Multi-CTE: year1 / year2 ratio. | medium |
| 3 | q13 | 4 tables: store_sales, store, customer_demographics, household_demographics + date_dim. Three OR'd customer-segment filters + DECIMAL aggregates. | medium |
| 4 | q19 | 5 tables: date_dim, store_sales, item, customer, customer_address, store. Manager-rank by brand-category-state. ORDER BY i_brand_id, item, store; LIMIT 100. | medium |
| 5 | q25 | 5 tables: store_sales, store_returns, catalog_sales, store, item, date_dim ×3 (sold + returned + reseller). Customer return ratio. | hard |
| 6 | q26 | 4 tables: catalog_sales, customer_demographics, date_dim, item, promotion. Gender + marital + ed-status filter; promotion ID predicates. | medium |
| 7 | q30 | 6 tables: web_returns, date_dim, customer_address, customer + customer-state ranking. Top-100 customers per state via CTE. | hard |
| 8 | q32 | 3 tables: catalog_sales, item, date_dim. SUM(cs_ext_discount_amt) > 1.3 × AVG(...) correlated subquery. | medium |
| 9 | q33 | 8 tables (3 channels × 3 dimension tables): store/web/catalog_sales × item × date_dim × customer_address. UNION ALL across 3 channels with GMT-zone filter. | hard |
| 10 | q40 | 3 tables: catalog_sales, catalog_returns, date_dim, item, warehouse. Returns net-sales by warehouse + item, date-range PIVOT. | medium |
| 11 | q46 | 6 tables: store_sales, customer_demographics, household_demographics, customer, customer_address, store, date_dim. Multi-segment customer filter. | medium |
| 12 | q54 | 8 tables: catalog_sales + web_sales + customer + c_address + store + item + date_dim. Customer-ROI per item class. Multi-CTE. | hard |
| 13 | q58 | 3 tables: store_sales / web_sales / catalog_sales × item × date_dim. Item-rank by week sales across 3 channels. UNION ALL + window. | medium |
| 14 | q59 | 3 tables: store_sales, date_dim, store. Week-over-week sales ratio. Self-join on week_seq. | medium |
| 15 | q60 | 8 tables: store/web/catalog_sales × item × date_dim × customer_address. Sales channel UNION ALL + GROUPING SETS-like ROLLUP-by-item. | hard |
| 16 | q61 | 8 tables: store_sales + promotion + date_dim + customer + customer_address + item + store. Promotion-driven sales ratio (with-promotion / no-promotion). | medium |
| 17 | q62 | 5 tables: web_sales, warehouse, ship_mode, web_site, date_dim. Shipping-mode aggregates with NULLs for un-shipped. | medium |
| 18 | q68 | 6 tables: store_sales, date_dim, store, household_demographics, customer_address, customer. Customer-store visit counts. | medium |
| 19 | q69 | 5 tables: customer, customer_address, customer_demographics, store_sales/web_sales/catalog_sales (EXISTS to one, NOT EXISTS to other two). | hard |
| 20 | q71 | 6 tables: web_sales / catalog_sales / store_sales × item × time_dim × date_dim × promotion. By-hour sales rank across 3 channels. | medium |
| 21 | q74 | 6 tables: customer, store_sales, web_sales, date_dim. Year-over-year ratio. Multi-CTE (year1 / year2 per channel). | medium |
| 22 | q76 | 3 tables (one of: store/web/catalog_sales) × item × date_dim. NULL with missing-value statistics by channel. UNION ALL. | medium |
| 23 | q78 | 3 tables: store_sales/web_sales/catalog_sales paired with returns. Sales-vs-returns ratio per customer per year. | medium |
| 24 | q80 | 8 tables: store_sales / web_sales / catalog_sales each + their returns + date_dim + item + promotion. Returns ratio UNION ALL across 3 channels with ROLLUP. | hard |
| 25 | q89 | 4 tables: item, store_sales, date_dim, store. Year-over-year monthly trend with AVG window. | medium |
| 26 | q91 | 5 tables: call_center, catalog_returns, date_dim, customer, customer_address, customer_demographics, household_demographics. Call-center returns by manager. | hard |
| 27 | q92 | 4 tables: web_sales, item, date_dim. SUM(ws_ext_discount_amt) > 1.3 × AVG correlated subquery. | medium |
| 28 | q94 | 4 tables: web_sales, web_returns, date_dim, customer_address, web_site. State-filtered returns ratio. | medium |
| 29 | q95 | 5 tables: web_sales, web_returns, date_dim, customer_address, web_site. Adds late-shipped filter on top of q94's shape. | medium |

Counts: 20 medium, 9 hard.

**Recommended execution order:** ascending complexity within family,
so the easiest fixtures land first, prove the toolchain, and the hard
ones benefit from the seed-data patterns the easier ones establish.

## Per-query recipe

Each fixture is a directory under
``tests/conformance/sql_corpus/standard_functions/tpcds_q<N>/``
containing exactly three files:

1. **``setup.sql``** — minimal seeded data (CREATE OR REPLACE TABLE
   ``${DATASET}.<name>`` + INSERT VALUES). The dataset is per-fixture
   and ephemeral; ``${DATASET}`` is substituted by the recorder at
   record-time. Seed rows are designed so the query returns a
   **small, non-empty, deterministic** result — never an empty set
   (which would fail to prove the query did anything), never
   thousands of rows (which bloats ``expected.json``). Target:
   1-20 result rows.

2. **``query.sql``** — the TPC-DS reference query body, adapted to
   BigQuery's syntax. References tables via
   ``\`${DATASET}.<table>\``` backtick quoting. Template substitution
   variables in the TPC-DS spec (e.g., ``[YEAR]``, ``[STATE]``) are
   hard-coded to specific values matching the seed data.

3. **``expected.json``** — the recorder writes this file with the
   real-BigQuery result, schema, job_id, total_bytes_processed,
   duration_class, and statement_type. **Never hand-edit** —
   the runner cross-checks against the recorded job_id.

The triple is round-tripped end-to-end by
``make test-conformance`` (offline, replay-only — no BQ creds
needed; the baselines are committed) and re-recorded by
``make record-conformance`` (live, requires the GCP project + ADC
credentials).

### Adapting TPC-DS reference SQL to BigQuery

TPC-DS templates are ANSI SQL with a few Oracle-isms. Required
adaptations:

| TPC-DS reference | BigQuery |
|---|---|
| ``"identifier"`` (double-quoted) | ``\`identifier\``` (backtick) — only for column / table identifiers that need quoting; otherwise unquoted. |
| ``schema.table`` | ``\`${DATASET}.table\``` — single-segment dataset binding via the recorder. |
| ``DATE '2000-01-01'`` | ``DATE "2000-01-01"`` (double-quoted literal — BQ-preferred) |
| ``INTERVAL '30' DAY`` | ``INTERVAL 30 DAY`` (no quotes around the integer) |
| ``DECIMAL'123.45'`` | ``NUMERIC "123.45"`` (BQ's DECIMAL is named NUMERIC) |
| ``COUNT_BIG(*)``, ``DECODE()`` | Avoid — use ``COUNT(*)``, ``CASE WHEN`` instead. |
| ``ROWNUM``, ``CONNECT BY`` | Out of scope; never appears in TPC-DS but worth flagging. |

The 70 existing fixtures collectively exercise every adaptation
pattern. When in doubt, grep the existing corpus for the same
function / construct.

### Seed-data sizing

The conformance recorder enforces a 1 GiB per-fixture byte-scan
cap. Real TPC-DS data at SF=1 is several GiB per fact table — the
emulator's corpus deliberately uses **minimal seeded data** (a few
rows per dimension; a few rows per fact) so:

* Each recording job scans < 1 MiB (well under the cap).
* The total cost of recording all 29 fixtures is ~$0.001 (TPC-DS SF=1
  scan cost would be ~$5).
* The recorded result rows fit in a hand-readable ``expected.json``
  (typically 5-30 rows).

Seed-data design rule: pick the smallest set of rows that exercises
**every WHERE-clause branch** the query has. If the query has
``WHERE region IN ('US','EU','APAC')``, seed 3 rows with those
regions + 1 row with a different region (so the filter actually
filters something).

### Anti-patterns

* **Don't** copy TPC-DS template literally; the template has
  substitution variables. Replace them with concrete values that
  match seed data.
* **Don't** seed data that returns empty results — that proves
  nothing.
* **Don't** seed thousands of rows — recorder + runner both pay
  the cost.
* **Don't** hand-edit ``expected.json`` — the runner cross-checks
  the recorded job_id.
* **Don't** use random / non-deterministic functions in the query
  body (``RAND``, ``GENERATE_UUID``, ``SESSION_USER``,
  ``CURRENT_*``). These are documented as conformance-excluded by
  ADR 0022 §1.2.

## Cost guardrails

| Guardrail | Value | Where |
|---|---|---|
| Per-fixture byte-scan cap | 1 GiB (configurable via the ``--byte-cap`` flag) | ``scripts/record_conformance_fixtures.py`` — the ``DEFAULT_BYTE_CAP`` constant + the ``--byte-cap`` CLI flag |
| Project billing | ``$BQEMU_CONFORMANCE_PROJECT`` (operator-supplied) | ``Makefile`` ``record-conformance`` target |
| Expected total scan for all 29 fixtures | < 29 MiB | seeded-data sizing rule |
| Expected total cost (US multi-region, on-demand) | < $0.01 | $5 / TiB scanned |

The cap is a safety net: any well-formed TPC-DS fixture with
minimal seeded data scans single-digit kilobytes, multiple orders
of magnitude below the cap.

## Recording flow (operator)

```bash
# 1. Authenticate to GCP (one-time per terminal session).
gcloud auth application-default login

# 2. Set the project that owns the recording dataset.
export BQEMU_CONFORMANCE_PROJECT=<your-gcp-project>
export BQEMU_CONFORMANCE_LOCATION=US   # or EU; matches existing corpus

# 3. (Optional) Filter to a single fixture or substring.
python scripts/record_conformance_fixtures.py \
    --project "$BQEMU_CONFORMANCE_PROJECT" \
    --location "$BQEMU_CONFORMANCE_LOCATION" \
    --filter tpcds_q12

# Re-record everything (rare; almost always run --filter instead):
make record-conformance

# 4. Replay offline to confirm the recording matches the emulator.
make test-conformance -- -k tpcds_q12

# 5. Commit setup.sql + query.sql + expected.json together.
```

## What's NOT changing

* **No new SQL rules** are needed — every missing query uses
  features already supported by the 92-rule translator. If a
  recording reveals an unsupported feature, raise it in the chip's
  follow-up notes (the gap analysis lives in
  ``docs/reference/gap-analysis.md``).
* **No conformance-corpus exclusion changes** —
  ``tests/conformance/_surface_inventory.py``'s SESSION_USER /
  GENERATE_UUID exclusions (ADR 0022 §1.2) stand; the 40 new
  queries don't use either function.
* **No ADR** — adding more fixtures of an existing shape isn't an
  architectural decision. The shape's design is in
  [ADR 0022](../../adr/0022-conformance-corpus-design.md).

## Open questions (resolve before starting bulk recording)

1. **Should any of the 29 queries be excluded from the corpus?**
   The existing 70 are unanimously inclusion-worthy; the 29 missing
   are similarly mainstream. **Recommendation**: include all 29.

2. **Cost approval.** The total recording cost is ~$0.01 against
   the supplied GCP project's billing account. No approval needed
   for that order of magnitude.

3. **Re-record cadence.** TPC-DS query bodies are stable
   (specifications change rarely). Re-recording is only needed when
   the emulator changes a translator rule whose output diverges
   from the recorded baseline; that's caught by the per-PR replay
   step. **No periodic re-record needed.**
