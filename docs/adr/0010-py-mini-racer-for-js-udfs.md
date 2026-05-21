# ADR 0010: mini-racer (embedded V8) for JavaScript UDFs

- **Status**: Accepted

## Context

BigQuery supports JavaScript UDFs. DuckDB does not. Options:

1. **mini-racer** — PyO3/PyPI-distributed V8 bindings; actively
   maintained.
2. **Node.js subprocess** — use IPC with a standalone node process.
3. **Document as unsupported** — rejected by the no-deferral principle.

## Decision

Embed V8 via `mini-racer` as an optional extra (`bqemulator[udf-js]`).
In-process, low-latency invocation. Arguments serialize through JSON
(matching BigQuery's UDF calling convention).

Sandboxing is enforced via:

- Configurable CPU time per invocation (default: 5 seconds).
- Configurable memory cap per context (default: 256 MiB).
- No network, no filesystem — mini-racer exposes neither.

## Consequences

- **Positive**: in-process, fast, no IPC.
- **Positive**: matches BigQuery's JS UDF semantics (type coercion via
  JSON) well.
- **Negative**: V8 is a large binary; optional extra keeps base install
  small.
- **Negative**: sandbox escape surface requires ongoing attention;
  addressed by resource limits, fuzzing, and a documented scope in
  `SECURITY.md`.
