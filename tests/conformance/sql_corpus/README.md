# Conformance SQL corpus

Every directory under this folder is one **conformance fixture**: a
canonical query whose recorded output from real BigQuery is the
ground-truth baseline the emulator is diffed against.

## Layout

    tests/conformance/sql_corpus/
        <phase>/
            <fixture_name>/
                query.sql         # required — the query under test
                setup.sql         # optional — fixture seed (DDL/DML) run before the query
                setup_rest.json   # optional — REST operations run after setup.sql
                headers.json      # optional — per-query HTTP headers (e.g. X-Bqemu-Caller)
                parameters.json   # optional — query parameters (P2.e)
                job_config.json   # optional — QueryJobConfig variations (P7.a)
                expected.json     # generated — recorded baseline from real BigQuery

`<phase>` corresponds to a phase in the roadmap and groups fixtures
by which subsystem they primarily exercise:

| Subdir | Exercises |
|---|---|
| `rest_crud` | basic SELECT / WHERE / GROUP BY / ORDER BY / JOIN / DML / set ops |
| `partitioning_clustering` | wildcard tables, `_TABLE_SUFFIX`, partition-pruned queries |
| `routines_scripting` | SQL UDFs, scripting, multi-statement queries |
| `versioning` | static snapshot / clone / materialized-view queries |
| `row_access` | views, row-access-policy filter enforcement, authorized-view delegation, caller-identity edge cases (RAP + view fixtures use `setup_rest.json` + `headers.json` to express the caller-bound shape) |
| `specialized_types` | GEOGRAPHY, RANGE, INTERVAL |
| `standard_functions` | string / numeric / date-time / array / struct / json / aggregate / conversion |
| `api_configuration` | `QueryJobConfig` variations — same SQL, different request configurations (`useQueryCache=false`, `priority=BATCH`, `dryRun=true`, `labels`, `parameterMode=POSITIONAL`, DML `statementType` + `numDmlAffectedRows`, …). See [`api-configuration-coverage-matrix`](../../../docs/reference/api-configuration-coverage-matrix.md) for the canonical list. |

`admin/` is intentionally absent — admin endpoints are
metadata HTTP routes, not SQL, and so do not have a conformance
shape.

## Picking what to author next

Before authoring new fixtures, check the
[**conformance coverage matrix**](../../../docs/reference/conformance-coverage-matrix.md).
It enumerates every BigQuery surface item the corpus targets (412
items across 19 categories at last count — see the matrix for the
authoritative current value) and ranks each by current fixture
depth (🔴 Uncovered / 🟡 Sampled / 🟢 Covered / 🟢🟢 Deep).
The matrix's "top 30 0-fixture surface items" gap section is the
fastest-payoff target for a fresh authoring session.

If you're authoring fixtures for a *new* BigQuery surface that isn't
yet in the inventory, add a `SurfaceItem` entry to
[`tests/conformance/_surface_inventory.py`](../_surface_inventory.py)
first — then `make coverage-matrix` regenerates the doc, and your
new fixtures count toward the right cells. The
`make coverage-matrix-check` step in `make verify` blocks PRs that
let the matrix drift from the inventory or the corpus.

## Authoring conventions

1. **Placeholders are UPPER-CASE only.** The recorder and the runner
   accept these tokens; anything else raises at substitution time:
   - `${DATASET}` — fully-qualified `project.dataset_id` form.
   - `${PROJECT}` / `${DATASET_ID}` — the split forms used by
     `setup_rest.json` URL paths.
   - `${PRINCIPAL}` — IAM-member string. The recorder substitutes
     this with `BQEMU_CONFORMANCE_PRINCIPAL` (its ADC identity); the
     runner substitutes with `user:alice@example.com` by default.
   - `${GROUP}` — IAM `group:` member used by group-grantee
     fixtures. Recorder gets `BQEMU_CONFORMANCE_GROUP`; runner gets
     `group:engineering@example.com` by default.
   - `${OTHER_PRINCIPAL}` — IAM-member string for a "real but not
     the caller" grantee, used by "denied"-pattern fixtures (where
     the policy grants a non-caller principal so the caller sees
     zero rows). Real BigQuery validates RAP grantees as real IAM
     principals, so this can't be a fake placeholder like
     `user:nobody@example.com`. Recorder reads
     `BQEMU_CONFORMANCE_OTHER_PRINCIPAL` (a real principal that
     exists in the recording project — the default compute service
     account `serviceAccount:<projnum>-compute@developer.gserviceaccount.com`
     is a convenient choice); runner substitutes
     `serviceAccount:other@example.com` by default.
2. **Fixtures with `setup.sql` or `setup_rest.json` get a temp
   dataset; literal-only fixtures don't.** The runner avoids dataset
   creation when both are absent, which keeps the parametrised suite
   fast for the ~70% of fixtures that operate on literals.
3. **`CREATE OR REPLACE TABLE`, not `CREATE TABLE`.** Setup scripts
   must be idempotent so re-recording the same fixture is a no-op.
4. **Single-statement, semicolon-terminated.** Multi-statement
   setup is supported (the splitter handles `;` boundaries) but
   block comments (`/* … */`) are not.
5. **Deterministic output.** Queries that depend on
   `CURRENT_TIMESTAMP()`, `RAND()`, `SESSION_USER()`, or any other
   wall-clock or session-derived value are excluded — see ADR 0022.
