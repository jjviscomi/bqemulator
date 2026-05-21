# Adding conformance cases

Conformance tests live at `tests/conformance/sql_corpus/`. Each case is
one `.sql` file plus a JSON snapshot of the expected result.

## Structure

```
tests/conformance/sql_corpus/
  safe_divide.sql
  safe_divide.snapshot.json
```

The `.sql` file contains the query with a header comment:

```sql
-- Fixture: none | tpch_sf0_01 | custom_<name>
-- Description: SAFE_DIVIDE returns NULL for zero divisor.
SELECT SAFE_DIVIDE(10.0, 0) AS zero_case,
       SAFE_DIVIDE(10.0, 4) AS normal_case;
```

The `.snapshot.json` is produced by running the query against real
BigQuery; commit only the JSON once verified:

```bash
scripts/record_conformance_fixtures.py safe_divide \
    --bq-project my-test-project
```

The script writes `safe_divide.snapshot.json` containing the query
result and the BigQuery job schema.

## Runner

`tests/conformance/runner.py` iterates over every `.sql` file,
executes it against the emulator, and diffs the result against the
snapshot row-for-row with type-aware tolerance (floats within 1e-9,
timestamps within ±1 µs).

## Expectations

- **One query per file.** Keeps diff failures scoped.
- **Deterministic fixture data.** Use TPC-H SF0.01 or a custom fixture
  loaded via `scripts/load_fixture_<name>.py`.
- **No absolute timestamps** unless the query pins them via literals
  (`TIMESTAMP '2024-04-15 00:00:00 UTC'`).
