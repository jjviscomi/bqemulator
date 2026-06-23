# ADR 0032: `bq` CLI as a fifth conformance client

- **Status**: Accepted

## Context

bqemulator's
[testing-strategy](../architecture/testing-strategy.md) commits to
four E2E client surfaces — Python, Node.js, Go, Java — to prove the
emulator speaks each official Google BigQuery SDK client library's
protocol. The four-language matrix has been a non-negotiable
principle since AGENTS.md was first drafted: "every new feature: …
e2e test(s) against live container … never skip an e2e test
language."

But four SDKs is not the full canonical client population.
Google's
[`bq` command-line tool](https://docs.cloud.google.com/bigquery/docs/reference/bq-cli-reference)
is the BigQuery CLI — distributed as part of the
`google-cloud-cli` package, used daily by data engineers, DBAs, CI
pipelines, ad-hoc shell scripts, and `dbt`/`Airflow`/`Looker`
configurations that shell out to bq for control-plane operations.

`bq` is a distinct client shape from any of the four SDK clients:

- It assembles its own REST request bodies via Python helper code
  shipped inside the gcloud SDK (different serialization choices
  than `google-cloud-bigquery`).
- Its `--format=json` / `--format=csv` / `--format=pretty` /
  `--format=sparse` output writers are reading the emulator's
  response envelopes and rendering them as text — a regression in
  the envelope's numeric/string coercion would only surface here.
- Its error renderer prints `BigQuery error in <op>: <reason>` to
  stderr, **not** a JSON error envelope. The
  emulator's
  [`error_mapper`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/jobs/error_mapper.py) feeds
  whatever the SDK clients pretty-print into their own JSON-aware
  parsers — but `bq` parses a different field path out of the
  response, and a regression that broke that field path would leave
  every SDK suite green.

The goccy `bigquery-emulator`'s
[FEATURE.md](https://github.com/goccy/bigquery-emulator/blob/main/docs/feature-support.md)
documents `bq` as part of its supported-clients matrix. Until G5
, bqemulator covered 4 of the 6 client shapes goccy
documents (Python + Node + Go + Java; we explicitly defer Ruby + PHP
per the goccy-comparison analysis). The remaining row — `bq` —
was a documented goccy-parity gap (G-14) that this ADR's
workstream closes.

## Decision

`bq` joins the conformance-client matrix as the **fifth** client.

### 1. Driven via Python subprocess from pytest

The bq-CLI E2E suite lives at
[`tests/e2e/bq_cli_client/`](https://github.com/jjviscomi/bqemulator/tree/main/tests/e2e/bq_cli_client),
mirroring the existing `tests/e2e/python_client/` /
`nodejs_client/` / `go_client/` / `java_client/` layout.

A [`bq_runner.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/e2e/bq_cli_client/bq_runner.py)
module wraps `subprocess.run([...], shell=False)` and exposes a
`BqRunner` class. Each `run(...)` call:

1. Passes `--api=<emulator_url>` per invocation (the only
   per-invocation endpoint override `bq` supports; the SDK
   client-libraries' `BIGQUERY_EMULATOR_HOST` env var is **not**
   honored by `bq`).
2. Passes `--project_id=<stable-suite-id>` so per-suite project
   isolation matches the SDK suites.
3. Sets `CLOUDSDK_AUTH_DISABLE_CREDENTIALS=true` in the subprocess
   env so `bq` doesn't refuse to talk to an unauthenticated
   endpoint.
4. Sets `CLOUDSDK_CONFIG=<per-pytest-session tmp dir>` so concurrent
   test sessions don't trample shared `~/.config/gcloud` state and
   so a failed test cannot leave the operator's local bq pointed
   at a now-stopped emulator.

### 2. Mirrors the Python-suite layout 1:1 where bq has the equivalent

The bq-CLI suite has one file per Python-suite file, named with the
same convention:

| File | Tests | Notes |
|---|---|---|
| `test_health.py` | 2 | smoke + `bq version` |
| `test_rest_crud_rest.py` | 4 | mk dataset/table, insert, query, ls, show, parameterised query |
| `test_jobs.py` | 8 | load NDJSON/CSV, extract, copy, head, DML, dry-run, rm |
| `test_partitioning_clustering.py` | 4 | time-partition + clustering metadata + partition pruning + `_TABLE_SUFFIX` wildcard |
| `test_storage_read_storage_write_skipped.py` | 2 (skip) | documents the Storage Read/Write gap |
| `test_routines_scripting.py` | 4 | SQL UDF + JS UDF + TVF + scripting block |
| `test_versioning.py` | 4 | snapshot + clone + MV + time-travel |
| `test_row_access.py` | 3 | RAP DDL + authorized view + DROP |
| `test_specialized_types.py` | 4 | GEOGRAPHY + RANGE + INTERVAL |
| `test_admin.py` | 3 | cross-dataset cp + update + recursive rm |
| `test_g4_information_schema.py` | 2 | INFORMATION_SCHEMA.TABLES + COLUMNS |
| `test_bq_cli_specific.py` | 5 | output formats +.bigqueryrc + error shape |
| **Total** | **~45** | (35 active + 2 deliberate skips for Phase 4/5; 3 of the active tests are layered on each other and may sub-trigger more pytest cases as bq output details evolve) |

### 3. Phases 4 + 5 are documented exclusions, not silent gaps

`bq` exposes **no** Storage Read / Storage Write API command. Its
closest analogues — `bq head` (`tabledata.list`) and `bq insert` /
`bq load` (REST `tabledata.insertAll` + `jobs.insert`) — exercise
**different REST surfaces** than Phase 4/5's gRPC streaming. Adding
synthetic bq tests for Storage Read/Write would mislead future
readers about what's exercised, and silently passing tests would be
worse than no tests at all.

`test_storage_read_storage_write_skipped.py` documents the gap explicitly via
`pytest.skip(...)` with a reason that points at the SDK files
carrying the gRPC contract. The skips are visible in pytest output
on every CI run — a future reader who wonders "do we exercise
Storage Read through bq?" gets the answer immediately.

### 4. Authentication bypass is subprocess-scoped, not session-scoped

`CLOUDSDK_AUTH_DISABLE_CREDENTIALS=true` is set in the subprocess
env via the `env=` parameter of `subprocess.run`, **not** in the
parent shell's `os.environ`. This means:

- Concurrent test sessions can't accidentally affect each other.
- The operator's interactive gcloud session is never touched.
- A test crash mid-run can't leave the operator's machine in an
  unexpected auth state.

### 5. CI install via `apt-get install google-cloud-cli`

The
[`.github/workflows/e2e.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/e2e.yml)
workflow gains a `bq-cli-e2e` matrix entry that installs the
gcloud SDK via the official Debian package. Verifying the
install succeeds (`bq version`) is a non-optional step — a
silent install failure would surface as `pytest.skip(...)` on
every test, masking a regression.

Local developers without gcloud SDK still get a clear message:
the `BqRunner.__init__` raises `pytest.skip` with an actionable
"install google-cloud-sdk" hint, and the `make test-e2e-bq-cli`
Makefile recipe pre-flights with a `command -v bq` check that
fails with a remediation message if the binary is missing.

### 6. Subprocess args, not shell strings

Every `bq` invocation goes through
`subprocess.run(["bq",...], shell=False)`. We never compose a
single shell string for `os.system` / `subprocess.run(shell=True)`.
This eliminates the command-injection surface that test data
(table names, query bodies, file paths) would otherwise expose.

## Consequences

### Positive

- bqemulator's E2E surface grows to **five** conformance clients,
  the broadest of any open-source BigQuery emulator at v1.0 tag
  time (goccy claims six in its FEATURE.md but doesn't ship the
  full SDK matrix bqemulator does — see the goccy-comparison
  analysis).
- The `bq` CLI's output formatters, error renderer, and request
  shapes are now CI-gated. A regression in any of those paths
  fails the build instead of shipping to users.
- The G-14 goccy-parity gap row closes.
- A user-facing
  [guide](../guides/using-bq-cli.md) + runnable
  [`docs/examples/bq-cli-quickstart/`](../examples/bq-cli-quickstart/README.md)
  ship simultaneously, so adopters who drive BigQuery from bq can
  point their CI at the emulator without reverse-engineering the
  endpoint-override and auth-bypass steps.

### Negative

- The e2e workflow's `bq-cli` job adds ~30 seconds to setup time
  for the `apt-get install google-cloud-cli` step.
- Local developers without gcloud SDK installed see a skipped test
  session for `make test-e2e-bq-cli`. We mitigate via a clear
  remediation message in both the runner (`pytest.skip` with a
  reason) and the Makefile (`command -v bq` pre-check that fails
  with an install URL).
- `bq`'s output formats and error rendering are stable but not
  contractually frozen. A `bq` upgrade in CI could surface
  cosmetic test failures (e.g., a column-padding change in
  `--format=pretty`). The tests are deliberately written to pin
  the **structural** invariants (header row exists; row count
  matches; error contains the bad-function name) rather than the
  exact byte stream, so cosmetic upgrades won't fail the suite.
- We pin a minimum `bq` version implicitly via the
  `google-cloud-cli` Debian package CI installs from
  `packages.cloud.google.com/apt`. Pinning a hard floor (e.g., `bq
  >= 2.1.0`) is deferred to a follow-up if a regression surfaces
  on an older bq version.

## Alternatives considered

### Bash test runner

A pure-bash test runner under `tests/e2e/bq_cli_client/*.sh` would
remove the Python-subprocess wrapper layer. We rejected it
because:

- pytest's fixture model (session-scoped emulator container via
  testcontainers; per-test cleanup; matrix parameterization)
  would need a hand-rolled equivalent.
- The other four E2E suites already either use pytest directly
  (Python) or use a language-native test runner that wraps a
  `docker run` invocation (Node/Go/Java). A bash suite would
  break the "one test-runner shape per client" cognitive load
  budget.
- Output-format assertions are easier in Python (`json.loads`,
  `assert rows == [...]`) than in bash (`jq`, `assert` shims).

### Go test harness wrapping bq invocations

We rejected this because Python is already the canonical test
runner for the project (see all other `tests/e2e/<client>/`
directories that use a per-client test framework, but pytest is
the lingua franca). Adding a Go runtime just to drive a
subprocess would be load-bearing complexity for no real gain.

### Skipping `bq` entirely

The deliberate gap goccy does not have. We rejected this because:

- Real users drive BigQuery through `bq` daily; regressions on
  that path are real production incidents.
- The goccy-parity column is a meaningful competitive axis at
  v1.0 tag time.
- The marginal cost (one CI matrix entry + one Python test file
  per phase) is small compared to the surface coverage gain.

### Adding Ruby + PHP CLIs

Out of scope for v1.0. Tracked as a follow-up RFC if user demand
surfaces. The goccy-comparison analysis explicitly deferred Ruby
and PHP per the project's strict "no new toolchains in CI without
a user request" policy.

## See also

- The
  [Using the bq CLI](../guides/using-bq-cli.md) guide.
- The
  [`docs/examples/bq-cli-quickstart/`](../examples/bq-cli-quickstart/README.md)
  runnable example.
- The
  [`tests/e2e/bq_cli_client/`](https://github.com/jjviscomi/bqemulator/blob/main/tests/e2e/bq_cli_client/) suite.
- The [goccy `bigquery-emulator`
  FEATURE.md](https://github.com/goccy/bigquery-emulator/blob/main/docs/feature-support.md)
  parity baseline.
- ADR 0018 (caller-bound row access policy enforcement) — explains
  why caller-grant testing happens in the Python suite, not the bq
  suite (bq has no header-injection flag).
- ADR 0019 (spheroidal vs planar) — why the Phase 9 GEOGRAPHY
  tests in this suite assert against POINT/LINESTRING happy paths
  but not the spheroidal-buffer cluster.
