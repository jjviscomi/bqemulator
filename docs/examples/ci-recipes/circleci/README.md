# CircleCI recipe for `bqemulator`

Reference `.circleci/config.yml` showing how to run `bqemulator` as a
secondary Docker container in the same job. CircleCI exposes the
secondary at `localhost` (it shares the network namespace with the
primary), so tests connect to `http://localhost:9050`.

Pairs with [CI/CD patterns guide](../../../guides/ci-cd-patterns.md).

## Layout

```
config.yml                — the canonical pipeline your repo would copy
meta-test.sh              — verifies the YAML parses and patterns match
```

## Run

```bash
make test
```

## Adapting the recipe

Copy `config.yml` to `.circleci/config.yml` and edit:

- The pinned `bqemulator` image tag.
- The `run:` step under `test:` — replace with your test command.

The recipe assumes the [machine executor](https://circleci.com/docs/configuration-reference/#machine-executor-linux)
or a Docker secondary; both are shown in the file.
