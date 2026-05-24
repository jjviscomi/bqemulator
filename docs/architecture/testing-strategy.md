# Testing strategy

bqemulator enforces a **7-tier testing pyramid**. Coverage threshold is
≥90% line and ≥90% branch, enforced by CI.

| Tier | What | Per-PR? | Other invocation |
|---|---|---|---|
| 1 Unit | Pure domain, no I/O | ✅ via [`ci.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/ci.yml) (Python 3.11/3.12/3.13 × ubuntu/macos/windows) | `make test-unit` |
| 2 Property | Hypothesis invariants | ✅ via `ci.yml` | `make test-property` |
| 3 Integration | Emulator in-process + Python client | ✅ via `ci.yml` | `make test-integration` |
| 4 E2E | Live container + five conformance clients (Python / Node / Go / Java / `bq` CLI) | ✅ via [`e2e.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/e2e.yml) | `make test-e2e` |
| 5 Conformance | Replay baselines recorded against real BigQuery | ✅ via `ci.yml` (replay-only; recording is a deliberate operator action) | `make test-conformance`; `make record-conformance` |
| 6 Performance | pytest-benchmark; per-arch baseline | manual-only — [`perf.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/perf.yml) `workflow_dispatch` | `make test-perf` |
| 7 Chaos | Fault injection + resource exhaustion + crash recovery | manual-only — [`chaos.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/chaos.yml) `workflow_dispatch` | `make test-chaos` |

Three ancillary tiers run alongside the pyramid as comparison gates
(each ships as a manual-only `workflow_dispatch` workflow per the
deferred-cadence CI policy — the per-PR vs nightly vs release-gate
decision is deferred until post-repo-setup when there is real CI
traffic to measure runtime / flakiness / runner-cost trade-offs
against):

| Sibling tier | Workflow | Local target | ADR |
|---|---|---|---|
| Mutation testing | [`mutation.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/mutation.yml) | `make test-mutation` | [ADR 0026](../adr/0026-mutation-tier-design-contract.md) |
| Differential (row-order perturbation of corpus) | [`differential.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/differential.yml) | `make test-differential` | [ADR 0028](../adr/0028-differential-tier-design-contract.md) |
| Fuzz (Atheris coverage-guided) | [`fuzz.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/fuzz.yml) | `make test-fuzz` | [ADR 0031](../adr/0031-fuzz-tier-design-contract.md) |

## Tier 1 — Unit (`tests/unit/`)

Pure domain, no I/O. Target <10s total runtime. Covers:

- Type mapping, Arrow bridge
- SQL translation rules (each rule gets its own test)
- Catalog repository contracts (against the in-memory implementation)
- Job state machine
- Domain error → ErrorProto rendering

## Tier 2 — Property (`tests/property/`)

Hypothesis-driven. Invariants rather than examples:

- SQL translation never crashes — always returns `Ok(sql)` or `Err(error)`.
- Type round-trips preserve semantics (BQ → Arrow → DuckDB → Arrow → BQ).
- Arrow bridge handles all supported types for arbitrary values.
- Scripting interpreter preserves lexical scope under arbitrary nesting.

## Tier 3 — Integration (`tests/integration/`)

Emulator in-process (pytest fixture) + official Python client:

- REST CRUD workflows
- Storage Read / Write API flows
- Error-shape compatibility with the Python client's parsing

## Tier 4 — E2E against live containers (`tests/e2e/`)

**The user-mandated bar.** Testcontainers spins a built-from-source
image; each of the five conformance clients (Python, Node, Go, Java, `bq` CLI) runs
the full scenario set against it.

Scenario coverage enumerated in the
[architecture overview](overview.md) and the
[CHANGELOG](https://github.com/jjviscomi/bqemulator/blob/main/CHANGELOG.md).

## Tier 5 — Conformance (`tests/conformance/`)

Replays recorded baselines against the emulator with row-for-row,
type-aware tolerance. The corpus ships **1215 active fixtures** —
**1141 SQL + 48 HTTP + 26 gRPC** (plus 18 INFORMATION_SCHEMA
fixture stubs awaiting operator-side recording, exercised in the
meantime by Tier 3 integration tests). 13 documented XFAILs are
pinned as permanent design-decision divergences in
[`tests/conformance/divergences.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/conformance/divergences.py).

The corpus includes full TPC-H (22/22 queries) and a 59-of-99
TPC-DS subset. The remaining 40 TPC-DS queries are tracked in
[tpcds-expansion-plan.md](contributing/tpcds-expansion-plan.md) —
that document records the missing query list (numerical order,
with complexity hints), the per-query authoring recipe, BigQuery
adaptation patterns, cost guardrails, and the open questions to
resolve before bulk recording. Replay is **per-PR** in CI (no
external credentials needed — the baselines are committed).
Re-recording is the deliberate operator action `make
record-conformance`, gated on `GOOGLE_APPLICATION_CREDENTIALS` +
`BQEMU_CONFORMANCE_PROJECT`.

## Tier 6 — Performance (`tests/perf/`)

