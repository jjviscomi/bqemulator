# GitLab CI recipe for `bqemulator`

Reference `.gitlab-ci.yml` showing how to run `bqemulator` as a
GitLab CI [service](https://docs.gitlab.com/ee/ci/services/) alongside
your test job. The emulator is reachable at `bqemulator:9050` (the
service name resolves via Docker's network DNS).

Pairs with [CI/CD patterns guide](../../../guides/ci-cd-patterns.md).

## Layout

```
gitlab-ci.yml             — the canonical pipeline your repo would copy
meta-test.sh              — verifies the YAML parses and patterns match
```

## Run

```bash
make test
```

## Adapting the recipe

Copy `gitlab-ci.yml` as `.gitlab-ci.yml` at your repo root and edit:

- The `BQEMU_IMAGE` variable.
- The `test:` job's `script:` — replace with your tests.

GitLab will scrape the service container's logs and expose the
emulator on its DNS hostname (`bqemulator`), so your tests connect to
`http://bqemulator:9050` (not `localhost`).
