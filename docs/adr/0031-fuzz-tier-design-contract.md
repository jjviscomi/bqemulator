# ADR 0031: Fuzz-tier design contract

- **Status**: Accepted

## Context

The
[testing-strategy](../architecture/testing-strategy.md) document
defines a seven-tier pyramid (unit / property / integration / e2e /
conformance / perf / chaos) plus three ancillary comparison gates
running alongside it (mutation per [ADR 0026](0026-mutation-tier-design-contract.md),
differential per [ADR 0028](0028-differential-tier-design-contract.md),
and now fuzz). Every pyramid tier consumes fixtures and inputs that
a human author wrote — even the property tier's Hypothesis
strategies, although broader than hand-rolled examples, are bounded
by the strategy author's imagination.

The translator-input surface is the project's outermost boundary
for untrusted user input. Three modules sit on that boundary:

* :mod:`bqemulator.sql.translator` — accepts arbitrary BigQuery
  GoogleSQL strings from REST and gRPC clients.
* :mod:`bqemulator.streaming.proto_deserializer` — accepts arbitrary
  protobuf wire bytes from the Storage Write API's ``AppendRows``
  bidi stream.
* :mod:`bqemulator.storage.arrow_bridge` +
  :mod:`bqemulator.streaming.arrow_deserializer` — accept arbitrary
  REST-JSON row payloads and Arrow IPC wire bytes from clients.

Each module currently catches the documented error families
(``sqlglot.ParseError`` / ``DecodeError`` / ``ArrowInvalid``) and
maps them to clean domain errors. But the *fuzz target* is not the
documented error families — it's the **uncaught Python exception**
that a translator-input crash would surface to a peering client.
The current test pyramid (unit + property + integration +
conformance + the P8.f differential pass) catches handcrafted
inputs; fuzz catches the inputs nobody hand-authored — malformed
UTF-8, unbalanced syntactic tokens, oversized arrays, mis-typed
protobuf fields, Arrow buffers with bogus length prefixes.

The conformance-depth audit elevated this from
"defer-acceptable to v1.0.x" to a **mandatory v1.0.0 must-have**.
The audit's rationale: fuzz is the **only tier** that exercises
translator inputs nobody hand-authored. Every preceding tier in
the pyramid is bounded by author imagination; the diminishing-
returns curve from adding more hand-authored fixtures (already
visible after the P8.x cluster shipped 60+90 surface and TPC
fixtures) does not match the curve a coverage-guided fuzzer
provides on truly novel inputs.

This ADR captures the v1.0 fuzz contract: what's fuzzed, what's
not, with which tool, at what cadence, and what the "any uncaught
non-domain exception is a bug" invariant means in practice.

## Decisions

### 1. The fuzz tier is a property-tier (Tier 2) sibling, not a new pyramid number

The
[testing-strategy](../architecture/testing-strategy.md) document
defines a seven-tier pyramid. The fuzz tier asserts the same kind
of contract the property tier asserts — "for all inputs ``x``,
``translate(x)`` returns ``Result.Ok | Result.Err``" — only with a
coverage-guided sampler instead of Hypothesis's strategy-bounded
sampler. It is conceptually a Tier 2 sibling rather than a new
pyramid step.

This mirrors the chaos tier ([ADR 0021](0021-chaos-tier-design-contract.md)),
the perf tier ([ADR 0025](0025-perf-tier-design-contract.md)), the
mutation tier ([ADR 0026](0026-mutation-tier-design-contract.md)),
and the differential tier ([ADR 0028](0028-differential-tier-design-contract.md)) —
each is a separately-gated tier with its own ADR, none are
numbered into the pyramid because their unit of analysis (a
delta-from-baseline, or a coverage-guided sample) differs from the
pyramid tiers' "assert one invariant per test".

### 2. Three harnesses; v1.0 ships Atheris 3.0.0