`pytest-benchmark`. Five scenario files / 19 benchmarks: cold-start
(containerized), query latency (TPC-H SF0.01 Q1/Q3/Q5/Q6/Q10), Storage
Read Arrow throughput, `insertAll` throughput (batches of
1 / 10 / 100 / 1000), Storage Write throughput (4 stream types x 2
payload formats). Per-arch baselines at
`tests/perf/baselines/<arch>.json` (one per `linux-x86_64` /
`linux-arm64` / `darwin-arm64`); CI compares each run against the
committed baseline with `--benchmark-compare-fail=median:10%`. A
regression > 10% on **any single benchmark** fails the release. The
design contract is locked in
[ADR 0025](../adr/0025-perf-tier-design-contract.md).

## Tier 7 — Chaos (`tests/chaos/`)

**Deliberately disruptive.** Each test injects a real failure (resource
exhaustion, crash, network drop, race) and asserts the emulator either
preserves invariants or fails in a clean, documented way. Chaos runs
**manual-only** in CI via the
[`chaos.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/chaos.yml)
workflow (`workflow_dispatch`); the cadence-migration decision is
deferred per the deferred-cadence CI policy chaos / perf / mutation /
differential / fuzz all share. Locally: `make test-chaos`.

Five categories, one file each under `tests/chaos/`:

| Category | File | What it injects |
|---|---|---|
| Concurrency | `test_concurrency.py` | 100+ readers on stale MVs; retry storms (1000 threads); mixed read/write contention with time-travel |
| Resource | `test_resource_exhaustion.py` | Disk-full during EXPORT/COPY; memory cap during Arrow batch; FD exhaustion under many gRPC streams |
| Crash | `test_crash_recovery.py` | `kill -9` mid-AppendRows, mid-DDL; gRPC client cancellation mid-stream |
| Storage | `test_storage_failures.py` | Two emulators racing same `data_dir`; spatial extension missing; migration rollback |
| Network | `test_network_failures.py` | gRPC server-side stream cancellation; slow client back-pressure on ReadRows; connection drop during BatchCommit |

**Rules**:

- Flaky chaos tests are not tolerated. Every scenario must be
  deterministic given the test seed.
- Chaos tests **must** assert one of: (a) invariant preserved despite
  fault (e.g., offset monotonicity under retry storms), or (b) clean
  documented failure (e.g., `InternalError` with row identity), or
  (c) graceful degradation (e.g., snapshot-level isolation under
  concurrent writers).
- Chaos tests use `pytest-timeout` to cap runaway scenarios at 60s
  each.
- The chaos tier ships **18 passing scenarios** plus 1 documented
  environment-conditional skip (the spatial-extension-offline
  scenario; its deterministic unit-tier counterpart is in
  [`tests/unit/storage/test_engine_spatial.py`](https://github.com/jjviscomi/bqemulator/blob/main/tests/unit/storage/test_engine_spatial.py)).
  Catalog-hydration robustness and concurrent-writer contention
  are also covered inline elsewhere. The design contract for the
  tier is documented in
  [ADR 0021](../adr/0021-chaos-tier-design-contract.md).

## Differential tier (Tier 5 sibling)

`pytest tests/conformance/test_corpus_row_order_perturbed.py
-m differential`. Re-runs the conformance corpus with every
``INSERT … VALUES (…), (…), …`` tuple list **reversed** in
``setup.sql`` and asserts the emulator's output still matches the
recorded baseline under canonical row sorting. Catches the
**fixture-specific-shortcut** bug class: emulator logic that
accidentally happens to be correct on the recorded data and wrong
on permuted data (e.g., a ``LIMIT N`` shortcut that picks the
first row in DuckDB's storage order, which happens to match
BigQuery's storage order on the recorded dataset).

The tier exercises ~77 of the ~1141 SQL fixtures; the remaining
fixtures are skipped because their queries use BigQuery-documented
order-sensitive contracts (``ORDER BY``, ``LIMIT``, ``ARRAY_AGG``
/ ``STRING_AGG`` / window functions without explicit OVER ORDER
BY, ``TABLESAMPLE``). The skip rules are conservative — false-
positive divergences would drown the genuine shortcut-bug signal.

v1.0 ships **row-order perturbation only** (mode A). Mode B
(value-shift) and mode C (schema-reorder) require operator
BigQuery time to re-record perturbed-sibling fixtures and are
deferred to v1.0.x.

The differential workflow at
[`differential.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/differential.yml)
ships as ``workflow_dispatch`` only — the gating / cadence
decision (per-PR vs nightly vs release-gate) is deferred until
post-repo-setup when there is real CI traffic to measure runtime
/ flakiness / runner-cost trade-offs. The design contract — the
perturbation taxonomy (A / B / C), eligibility rules, comparator
behaviour, skip-list policy, and triage protocol on divergence —
is locked in
[ADR 0028](../adr/0028-differential-tier-design-contract.md).

The differential tier is intentionally **not** numbered into the
seven-tier pyramid above — it sits alongside Tier 6 (performance),
Tier 7 (chaos), and the mutation tier as a comparison gate whose
unit of analysis is a delta from a stored baseline (here: the
recorded ``expected.json``).

