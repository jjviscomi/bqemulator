# Dev environment

Requirements: Python 3.11+, Docker with buildx, `make`. For full e2e
runs: Node.js 20+, Go 1.22+, JDK 17+, Maven.

```bash
git clone https://github.com/jjviscomi/bqemulator
cd bqemulator
make dev-setup
```

`make dev-setup` installs the `dev` extras and configures pre-commit
hooks (including commit-msg for Conventional Commits).

## Typical inner loop

```bash
make test-unit          # <10s — run on save
make lint               # ruff + mypy --strict + bandit + ...
make test-integration   # in-process emulator + Python client
```

When touching user-visible behavior:

```bash
make docker-build       # builds ghcr.io/jjviscomi/bqemulator:dev
make test-e2e           # live container + all four client languages
```

Before opening a PR:

```bash
make verify             # full release-ready gate chain
```

## IDE

- PyCharm / VS Code: enable the Ruff and mypy extensions.
- Configure the interpreter to the `.venv` that `pip install -e.[dev]`
  created.

## Running a local emulator

```bash
make run   # or: bqemulator start --ephemeral
```