Three harnesses live under [`fuzz/`](https://github.com/jjviscomi/bqemulator/blob/main/fuzz/),
one per translator-input boundary:

| Harness | Surface | Entry point |
|---|---|---|
| [`fuzz_sql_translator.py`](https://github.com/jjviscomi/bqemulator/blob/main/fuzz/fuzz_sql_translator.py) | SQL translator |:meth:`bqemulator.sql.translator.SQLTranslator.translate` |
| [`fuzz_dyn_proto.py`](https://github.com/jjviscomi/bqemulator/blob/main/fuzz/fuzz_dyn_proto.py) | Storage Write API dynamic protobuf |:meth:`bqemulator.streaming.proto_deserializer.ProtoRowDecoder.decode` |
| [`fuzz_arrow_bridge.py`](https://github.com/jjviscomi/bqemulator/blob/main/fuzz/fuzz_arrow_bridge.py) | Arrow REST-JSON bridge + Arrow IPC deserialiser |:func:`bqemulator.storage.arrow_bridge.bq_rows_to_arrow` +:func:`bqemulator.streaming.arrow_deserializer.deserialize_arrow_rows` |

**Tool choice: Atheris 3.0.0**, the Google-maintained Python
binding for libFuzzer's coverage-guided engine. Atheris 3.0.0 was
released and supports Python 3.11/3.12/3.13 — the full
[`ci.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/ci.yml)
per-PR matrix.

Alternatives weighed:

* **Hypothesis with `@given(text())`** — already in the project's
  test stack (Tier 2). Generates random inputs, but the search is
  strategy-bounded; there is no coverage signal driving the
  mutation engine toward unexplored branches. Effective for
  property-tier invariants on a *known* input domain; ineffective
  at finding inputs nobody thought of.
* **python-afl** — older, AFL-style bridge. Maintenance is thin
  and the project hasn't kept pace with CPython's faster release
  cadence. The cost of integration outweighs the benefit over
  Atheris for a v1.0 must-have.
* **boofuzz** — protocol/wire-format fuzzer. Optimised for
  network-protocol fuzzing (every byte of every PDU type); the
  surface we're fuzzing is *function-level*, not protocol-level.
  Boofuzz would over-rotate on the proto-deserialiser harness and
  under-cover the SQL translator.

Atheris's main caveat is the **dev-box Python-version gap**: the
maintainer's asdf-managed default Python is 3.14 (via the user-home
`~/.tool-versions`), and Atheris does NOT yet support 3.14. The
project's own `.tool-versions` pins only Java — Python is left to
the operator's environment. Local-run instructions in
[`make test-fuzz`](https://github.com/jjviscomi/bqemulator/blob/main/Makefile)
print a remediation message routing the operator to a 3.13 venv.
The CI workflow's `setup-python` step pins 3.13 explicitly. When
Atheris ships 3.14 support the workflow change is a single-line
bump.

### 3. The baseline contract: any uncaught non-domain exception is a bug

Each harness's :func:`TestOneInput` exercises its entry point and
catches:

* :class:`bqemulator.domain.errors.DomainError` (any subclass) —
  every project module's documented error envelope.
* The harness-specific upstream parser error
  (:class:`google.protobuf.message.DecodeError` for the proto
  harness; :class:`ValueError` and :class:`pyarrow.ArrowInvalid`
  for the Arrow harness).

Anything else escaping the entry point — ``TypeError``,
``IndexError``, ``UnicodeDecodeError``, ``RecursionError``,
``MemoryError`` from bogus length prefixes, ``KeyError`` from an
unexpected AST shape — is a bug. libFuzzer surfaces it as a crash
and writes the reproducer input to the configured
``artifact_prefix`` directory; the manual-dispatch workflow uploads
that directory as a CI artefact for operator triage.

The SQL translator harness adds one extra assertion:
``SQLTranslator.translate`` must return an:class:`Ok` or
:class:`Err`. Returning anything else (e.g. a raw string, or
``None``) would silently break every caller; the harness raises
:class:`AssertionError` to surface it as a crash.

### 4. No skip-list; every crash is a bug or a contract clarification

The differential tier ships an empty skip-list ([ADR 0028](0028-differential-tier-design-contract.md) §5)
and a documented escape valve. The fuzz tier ships **no skip-list
at all** and no escape valve. The reasoning: differential's
perturbation can legitimately surface "this query is documented as
order-sensitive, perturbation is meaningless" — a class of false
positive that requires a per-fixture rationale. Fuzz has no
equivalent — every crash is either:

1. **Emulator bug.** Fix inline; add a regression test in the
   unit tier (canonical-lock pattern from [ADR 0021](0021-chaos-tier-design-contract.md) §6).
   The fuzz tier is the *detection* mechanism, not the regression
   test — committing the raw libFuzzer reproducer input would
   leave a binary blob in git with opaque hashing properties.
2. **Reproducer is invalid input outside the documented contract.**
   The fix is to assert a domain error envelope, not to skip-list
   the input. The harness changes from "raises a bare exception"
   to "raises ``InvalidQueryError`` / ``ValidationError`` /
   etc.", and the regression test pins the new envelope.

There is no third option. A v1.0 fuzz divergence cannot be
"deferred to v1.0.x" because the divergence itself is the bug —
the contract violation is happening *now*, in shipped code.

### 5. Per-harness time budget: 10 minutes in CI, 1 minute locally

The CI workflow runs each harness with
``-max_total_time=600`` (10 minutes; 30 minutes total across the
three harnesses). The Makefile's local target uses
``-max_total_time=60`` (1 minute per harness; 3 minutes total).

The bound is empirical — coverage-guided fuzzing has
diminishing-returns characteristics. The first ~60 seconds
exhausts the seed corpus's reachable branches; the next ~5
minutes finds the shallow bugs the seed corpus didn't reach; past
that point the find rate approaches asymptotic. 10 minutes per
harness is the v1.0 sweet spot — long enough to find shallow
crashes, short enough that a manual-dispatch run is feasible (~30
minutes wall time end-to-end) and that the post-repo-setup
cadence evaluation has a realistic per-run cost to weigh against
runner economics.

The budget is configurable per-invocation via libFuzzer's CLI:
the workflow's positional argument can be overridden by adding
``-max_total_time=N`` ahead of it for ad-hoc longer runs.

### 6. Manual-only CI cadence; gating decision deferred

The
[`fuzz.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/fuzz.yml)
workflow ships with ``workflow_dispatch`` only — no ``schedule:``,
no ``push:``, no ``pull_request:``. This matches the policy
chaos.yml, perf.yml, mutation.yml, and differential.yml adopted in
the P8.e / P8.f sessions: every ancillary tier ships as a
manual-only workflow until empirical data justifies a stricter
cadence.

The gating decision (per-PR vs nightly vs release-gate vs
stays-manual) is **explicitly deferred** until after the project's
GitHub repo is set up and there is real PR traffic to characterise
runtime, flakiness, and runner-cost trade-offs against. Candidate
future triggers for evaluation:

- **Nightly schedule** — the natural fit. Fuzzing is the canonical
  "find crashes between releases" tier, and the 30-minute total
  budget makes per-PR unattractive on cost alone. A nightly job
  surfaces crashes early enough to actionably triage them within
  the same dev cycle.
- **Release-gate-only** — invoked as a precondition of the
  ``release/`` branch's gate chain. Cheapest cadence; weakest
  coverage; effectively gates v1.0.1 against new crashes.
- **Per-PR gate** — listed for completeness. The 30-minute
  per-job budget makes this almost certainly too slow even with
  aggressive parallelism; only worth re-evaluating if the
  per-harness budget can be cut to 1-2 minutes without losing
  signal.

Until the cadence migration lands, the workflow is invoked
manually via the GitHub Actions "Run workflow" button (or
``gh workflow run fuzz``).

### 7. No auto-filed tracking issues; operator triage via uploaded artefacts

The workflow uploads the entire ``fuzz-artifacts/`` tree (each
harness has its own subdirectory) as a CI artefact. libFuzzer
writes reproducer inputs as ``crash-<sha1>``, ``leak-<sha1>``,
``timeout-<sha1>``, ``oom-<sha1>`` files inside the configured
``-artifact_prefix=`` directory. The operator downloads the
artefact, identifies the crash class, and either:

* Reproduces locally with
  ``python fuzz/fuzz_sql_translator.py crash-<sha1>`` (libFuzzer
  re-runs the single reproducer when invoked with a file argument).
* Adds a canonical-lock regression test in the unit tier and
  closes the bug inline.

The decision to **not** auto-file tracking issues mirrors the
artefact-upload-and-operator-triage pattern that chaos / perf /
mutation / differential all share. Auto-filing was considered and
deferred to v1.0.x scope because:

* The triage step (classify as "emulator bug" vs "contract
  clarification") requires human judgement; an auto-filed issue
  with a binary reproducer attachment would be lower-signal than
  the operator's direct read of the artefact.
* The infrastructure cost (gh CLI in CI, deduplication logic so
  re-runs don't spam, signed-issue policies) is non-trivial for
  v1.0 ship-criteria scope.

## Consequences

- **Positive.** The translator-input crash class is now testable
  by a coverage-guided sampler. The v1.0 baseline establishes
  "no input within the seed corpus's reachable mutation
  neighbourhood crashes the translator / proto decoder / Arrow
  bridge", and any future regression surfaces in the
  manual-dispatch run.

- **Positive.** All three high-attack-surface boundaries are
  covered by a single tier in a single PR. v1.0 ships with a
  uniform crash-detection contract across the SQL parser, the
  proto wire format, and the Arrow IPC format — no boundary is
  asymmetrically more tested than another.

- **Positive.** The manual-only cadence mirrors chaos / perf /
  mutation / differential, so the operator workflow is consistent
  across all five ancillary tiers. A new contributor who knows
  how to invoke differential knows how to invoke fuzz without
  re-reading the docs.

- **Positive.** The "no skip-list" discipline forces the crash-
  triage decision tree to converge on either an inline fix or a
  contract clarification. There's no v1.0.x backlog of
  "tolerated crash" cases that have to be re-evaluated later.

- **Negative.** v1.0 ships the harnesses but does NOT gate on
  them in the per-PR chain. A crash class that the fuzz tier
  catches will sit in ``main`` until an operator manually
  dispatches the workflow. The mitigation is the per-PR
  conformance + property + unit gates catching anything reachable
  from hand-authored fixtures; the fuzz tier catches the residual
  long tail. The post-repo-setup cadence evaluation (probably
  a nightly migration) will close this gap once empirical data
  exists.

- **Negative.** Atheris's dev-box-incompatible Python-version
  story (3.13 required; dev-box is 3.14) adds friction to the
  local-run path. The mitigation is the
  [`make test-fuzz`](https://github.com/jjviscomi/bqemulator/blob/main/Makefile)
  remediation message; the post-mitigation cost is a one-time
  ``asdf install python 3.13.<latest>`` per contributor and a
  ``.venv-fuzz`` directory in the working tree. The version-gap
  closes automatically when Atheris ships 3.14 support — at
  which point the Makefile remediation message can be removed
  and the workflow's ``python-version: "3.13"`` pin can advance.

- **Negative.** The 30-minute total CI budget makes per-PR
  unattractive on runner cost. Even with aggressive parallelism
  the three harnesses can't share an instrumented Python
  interpreter (each ``Setup()`` builds libFuzzer's coverage map
  independently), so the total cost is roughly additive.
  Re-evaluating the cadence is part of the post-repo-setup
  evaluation; reducing the per-harness budget below 10 minutes
  trades coverage depth for shorter wall time and would need its
  own ADR amendment.

- **Negative.** The harnesses' assertion that
  ``DomainError`` is the only acceptable escape envelope is a
  forward-compat promise. If the translator pipeline ever needs
  to surface a non-domain error (e.g. a structlog logging
  failure, a runtime configuration error) the harness must be
  updated alongside the surface change — failing to do so would
  create a false-positive crash. The mitigation is the principle
  that domain-error escape is the documented contract; any new
  exception type that escapes is a code-smell anyway.

## Implementation notes

- The ``fuzz`` pytest marker is **not** registered. The harnesses
  run standalone (Atheris's recommended pattern); pytest does not
  collect them. The
  [`pyproject.toml`](https://github.com/jjviscomi/bqemulator/blob/main/pyproject.toml)
  markers list stays at its 9-marker set (unit / integration /
  e2e / conformance / property / perf / chaos / differential /
  slow) unless a future harness rewrite moves the entry points
  through pytest fixtures.
- Atheris is intentionally **not** a declared
  [`pyproject.toml`](https://github.com/jjviscomi/bqemulator/blob/main/pyproject.toml)
  dev dependency. The CPython-build-tied native extension would
  break ``pip install -e ".[dev]"`` on the dev-box's 3.14. The
  CI workflow installs it explicitly in the fuzz-only step; the
  local-run Makefile target imports it as a feasibility probe and
  prints the remediation message when it fails.
- The committed seed corpus under
  [`fuzz/corpus/`](https://github.com/jjviscomi/bqemulator/blob/main/fuzz/corpus/)
  is *intentionally small*. Each subdirectory holds ~5-10 seeds
  that exercise the major branches of each entry point; the
  coverage-guided mutation engine expands from there. The full
  fuzz corpus is NOT committed because libFuzzer's mutation
  outputs include random-content blobs whose hashing properties
  would bloat the git history without adding signal.
- The Atheris-instrumented imports are scoped to the modules
  whose branches we want libFuzzer's coverage map to reach. The
  domain-error hierarchy and the
  :class:`bqemulator.domain.result.Result` types are imported
  *outside* the instrumentation block — they're small leaf
  modules the coverage signal does not depend on, and
  instrumenting them adds noise to libFuzzer's branch-coverage
  decisions.

## References

- [Tier 2 in the testing-strategy doc](../architecture/testing-strategy.md)
- v1-confidence-plan workstream P3.c
  — this ADR closes the workstream
- ADR [0021](0021-chaos-tier-design-contract.md) — Tier 7 (chaos)
  design contract, the precedent for an ancillary-tier ADR
- ADR [0025](0025-perf-tier-design-contract.md) — Tier 6 (perf)
  design contract, the precedent for a manual-only workflow with
  deliberate operator action
- ADR [0026](0026-mutation-tier-design-contract.md) — mutation-tier
  design contract, the precedent for a comparison gate alongside
  the pyramid
- ADR [0028](0028-differential-tier-design-contract.md) —
  differential-tier design contract, the precedent for the
  "deferred-cadence policy" the fuzz workflow inherits
- [`fuzz/`](https://github.com/jjviscomi/bqemulator/blob/main/fuzz/)
  — the three harnesses + seed corpora
- [`.github/workflows/fuzz.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/fuzz.yml)
  — the manual-dispatch workflow
- [Atheris on GitHub](https://github.com/google/atheris) — upstream
  documentation for the chosen fuzzer
