# ADR 0044: Adopt starlette 1.x (security) without adopting httpx2

- **Status**: Accepted

## Context

`pip-audit` flagged four advisories against `starlette` 0.52.1, the ASGI
foundation under FastAPI that serves the emulator's REST API:

- `CVE-2026-48818` (fixed in starlette 1.1.0)
- `CVE-2026-48817` (fixed in starlette 1.1.0)
- `CVE-2026-54282` (fixed in starlette 1.3.0)
- `CVE-2026-54283` (fixed in starlette 1.3.1)

Every fix ships in the starlette 1.x line. The dependency had been held at
`starlette>=0.46,<1.0`. The ceiling pin's stated reason was a feared
`httpx` to `httpx2` migration in `starlette.testclient` (which FastAPI's
`TestClient` re-exports), deferred to "its own PR." A prior advisory,
`PYSEC-2026-161`, was carried in `.pip-audit-ignore` for the same reason: its
only fix was in the capped 1.x line.

Empirically, the feared migration is narrower than the pin assumed:

- `fastapi` 0.137 (the current floor's resolution) already accepts
  `starlette` 1.3.1 and resolves with plain `httpx` 0.28; there is no runtime
  `httpx2` requirement.
- `starlette.testclient` in 1.x prefers an optional `httpx2` package and, when
  it is absent, falls back to plain `httpx` while emitting a
  `StarletteDeprecationWarning`. The only concrete breakage was that the
  project's warnings-as-errors test policy turned that import-time deprecation
  into a collection error for every `TestClient`-based test.

The security exposure is real (a runtime dependency), though the emulator's
threat model is a local, trusted developer or CI environment rather than an
internet-facing service.

## Decisions

### 1. Floor `starlette>=1.3.1`

1.3.1 is the lowest release that clears all four advisories plus the
previously-ignored `PYSEC-2026-161`. `fastapi` and `httpx` are left at their
existing floors; no FastAPI bump is required.

### 2. Keep plain `httpx`; do not adopt `httpx2`

`httpx2` is a separate, distinct HTTP-client package whose provenance and
maintenance are not yet vetted for this dependency graph, and the security fix
that motivated this change lives in `starlette` itself, not in the test
client. Plain `httpx` remains fully supported by `starlette.testclient` as the
fallback, so the test client keeps working unchanged.

### 3. Scope-ignore the test-client deprecation warning

A single `filterwarnings` entry in `pyproject.toml` ignores exactly the
"Using `httpx` with `starlette.testclient` is deprecated" message
(`starlette.exceptions.StarletteDeprecationWarning`). The ignore is matched by
message so any other starlette deprecation still fails the warnings-as-errors
gate. Adopting `httpx2`, or revisiting if a future starlette release removes
the plain-`httpx` fallback, is left as follow-up.

### 4. Drop the obsolete `PYSEC-2026-161` ignore

That entry existed only because the fix was gated behind the `<1.0` cap. With
the floor at 1.3.1 the advisory is genuinely patched, so the ignore is removed
rather than carried as dead configuration.

## Consequences

- `pip-audit` reports no known vulnerabilities; the four CVEs and
  `PYSEC-2026-161` are resolved by the upgrade, not suppressed.
- The long-standing `starlette<1.0` deferral and its tracking comment are
  retired.
- One narrowly-scoped warning ignore is added to the test configuration; the
  rest of the warnings-as-errors discipline is unchanged.
- A future move to `httpx2` (or a forced move, if starlette drops the
  plain-`httpx` fallback) is a self-contained follow-up that does not block the
  security fix.
