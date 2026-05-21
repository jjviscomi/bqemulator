# ADR 0005: FastAPI + grpc.aio in a single process, single event loop

- **Status**: Accepted

## Context

Should REST and gRPC run in separate processes or a single process?
Separate processes simplify each surface at the cost of IPC for the
shared catalog + DuckDB state.

## Decision

Single process, single asyncio event loop, both servers bound on separate
ports. FastAPI (ASGI via uvicorn) serves REST; `grpc.aio` serves gRPC.
Shared state (DuckDB connection, catalog, event bus) is held on an
`AppContext` constructed by the composition root and injected into both
adapters.

## Consequences

- **Positive**: no IPC; shared in-memory catalog; trivial transactional
  coordination.
- **Positive**: one binary, one container, one health endpoint.
- **Negative**: the REST event loop and gRPC event loop share CPU time.
  Adequate for emulator workloads; DuckDB releases the GIL for the heavy
  lifting.
