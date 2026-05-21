# The conformance corpus

The conformance corpus is the emulator's promise that its SQL output
matches real BigQuery for the queries we care about. Every fixture in
`tests/conformance/sql_corpus/` is a canonical query whose *recorded*
output from real BigQuery is diff'd against the emulator's output at
test time. This guide explains how to read it, add a fixture,
re-record after a change, and interpret divergences.

For the design rationale see [ADR 0022](../adr/0022-conformance-corpus-design.md).
For the tier's place in the broader strategy see
[testing-strategy.md](../architecture/testing-strategy.md) (Tier 5).

## Quick start

Run the corpus against the in-process emulator (no credentials
required):

```bash
make test-conformance
# …or directly:
pytest tests/conformance -m conformance
```

The runner is fully offline — it re-uses each fixture's recorded
`expected.json` and only talks to the emulator. Re-recording is a
separate, credentialed action (see "Recording" below).

## Adding a fixture

A fixture is a directory under `tests/conformance/sql_corpus/<surface>/`
with this shape:

```
tests/conformance/sql_corpus/
    rest_crud/
        select_where_eq/
            query.sql         # the SQL under test (required)
            setup.sql         # fixture seed (optional)
            expected.json     # baseline from real BigQuery (generated)
```

Pick the `<surface>` subdir that best matches the surface the fixture
exercises. The catalog is in
[`sql_corpus/README.md`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/sql_corpus/README.md).

### Authoring rules

1. **`${DATASET}` is the only placeholder.** Reference your seed
   tables as `` `${DATASET}.orders` `` (note the backticks — BigQuery
   needs them for fully-qualified names that include `-` in the
   project segment).
2. **Use `CREATE OR REPLACE TABLE` in `setup.sql`.** The recorder
   re-runs the setup on every `--force` recording; idempotent DDL
   keeps re-recording deterministic.
3. **No time-dependent SQL.** `CURRENT_TIMESTAMP()`, `CURRENT_DATE()`,
   `RAND()`, `SESSION_USER()`, `FOR SYSTEM_TIME AS OF
   <relative>` are all excluded by ADR 0022 — their baselines would
   drift on every re-record.
4. **No `INFORMATION_SCHEMA` queries.** Those return dataset names
   that vary between recording (BigQuery) and runtime (the emulator)
   and have no clean canonicalisation.
5. **Single output column types per row.** Mixing strict-int and
   string-of-int values in the same column triggers BigQuery's type
   inference differently from the emulator's; pick one.

A typical literal-only fixture:

```sql
-- tests/conformance/sql_corpus/standard_functions/str_upper/query.sql
SELECT UPPER('Hello') AS s
```

A fixture with a seed:

```sql
-- tests/conformance/sql_corpus/rest_crud/select_where_eq/setup.sql
CREATE OR REPLACE TABLE `${DATASET}.orders` (
  order_id INT64,
  customer STRING,
  amount NUMERIC,
  order_date DATE
);

INSERT INTO `${DATASET}.orders` (order_id, customer, amount, order_date) VALUES
  (1, "Alice", NUMERIC "100.00", DATE "2024-01-15"),
  (2, "Bob",   NUMERIC "250.50", DATE "2024-01-15");
```

```sql
-- tests/conformance/sql_corpus/rest_crud/select_where_eq/query.sql
SELECT order_id, customer
FROM `${DATASET}.orders`
WHERE customer = 'Alice'
ORDER BY order_id
```

## Adding an error fixture (P3.a / ADR 0022 §3)

An *error fixture* is a fixture whose ``query.sql`` is expected to
fail against real BigQuery. The runner expects the emulator to
raise a matching ``GoogleAPIError`` and diffs the four-field error
shape (``reason`` / ``location`` / ``http_status`` /
``message_pattern``) against the recorded BigQuery baseline. The
file layout is identical to a success fixture; the recorder
detects the failure and writes an ``error`` envelope to
``expected.json`` instead of ``schema`` + ``rows``:

```sql
-- tests/conformance/sql_corpus/standard_functions/error_syntax_unclosed_paren/query.sql
SELECT (1 + 2 AS x
```

Naming convention: prefix the fixture directory with ``error_`` so
``ls error_*`` enumerates the error-shape fixtures at a glance.
Choose a thematically-appropriate surface subdir
(``standard_functions/error_syntax_*`` for syntax errors,
``rest_crud/error_table_not_found`` for resource-not-found,
``routines_scripting/error_routine_not_found`` for routine references,
etc.). The naming + subdir placement is convention only — the
runner's branch is driven by the recorded ``error`` field.

If the fixture needs a dataset (e.g., for ``error_table_not_found``
the dataset must exist before the query can reference a missing
table inside it), provide a minimal idempotent ``setup.sql``:

```sql
-- tests/conformance/sql_corpus/rest_crud/error_table_not_found/setup.sql
-- The runner provisions the dataset; this no-op query forces
-- setup.sql to be non-empty (a comment-only file is rejected by
-- BigQuery's parser).
SELECT 1 AS placeholder;
```

