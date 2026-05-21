# ADR 0004: Hexagonal (ports and adapters) architecture

- **Status**: Accepted

## Context

The emulator has two external protocols (REST, gRPC) and one data engine
(DuckDB). The temptation is to put SQL translation inside the REST
handler, which couples HTTP semantics to domain logic and makes testing
the domain require a web server.

## Decision

Hexagonal architecture:

- **Domain core** (`bqemulator.domain`, `.catalog`, `.storage`, `.sql`,
  `.jobs`, `.streaming`, `.scripting`, `.udf`, `.versioning`, `.types`,
  `.row_access`, `.views`, `.external_tables`, `.transactions`) imports
  no framework code. Pure types, interfaces, and business logic.
- **Adapters** (`bqemulator.api`, `.grpc_api`) translate external protocols
  to domain operations.
- **Composition root** (`bqemulator.server`) is the sole place top-level
  objects are constructed and wired together.

## Consequences

- **Positive**: domain is unit-testable without spinning up a server.
- **Positive**: we could add a CLI frontend, a TCP binary protocol, or a
  WebSocket surface without touching business logic.
- **Positive**: clear import direction — lint rule enforces
  `src/bqemulator/domain/**` cannot import from `src/bqemulator/api/**`.
- **Negative**: one extra indirection for simple CRUD operations.
  Acceptable; saves us many times over when features span layers.