## Fuzz tier (Tier 2 sibling)

`python fuzz/fuzz_sql_translator.py …` / `…fuzz_dyn_proto.py` /
`…fuzz_arrow_bridge.py` — three Atheris coverage-guided harnesses
covering the project's three highest-attack-surface translator-
input boundaries:

| Harness | Surface | Entry point |
|---|---|---|
| [`fuzz_sql_translator.py`](https://github.com/jjviscomi/bqemulator/blob/main/fuzz/fuzz_sql_translator.py) | SQL translator | `SQLTranslator.translate` |
| [`fuzz_dyn_proto.py`](https://github.com/jjviscomi/bqemulator/blob/main/fuzz/fuzz_dyn_proto.py) | Storage Write API dynamic protobuf | `ProtoRowDecoder.decode` |
| [`fuzz_arrow_bridge.py`](https://github.com/jjviscomi/bqemulator/blob/main/fuzz/fuzz_arrow_bridge.py) | Arrow REST-JSON bridge + Arrow IPC deserialiser | `bq_rows_to_arrow` + `deserialize_arrow_rows` |

Each harness's:func:`TestOneInput` asserts the **baseline
contract**: any uncaught Python exception that is NOT a
documented domain error (or the parser-specific upstream error
class — ``DecodeError`` for protobuf, ``ArrowInvalid`` /
``ValueError`` for Arrow) is a bug. The fuzz tier is the only
tier in the project that exercises translator inputs nobody
hand-authored — the long tail of malformed UTF-8, unbalanced
syntactic tokens, oversized arrays, mis-typed protobuf fields,
and Arrow buffers with bogus length prefixes that a
human-authored fixture catalogue cannot enumerate.

Tool choice is **Atheris 3.0.0** (Google's CPython binding for
libFuzzer). Atheris supports Python 3.11/3.12/3.13 — matching the
[`ci.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/ci.yml)
per-PR matrix. The dev-box's Python 3.14 is NOT supported yet;
`make test-fuzz` prints a remediation message routing the
operator to a 3.13 venv.

The committed [`fuzz/corpus/`](https://github.com/jjviscomi/bqemulator/blob/main/fuzz/corpus/)
tree carries one seed per major translator branch (canonical SQL
shapes; a representative populated proto wire-bytes blob; a
valid Arrow IPC stream + the documented zero-row / garbage
shapes). Atheris's coverage-guided mutation expands from there.

The
[`fuzz.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/fuzz.yml)
workflow ships with ``workflow_dispatch`` only — the gating /
cadence decision (per-PR vs nightly vs release-gate vs
stays-manual) is deferred until post-repo-setup when there is
real CI traffic to measure runtime / flakiness / runner-cost
trade-offs. Each harness runs for 10 minutes in CI (30 minutes
total wall time); `make test-fuzz` runs for 60 seconds per
harness locally. The design contract — surface enumeration, tool
choice rationale, baseline-contract invariant, per-harness time
budget, no-skip-list discipline, and triage protocol on crash —
is locked in
[ADR 0031](../adr/0031-fuzz-tier-design-contract.md).

The fuzz tier is intentionally **not** numbered into the
seven-tier pyramid above — it sits alongside the differential
(Tier 5 sibling) and mutation tiers as a comparison gate whose
sampling discipline (coverage-guided libFuzzer mutation) differs
from the pyramid tiers' "one invariant per test" structure. It
shares the property-tier (Tier 2) discipline rather than asserting
fresh invariants.

## Mutation testing

`mutmut` runs **manual-only** (`workflow_dispatch`) in the
[mutation.yml](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/mutation.yml)
workflow against a curated pilot scope (nine pure-domain modules,
~1 800 LOC). The cadence migration (per-PR scoped vs nightly vs
release-gate) is deferred per the deferred-cadence CI policy
chaos / perf / mutation / differential / fuzz all share. The
committed baseline lives at
[`tests/mutation/baseline.json`](https://github.com/jjviscomi/bqemulator/blob/main/tests/mutation/baseline.json);
the regression gate fails the release when the live mutation score
drops more than 2 percentage points below it. Re-baselining is the
operator action ``make test-mutation-baseline``.

The mutation tier is intentionally **not** numbered into the
seven-tier pyramid above — it sits alongside Tier 6 (performance)
as a comparison gate whose unit of analysis is a delta from a
stored baseline. The design contract — pilot scope, score formula
(killed / (killed + survived), excluding ``no_tests`` /
``skipped`` / ``timeout`` / ``suspicious``), cadence, and the
v1.0.x scope-expansion plan — is locked in
[ADR 0026](../adr/0026-mutation-tier-design-contract.md).

## Determinism

- `Clock` protocol (default `SystemClock`; tests inject `FrozenClock`).
- `IdGenerator` protocol (default UUID4; tests inject deterministic sequences).
- Fixed random seeds; Hypothesis uses explicit per-test seeds.

## Never do

- Mock DuckDB in integration or e2e tests.
- Skip a client language for an e2e scenario.
- Merge a PR that drops coverage below 90%.
- Add a test that sometimes fails ("flaky") without an issue marking it
  to be fixed.