6. **REST setup runs after SQL setup.** `setup.sql` always runs
   before `setup_rest.json` so tables created by SQL are available
   when REST calls reference them. Any datasets created by
   `setup_rest.json` (via `POST /datasets`) are auto-cleaned at
   teardown by both the recorder and runner.
7. **Headers apply to the canonical query only.** `headers.json`
   sets HTTP headers on the `query.sql` request; setup steps run
   under the default identity. On real BigQuery the headers are
   ignored (BigQuery uses ADC); on the emulator the headers drive
   row-access enforcement per
   [ADR 0018](../../../docs/adr/0018-caller-identity-and-row-access-enforcement.md).
8. **Parameters are submitted via `QueryJobConfig`, not by string
   substitution.** `parameters.json` carries a typed parameter list
   that the recorder + runner both pass to
   `bigquery.QueryJobConfig(query_parameters=...)`. The wire-format
   `queryParameters` field on the REST body is what's exercised —
   string-interpolating values into the SQL would defeat the purpose.
   Authoring shape:

   ```json
   {
     "mode": "named",
     "parameters": [
       {"name": "n", "type": "INT64", "value": 42},
       {"name": "ids",
        "type": {"type": "ARRAY", "arrayType": {"type": "INT64"}},
        "value": [1, 2, 3]},
       {"name": "profile",
        "type": {
          "type": "STRUCT",
          "structTypes": [
            {"name": "name", "type": "STRING"},
            {"name": "age", "type": "INT64"}
          ]
        },
        "value": {"name": "Alice", "age": 30}}
     ]
   }
   ```

   For positional (`?` placeholder) queries, `mode` is `"positional"`
   and entries omit `name`. NULL values are written as JSON `null`.
   See `rest_crud/param_*` for ~15 reference fixtures.
9. **`QueryJobConfig` variations live in `job_config.json` (P7.a).**
   Same SQL, different request configuration → different response.
   The on-disk shape is a top-level JSON object with any of the keys
   documented in
   [`tests/conformance/_job_config.py::SUPPORTED_KEYS`](../_job_config.py).
   Examples:

   ```json
   {"use_query_cache": false}
   ```

   ```json
   {"priority": "BATCH", "labels": {"fixture": "my_fixture"}}
   ```

   ```json
   {"write_disposition": "WRITE_TRUNCATE",
    "destination": "${PROJECT}.${DATASET_ID}.target"}
   ```

   The recorder and runner compose `job_config.json` with
   `parameters.json` when both are present (the job config is the
   base, the parameter list is set on the config's
   `query_parameters` attribute). See
   [`api-configuration-coverage-matrix`](../../../docs/reference/api-configuration-coverage-matrix.md)
   for the canonical configuration enumeration + recording
   priorities; see `api_configuration/*` for ~6 reference pilot
   fixtures shipped in P7.a.
10. **Response-object equivalence — `job_metadata` block (P7.a).**
    Recorded `expected.json` files can carry an optional
    `job_metadata` object with any of `cache_hit`,
    `statement_type`, `num_dml_affected_rows`, or
    `ddl_operation_performed`. The runner diffs only the keys
    present in the recorded baseline — keys absent are not
    asserted — so the 878 pre-P7 fixtures stay valid. The recorder
    only writes the block for fixtures with `job_config.json` (so
    parameterless legacy fixtures don't bloat with diagnostic-only
    fields).

## Recording

The recorder is the **only** path that produces `expected.json`.
Hand-editing the file is a non-negotiable disqualifier (Phase 11
non-negotiable #8) — the recorder logs the BigQuery `job_id` of the
job that produced each payload for audit.

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
python scripts/record_conformance_fixtures.py \
    --project <bq-project> \
    --location US
```

Use `--filter <substring>` to re-record a subset; `--force` to
overwrite existing baselines; `--dry-run` to print the plan without
executing.

For Phase 8 RAP fixtures, also export:

```bash
export BQEMU_CONFORMANCE_PRINCIPAL='user:test-svc@example.com'
export BQEMU_CONFORMANCE_GROUP='group:bqemu-recorders@example.com'  # optional, group-grantee fixtures only
export BQEMU_CONFORMANCE_OTHER_PRINCIPAL='serviceAccount:<projnum>-compute@developer.gserviceaccount.com'  # the project's default compute SA
```

The principal **must** match the recording account's ADC identity
so BigQuery's RAP enforcement sees the grantee match. The group is
needed only for fixtures that use `${GROUP}` in their grantees
(the `*_group_grantee` / `*_via_group_only` fixtures). The
``OTHER_PRINCIPAL`` is needed for "denied"-pattern fixtures and
must be a real principal that BigQuery accepts at policy-creation
time but that is NOT the recording account; the project's default
compute service account (project-scoped, always exists) is the
canonical choice. The recorder exits non-zero with an explanatory
error if any selected fixture references `${PRINCIPAL}` and the env
var is unset.

## Running

```bash
make test-conformance   # offline; no credentials required
# or directly:
pytest tests/conformance -m conformance
```

The runner does **not** require credentials — it runs the corpus
against the in-process emulator and diffs against the recorded
baselines. Credentials are only needed for the recorder (and for the
weekly CI job that re-records against real BigQuery).

## Divergence policy

Fixtures whose result we expect to differ from real BigQuery (e.g.
spheroidal-vs-planar GEOGRAPHY measurements) live in the corpus with
their recorded BigQuery output AND an entry in
`tests/conformance/divergences.py` that pins them to
`@pytest.mark.xfail(strict=True)` with a rationale rooted in an ADR.
See ADR 0022 for the full divergence policy.
