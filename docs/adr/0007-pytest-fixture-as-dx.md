# ADR 0007: pytest fixture as the primary developer experience

- **Status**: Accepted

## Context

The primary user for bqemulator is a developer writing tests. Forcing
users to start a separate process, wire an env var, and manage lifecycle
is friction.

## Decision

Ship a pytest plugin via the `pytest11` entry point in `pyproject.toml`.
`pip install bqemulator` makes `bqemu_server`, `bqemu_endpoint`, and
`bqemu_client` fixtures available automatically with zero `conftest.py`
setup.

The fixture runs the emulator in-process on a background thread with its
own asyncio event loop (see `bqemulator.testing._thread_runner`), so
synchronous test code and the Google Python client can use it naturally.

## Consequences

- **Positive**: friction-free DX; users `pip install` and start writing
  tests.
- **Positive**: ephemeral mode means every test session starts clean.
- **Negative**: threading the event loop adds some complexity. Encapsulated
  in the plugin; users never see it.
