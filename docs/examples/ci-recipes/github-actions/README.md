# GitHub Actions recipe for `bqemulator`

Reference workflow showing two canonical patterns:

1. **Service container** — start `bqemulator` once per job using the
   `services:` block. Best for fast, hermetic Python/Node/Go test
   suites that talk to a single emulator instance.

2. **Testcontainers-driven** — let your test harness start the
   emulator on demand. Best for Java/Scio/Beam suites where the
   test framework already manages container lifecycle.

Pairs with [CI/CD patterns guide](../../../guides/ci-cd-patterns.md).

## Layout

```
workflow.yml              — the canonical workflow your repo would copy
meta-test.sh              — verifies the YAML parses and the patterns
                            match what's documented
```

## Run

```bash
make test
```

`make test` runs `meta-test.sh`, which:

- Lints `workflow.yml` with `yamllint` (or just `python -c "import yaml; yaml.safe_load(...)"`
  fallback).
- Verifies the `services:` block names `bqemulator` exactly once.
- Verifies the documented health-check command appears.

## Adapting the recipe

Copy `workflow.yml` into your `.github/workflows/` directory and edit:

- `BQEMU_IMAGE` env var — pin to the version you want (e.g.
  `ghcr.io/jjviscomi/bqemulator:1.0.0`).
- The `test:` step — replace with your project's test command.
