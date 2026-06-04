# Adding conformance cases

Conformance tests live under `tests/conformance/sql_corpus/`. Each case
is a **directory** holding the query, optional setup, and a recorded
snapshot of the result from real BigQuery. The
[conformance-tier reference](../conformance-tier.md) is the canonical
description of the harness; this guide is the practical "how to add one"
path.

## Structure

```
tests/conformance/sql_corpus/
  <surface>/                 # surface area, e.g. rest_crud, routines_scripting
    <fixture>/               # descriptive snake_case name, e.g. safe_divide_zero_divisor
      query.sql              # the query under test (required)
      setup.sql              # DDL/DML to stage data (optional)
      expected.json          # recorded BigQuery result (required; generated)
```

Group the fixture under the `<surface>` directory that already holds
related cases.

## `query.sql`

The single query under test — no header comment. Reference the per-test
dataset through the `${DATASET}` placeholder, which the harness
substitutes at run time (`_corpus.py`):

```sql
SELECT SAFE_DIVIDE(10.0, 0) AS zero_case,
       SAFE_DIVIDE(10.0, 4) AS normal_case
```

A fixture that needs tables references them under `${DATASET}`:

```sql
SELECT id, val FROM `${DATASET}.t` ORDER BY id
```

## `setup.sql` (optional)

If the query needs tables or routines, stage them in `setup.sql`. The
runner creates a fresh per-test dataset, runs `setup.sql`, runs
`query.sql`, then drops the dataset. Literal-only fixtures omit
`setup.sql` and skip dataset creation.

```sql
CREATE TABLE `${DATASET}.t` (id INT64, val STRING);
INSERT INTO `${DATASET}.t` VALUES (1, 'a'), (2, 'b');
```

## `expected.json`

The recorded baseline — the query result plus the BigQuery job's schema
and metadata. It is **generated, not hand-written**: record it against a
real BigQuery project, then commit the JSON.

```bash
python scripts/record_conformance_fixtures.py \
    --project <your-bigquery-project> \
    --filter <surface>/<fixture>
```

`--project` (required) bills the recording jobs; `--filter` is a
substring match that limits recording to the matching
`<surface>/<fixture>` ids; `--force` overwrites an existing
`expected.json`. The recorder enforces a byte-scan cap and refuses to
overwrite without `--force`. The file shape (`fixture_version`,
`bigquery`, `schema`, `rows`, `row_count`, `duration_class`, with a
separate version-2 shape for error fixtures) is documented in the
[conformance-tier reference](../conformance-tier.md).

## Runner

`tests/conformance/test_corpus.py` discovers every fixture directory at
import time and parametrises one pytest test per fixture. It runs
`query.sql` against the emulator and diffs the result against
`expected.json` with the type-aware tolerance in `_comparison.py`
(ADR 0022 §3): `FLOAT64` via `math.isclose(rel_tol=1e-12, abs_tol=1e-15)`
and `TIMESTAMP` / `DATETIME` / `TIME` within ±1 µs. The full tolerance
table is in the [conformance-tier reference](../conformance-tier.md).

Replay the tier locally with:

```bash
make test-conformance
```

## Expectations

- **One query per fixture.** Keeps diff failures scoped to one surface.
- **Deterministic data.** Stage rows in `setup.sql`; avoid time- or
  environment-dependent values.
- **No absolute timestamps** unless the query pins them via literals
  (`TIMESTAMP '2024-04-15 00:00:00 UTC'`).