Then record as normal (``make record-conformance --filter
error_``). The recorder will write a v2 ``expected.json`` with
an ``error`` envelope — the ``message_pattern`` is a regex with
the per-fixture dataset FQDN substituted to a wildcard and
line:column markers normalised to ``\[\d+:\d+\]``, so the same
pattern survives re-recordings against different BQ projects.
After recording, run ``make test-conformance`` to verify the
emulator's error matches BigQuery's. If a divergence surfaces,
either fix the emulator's error renderer (see
[`src/bqemulator/jobs/error_mapper.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/error_mapper.py)
for the existing translator) or add an ``xfail(strict=True)``
entry to ``divergences.py`` with an ADR-rooted rationale.

## Recording (local-only)

Recording is a **local action**, never run from CI. The recorder is
the only path that produces `expected.json` — hand-editing the file
is a non-negotiable disqualifier; every payload must include the
BigQuery `job_id` of the job that produced it.

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json
export BQEMU_CONFORMANCE_PROJECT=<bq-project-you-control>
make record-conformance
# …or invoke the script directly for finer control:
python scripts/record_conformance_fixtures.py \
    --project "$BQEMU_CONFORMANCE_PROJECT" --location US
```

After recording, `git diff` the changed `expected.json` files,
confirm the changes reflect a real upstream BigQuery change (or your
fixture edits), commit, and open a PR. CI exercises the new
baselines on the next push.

Useful flags:

| Flag | Effect |
|---|---|
| `--filter <substring>` | Re-record only fixtures whose `<surface>/<name>` contains the substring. |
| `--force` | Overwrite an existing `expected.json`. Default is to skip. |
| `--dry-run` | Print the plan without running queries. |
| `--byte-cap <bytes>` | Refuse any fixture that scans more than this (default: 1 GiB). |
| `--verbose` | DEBUG-level logging — includes the first line of each setup statement. |

The recorder logs every BigQuery job id to stdout. Save the output of
a recording session: if a baseline ever becomes suspect, the job id
lets you replay the exact query in real BigQuery's job history.

### Cost guard

The recorder enforces a per-fixture byte-scan cap (default **1 GiB**)
using BigQuery's `total_bytes_processed`. A fixture that exceeds the
cap is logged and the corresponding `expected.json` is **not**
written, so a subsequent test run reports the fixture as
"unrecorded" rather than silently picking up a stale baseline. The
recorder exits non-zero whenever the cap is hit.

For the v1.0.0 corpus the per-fixture cost is in single-digit cents
worst-case (each fixture seeds kilobytes of synthetic data).

## Interpreting test output

The runner produces per-fixture pytest output:

* **PASS** — emulator output matches the recorded baseline within
  the tolerances documented in ADR 0022 §3.
* **FAIL** — a real divergence. The failure message names the
  fixture id, the BigQuery `job_id` of the recorded baseline, and the
  first three diffing cells (column path + expected vs actual).
* **XFAIL** — the fixture is in
  `tests/conformance/divergences.py` (e.g. spheroidal-vs-planar
  GEOGRAPHY at continental scales) and the divergence still holds —
  expected.
* **XPASS** — the fixture is in the divergences dict but the emulator
  *and* real BigQuery agreed. Because `strict=True` is applied, this
  fails the test. The remediation is to delete the entry from
  `divergences.py`; the next run will re-grade as PASS.

## Adding a known divergence

When a fixture *intentionally* diverges from real BigQuery, add it to
[`tests/conformance/divergences.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py):

```python
KNOWN_DIVERGENCES["specialized_types/st_distance_continental"] = (
    "Spheroidal-vs-planar divergence — see ADR 0019 and "
    "docs/reference/out-of-scope.md#spheroidal-geometry-on-geography"
)
```

Conventions:

* The rationale **must** reference an ADR or
  `docs/reference/out-of-scope.md`. Invented divergences are
  forbidden.
* When the divergence is later closed (a fix lands, or the upstream
  changes), remove the entry. The runner's `strict=True` will then
  flag any latent re-introduction as an XPASS, which fails the suite.

## Re-recording after a real-BigQuery change

If BigQuery itself ships a behavioural change that breaks a baseline:

1. Identify the affected fixtures from CI failure output.
2. `python scripts/record_conformance_fixtures.py --filter <substring> --force`.
3. `git diff` the `expected.json` changes. Inspect them — confirm
   they reflect the real upstream change and not a recording error.
4. Commit the new baselines with a message referencing the upstream
   change.
5. Push and let CI re-run.

If the upstream change permanently breaks the emulator's behaviour:
either add the fixture to `divergences.py` with an ADR-rooted
rationale or fix the emulator before merging.

## CI integration

The conformance workflow runs **weekly** (Mondays at 07:23 UTC) and
on demand via the GitHub Actions UI. Configuration is in
[`.github/workflows/conformance.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/conformance.yml).

CI is **offline**: it installs the project, runs
`pytest tests/conformance -m conformance` against the in-process
emulator, and asserts the diff vs the recorded baselines is empty.
There is no GCP credential stored in repository secrets, the
recorder is not invoked, and no live BigQuery query is made. A
green CI run means the emulator still matches the *recorded*
baselines; a red run means the emulator either regressed or
real BigQuery's behaviour shifted in a way the maintainer needs to
triage.

Re-recording is a deliberate human action, done locally:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json
python scripts/record_conformance_fixtures.py \
    --project <bq-project> --location US --force
# review the diff, commit, open a PR
```

This keeps the GCP service-account key off GitHub, prevents an
automated drift-fixer from silently rewriting baselines, and forces
every baseline change through human review.

## Auditing the corpus

Every `expected.json` carries the BigQuery `job_id` that produced it.
To audit that the corpus values are *actually* recorded against real
BigQuery (rather than hand-tuned):

```bash
# Confirm every expected.json has a non-empty job_id
find tests/conformance/sql_corpus -name expected.json -print0 \
  | xargs -0 jq -e '.bigquery.job_id != null and .bigquery.job_id != ""' >/dev/null \
  && echo "All baselines carry a recorded job_id."
```

This invariant is enforced by a CI step that consults the recorded
`job_id` on every baseline.
