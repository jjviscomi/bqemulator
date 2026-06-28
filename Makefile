# Makefile — canonical entry points for bqemulator development.
#
# Every target is documented. `make help` lists them.
# `make verify` runs the full release-ready gate chain.

.DEFAULT_GOAL := help
SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c

# Prefer the project virtualenv's tools when ``.venv`` exists, so every
# target runs against the pinned interpreter regardless of which Python
# is active on PATH (asdf, pyenv, system). Without this, bare ``pytest``
# / ``mypy`` resolve to whatever the shell points at, which drifts from
# the dependency pins and produces confusing failures: a stale starlette
# breaking pytest startup, or an editable install resolving to a
# different checkout's ``src``.
#
# These are explicit tool variables rather than a ``PATH`` export on
# purpose: macOS still ships GNU Make 3.81, whose ``export PATH :=
# …$(PATH)`` handling is unreliable (recipes intermittently fall back to
# the shell interpreter), so each tool is invoked by its absolute venv
# path directly. ``$(CURDIR)`` keeps it absolute so recipes that ``cd``
# into subdirectories still resolve it. ``VENV_BIN`` is empty when no
# ``.venv`` is present, so the bare names fall through to whatever Python
# is active; both ``make dev-setup`` and CI create ``.venv`` from the
# lockfile (ADR 0048), so locally and in CI these resolve to the pinned
# interpreter. Tools that are not Python packages (docker, go, node, npm,
# mvn, bq, typos, lychee, npx) are invoked by bare name and fall through
# to the system PATH.
VENV_BIN := $(if $(wildcard .venv/bin),$(CURDIR)/.venv/bin/,)
PYTHON := $(VENV_BIN)python3
PIP := $(PYTHON) -m pip
PYTEST := $(VENV_BIN)pytest
MYPY := $(VENV_BIN)mypy
RUFF := $(VENV_BIN)ruff
BANDIT := $(VENV_BIN)bandit
PIP_AUDIT := $(VENV_BIN)pip-audit
INTERROGATE := $(VENV_BIN)interrogate
XENON := $(VENV_BIN)xenon
DIFF_COVER := $(VENV_BIN)diff-cover
MKDOCS := $(VENV_BIN)mkdocs
PRE_COMMIT := $(VENV_BIN)pre-commit
UV := uv
DOCKER_IMAGE := ghcr.io/jjviscomi/bqemulator
DOCKER_TAG ?= dev
DOCKER_PLATFORMS ?= linux/amd64,linux/arm64

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

.PHONY: _require-uv
_require-uv:
	# Internal guard: dev-setup and lock drive ``uv``, so fail with an
	# actionable install hint rather than a bare "command not found" on a
	# fresh machine. One backslash-joined line so it behaves identically
	# whether each recipe line gets its own shell (GNU Make 3.81 on macOS,
	# no ``.ONESHELL``) or the recipe runs as one script (Make 4.x on Linux).
	@command -v $(UV) >/dev/null 2>&1 || { \
	  printf '%s\n' \
	    "Error: '$(UV)' is required but was not found on PATH." \
	    "Install it, then re-run this target:" \
	    "  pipx install uv        # isolated install (recommended)" \
	    "  pip install --user uv  # into your Python user site" \
	    "  docs: https://docs.astral.sh/uv/getting-started/installation/"; \
	  exit 1; \
	}

.PHONY: dev-setup
dev-setup: _require-uv ## Create .venv from the lockfile and install pre-commit hooks
	# Own the virtualenv so every other target runs against a known,
	# pinned interpreter (see the tool variables above). ``uv sync --locked``
	# populates ``.venv`` from ``uv.lock`` and FAILS FAST if the lock is stale
	# relative to pyproject.toml, so the local environment is the same
	# reproducible set CI installs (ADR 0048) and never silently re-resolves
	# or rewrites the lock as a side effect of setup. If this errors because
	# you changed dependencies, run ``make lock`` first, then re-run.
	$(UV) sync --locked --extra dev
	.venv/bin/pre-commit install --install-hooks
	.venv/bin/pre-commit install --hook-type commit-msg
	@echo "Dev environment ready in .venv. Activate with: source .venv/bin/activate"

.PHONY: lock
lock: _require-uv ## Re-resolve and rewrite uv.lock after changing deps in pyproject.toml
	# The only sanctioned way to change the locked set: run this whenever you
	# edit ``[project] dependencies`` or an ``[project.optional-dependencies]``
	# extra, then commit the updated ``uv.lock`` alongside the pyproject change.
	# The lint gate's ``uv lock --check`` rejects a pyproject edit whose lock
	# was not regenerated (ADR 0048).
	$(UV) lock

.PHONY: clean
clean: ## Remove build and test artifacts
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache \
		.coverage coverage.xml htmlcov .benchmarks .mutmut-cache mutants \
		.hypothesis site test-results
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# ---------------------------------------------------------------------------
# Lint / format / types
# ---------------------------------------------------------------------------

.PHONY: format
format: ## Auto-format with ruff
	$(RUFF) format src tests scripts
	$(RUFF) check --fix src tests scripts

.PHONY: lint
lint: ## Lint + typecheck + security scan
	$(RUFF) check src tests scripts
	$(RUFF) format --check src tests scripts
	$(MYPY) src
	$(BANDIT) -c pyproject.toml -r src -ll -q
	# Filter ``.pip-audit-ignore`` for advisory IDs only (skip comments
	# + blank lines) and feed each as a ``--ignore-vuln`` flag. The
	# previous form used ``xargs -a <file>`` which is GNU-only and
	# silently treated comment lines as advisory IDs on BSD systems.
	awk '/^(PYSEC|CVE|GHSA)-/ {printf "--ignore-vuln %s\n", $$1}' .pip-audit-ignore | xargs $(PIP_AUDIT)
	$(INTERROGATE) src
	typos .

linkcheck: ## Run lychee with the workspace-aware self-link remap (CI parity)
	@command -v lychee >/dev/null 2>&1 || { \
	  echo "lychee not installed. Install via:"; \
	  echo "  brew install lychee   # macOS"; \
	  echo "  cargo install lychee  # other"; \
	  exit 1; \
	}
	lychee --no-progress \
	  --config .lychee.toml \
	  --remap 'https://github.com/jjviscomi/bqemulator/blob/main/(.+) file://$(CURDIR)/$$1' \
	  --remap 'https://github.com/jjviscomi/bqemulator/tree/main/(.+) file://$(CURDIR)/$$1' \
	  '**/*.md'

# ---------------------------------------------------------------------------
# Quality gates (complexity required; duplication + dead-code non-blocking)
# ---------------------------------------------------------------------------
#
# Three gates that ``ruff`` and the standard ``make lint`` chain don't
# meaningfully enforce today:
#
#   - quality-complexity: xenon (radon wrapper) checks per-function
#     cyclomatic-complexity rank. ruff's C901 / PLR0911 / PLR0912 /
#     PLR0913 are all in pyproject's ignore list ("type-dispatch is
#     naturally branchy"); xenon provides an absolute-rank ceiling
#     without per-function noqa annotations.
#
#   - quality-duplication: jscpd (cross-file DRY). Nothing in the
#     standard chain catches this — pylint's duplicate-code is the
#     only other Python-native option and it's slower with weaker
#     output. Requires node/npm for ``npx``; CI installs both.
#
#   - quality-dead-code: vulture, configured in pyproject.toml but
#     previously unwired. Surfaces names not referenced anywhere.
#
# Wiring status:
#
#   - ``quality-complexity`` is REQUIRED (part of ``make verify``, no
#     ``continue-on-error`` on its CI step). ADR 0036 promoted the
#     gate from non-blocking to required and ratcheted the absolute
#     threshold from rank E to rank C; ADR 0041 ratcheted it further
#     from rank C to rank B after the PR-1 through PR-11 sweep closed
#     every remaining rank-C function (~60 across 26 files); ADR 0042
#     ratcheted the per-module-average threshold from rank C to rank B
#     as a single-PR config flip (the per-function campaign drove
#     module averages down to ≤B as a side effect — verified by
#     ``xenon --max-modules B`` exit 0 pre-flip; zero refactor work
#     required for the module ratchet).
#   - ``quality-duplication`` and ``quality-dead-code`` are still
#     non-blocking in CI; their own promote-to-required PRs come
#     when the baselines settle.

.PHONY: quality-complexity
quality-complexity: ## Cyclomatic-complexity ceiling via xenon — REQUIRED gate (per-function + per-module both at B in ADR 0041 + ADR 0042)
	# Threshold: every function in src/bqemulator must rank B or
	# better (cyclomatic complexity ≤ 10), every module average must
	# stay ≤ rank B (CC avg ≤ 10), and the project-wide average must
	# stay ≤ rank A (currently 3.06 against the 5.0 rank-A ceiling).
	# ADR 0036 documents the original C-ratchet bucket-A/B refactor
	# patterns; ADR 0041 documents the per-function C→B campaign
	# retrospective; ADR 0042 documents the per-module C→B audit
	# (campaign side-effect, no extra refactor work).
	# Any new function above rank B either gets a behavior-preserving
	# refactor (dispatch table or helper extraction — see the existing
	# ``_DUCKDB_TRANSLATORS`` / ``_ARROW_TO_BQ_RULES`` /
	# ``_LOAD_FORMAT_HANDLERS`` / ``_STATEMENT_DISPATCH`` examples for
	# the canonical shape) or a separate PR adding a documented xenon
	# ``--exclude`` carve-out (a new bucket-C irreducibility verdict —
	# bar is genuine domain-shaped complexity, not accumulated cruft;
	# the empirical bucket-C rate across PR-1…PR-11 was 0%).
	$(XENON) --max-absolute B --max-modules B --max-average A src/bqemulator

.PHONY: quality-duplication
quality-duplication: ## Cross-file DRY check via jscpd (non-blocking)
	# Requires ``npx`` on PATH (node/npm). ``-y`` auto-confirms the
	# jscpd download on first run; ``@4`` pins the major version so
	# the gate is reproducible (ADR 0035 documents the choice — a
	# jscpd 5.x release can break our config / threshold semantics).
	npx -y jscpd@4 --config .jscpd.json

.PHONY: quality-dead-code
quality-dead-code: ## Dead-name detection via vulture (non-blocking)
	# Config + whitelist live in pyproject.toml [tool.vulture] and
	# .vulture_whitelist.py.
	vulture

.PHONY: quality
quality: quality-complexity quality-duplication quality-dead-code ## Run all quality gates (complexity required; rest non-blocking)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

.PHONY: test-unit
test-unit: ## Fast hermetic unit tests (no coverage gate — see test-coverage)
	$(PYTEST) tests/unit -m unit

.PHONY: test-property
test-property: ## Hypothesis property-based tests
	$(PYTEST) tests/property -m property

.PHONY: test-integration
test-integration: ## In-process emulator + client integration tests
	$(PYTEST) tests/integration -m integration

.PHONY: test-coverage
test-coverage: ## Combined U+P+I coverage gate (90% line+branch; matches STATUS.md methodology)
	$(PYTEST) tests/unit tests/property tests/integration \
		--cov=bqemulator --cov-branch \
		--cov-report=term --cov-report=xml \
		--cov-fail-under=90

# Patch-coverage gate. ``--cov-fail-under`` above checks the
# **project** total. This target checks the **diff** — the lines this
# branch adds (or modifies) versus ``main``. Mirrors Codecov's
# ``patch`` status (configured at 70% in codecov.yml). Catches the
# gap where the project total stays ≥90% but the PR's own new
# lines are uncovered. Requires a fresh ``coverage.xml`` (the
# ``test-coverage`` target now emits one).
PATCH_COVERAGE_BASE ?= origin/main
.PHONY: test-patch-coverage
test-patch-coverage: ## Patch coverage on lines added vs main (≥70%); needs prior `make test-coverage`
	@if [ ! -f coverage.xml ]; then \
	    echo "ERROR: coverage.xml missing — run 'make test-coverage' first." >&2; \
	    exit 1; \
	fi
	$(DIFF_COVER) coverage.xml \
		--compare-branch=$(PATCH_COVERAGE_BASE) \
		--fail-under=70 \
		--include-untracked

.PHONY: test-e2e
test-e2e: test-e2e-python test-e2e-nodejs test-e2e-go test-e2e-java test-e2e-bq-cli ## E2E against live container (all five conformance clients)

.PHONY: test-e2e-python
test-e2e-python: docker-build ## E2E — Python client (uses testcontainers; auto-manages container lifecycle)
	BQEMU_IMAGE="$(DOCKER_IMAGE):$(DOCKER_TAG)" $(PYTEST) tests/e2e/python_client -m e2e

# The Node/Go/Java suites read BQEMU_REST_URL / BQEMU_GRPC_ENDPOINT and
# expect a container already running on those endpoints. Each recipe
# starts a fresh container, polls healthz, sets a teardown trap, then
# runs the language's suite — all inside a single shell invocation
# (joined with ``\`` continuations) so the trap fires in the same
# shell as the tests.
#
# Why one big recipe per language: the trap MUST run in the same shell
# as ``npm test`` / ``go test`` / ``mvn`` so a test failure still
# triggers cleanup. ``.ONESHELL:`` (declared above) would let us split
# the recipe across multiple physical lines and still share a shell —
# but GNU Make < 3.82 (notably the macOS Xcode-bundled make 3.81)
# silently ignores ``.ONESHELL:``, causing each line to become its own
# shell and the trap to fire too early. The ``\``-continuation form
# is portable to both make 3.81 and modern make.

.PHONY: test-e2e-nodejs
test-e2e-nodejs: docker-build ## E2E — Node.js client (live container)
	docker rm -f bqemu-e2e-nodejs > /dev/null 2>&1 || true ; \
	GCS_DIR="$$(mktemp -d -t bqemu-g1-nodejs.XXXXXX)" ; chmod 777 "$$GCS_DIR" ; \
	trap 'docker rm -f bqemu-e2e-nodejs > /dev/null 2>&1 || true ; rm -rf "$$GCS_DIR"' EXIT ; \
	$(PYTHON) scripts/stage_g1_e2e_fixtures.py --root "$$GCS_DIR" ; \
	docker run -d --name bqemu-e2e-nodejs \
		-p 9050:9050 -p 9060:9060 \
		-v "$$GCS_DIR:/var/lib/bqemu-gcs:rw" \
		-e BQEMU_REST_HOST=0.0.0.0 -e BQEMU_GRPC_HOST=0.0.0.0 \
		-e BQEMU_REST_PORT=9050 -e BQEMU_GRPC_PORT=9060 \
		-e BQEMU_ADMIN_ENABLED=1 \
		-e BQEMU_GCS_LOCAL_ROOT=/var/lib/bqemu-gcs \
		$(DOCKER_IMAGE):$(DOCKER_TAG) > /dev/null ; \
	for _ in $$(seq 1 120); do \
		if curl -sf http://localhost:9050/healthz > /dev/null; then break; fi; \
		sleep 0.5; \
	done ; \
	cd tests/e2e/nodejs_client && npm ci && \
		BQEMU_REST_URL=http://localhost:9050 BQEMU_GRPC_ENDPOINT=localhost:9060 \
		BQEMU_GCS_HOST_ROOT="$$GCS_DIR" npm test

.PHONY: test-e2e-go
test-e2e-go: docker-build ## E2E — Go client (live container)
	docker rm -f bqemu-e2e-go > /dev/null 2>&1 || true ; \
	GCS_DIR="$$(mktemp -d -t bqemu-g1-go.XXXXXX)" ; chmod 777 "$$GCS_DIR" ; \
	trap 'docker rm -f bqemu-e2e-go > /dev/null 2>&1 || true ; rm -rf "$$GCS_DIR"' EXIT ; \
	$(PYTHON) scripts/stage_g1_e2e_fixtures.py --root "$$GCS_DIR" ; \
	docker run -d --name bqemu-e2e-go \
		-p 9050:9050 -p 9060:9060 \
		-v "$$GCS_DIR:/var/lib/bqemu-gcs:rw" \
		-e BQEMU_REST_HOST=0.0.0.0 -e BQEMU_GRPC_HOST=0.0.0.0 \
		-e BQEMU_REST_PORT=9050 -e BQEMU_GRPC_PORT=9060 \
		-e BQEMU_ADMIN_ENABLED=1 \
		-e BQEMU_GCS_LOCAL_ROOT=/var/lib/bqemu-gcs \
		$(DOCKER_IMAGE):$(DOCKER_TAG) > /dev/null ; \
	for _ in $$(seq 1 120); do \
		if curl -sf http://localhost:9050/healthz > /dev/null; then break; fi; \
		sleep 0.5; \
	done ; \
	cd tests/e2e/go_client && \
		BQEMU_REST_URL=http://localhost:9050 BQEMU_GRPC_ENDPOINT=localhost:9060 \
		BQEMU_GCS_HOST_ROOT="$$GCS_DIR" \
		go test ./... -count=1

.PHONY: test-e2e-java
test-e2e-java: docker-build ## E2E — Java client (live container)
	docker rm -f bqemu-e2e-java > /dev/null 2>&1 || true ; \
	GCS_DIR="$$(mktemp -d -t bqemu-g1-java.XXXXXX)" ; chmod 777 "$$GCS_DIR" ; \
	trap 'docker rm -f bqemu-e2e-java > /dev/null 2>&1 || true ; rm -rf "$$GCS_DIR"' EXIT ; \
	$(PYTHON) scripts/stage_g1_e2e_fixtures.py --root "$$GCS_DIR" ; \
	docker run -d --name bqemu-e2e-java \
		-p 9050:9050 -p 9060:9060 \
		-v "$$GCS_DIR:/var/lib/bqemu-gcs:rw" \
		-e BQEMU_REST_HOST=0.0.0.0 -e BQEMU_GRPC_HOST=0.0.0.0 \
		-e BQEMU_REST_PORT=9050 -e BQEMU_GRPC_PORT=9060 \
		-e BQEMU_ADMIN_ENABLED=1 \
		-e BQEMU_GCS_LOCAL_ROOT=/var/lib/bqemu-gcs \
		$(DOCKER_IMAGE):$(DOCKER_TAG) > /dev/null ; \
	for _ in $$(seq 1 120); do \
		if curl -sf http://localhost:9050/healthz > /dev/null; then break; fi; \
		sleep 0.5; \
	done ; \
	cd tests/e2e/java_client && \
		BQEMU_REST_URL=http://localhost:9050 BQEMU_GRPC_ENDPOINT=localhost:9060 \
		BQEMU_GCS_HOST_ROOT="$$GCS_DIR" \
		mvn -B test

# G5 (2026-05-21) — fifth conformance client: Google's ``bq`` CLI. The
# suite uses testcontainers (same lifecycle as test-e2e-python). The
# recipe pre-flights ``bq`` on PATH so a CI miss doesn't silently
# no-op via pytest.skip; local developers without gcloud SDK still
# get a clear error rather than a confusing pass.
.PHONY: test-e2e-bq-cli
test-e2e-bq-cli: docker-build ## E2E — bq CLI (live container; requires google-cloud-sdk on PATH)
	@command -v bq >/dev/null 2>&1 || { \
	    echo "ERROR: bq CLI not installed. Install google-cloud-sdk:" ; \
	    echo "  https://docs.cloud.google.com/sdk/docs/install" ; \
	    exit 1 ; \
	}
	BQEMU_IMAGE="$(DOCKER_IMAGE):$(DOCKER_TAG)" $(PYTEST) tests/e2e/bq_cli_client -m e2e

.PHONY: test-conformance
test-conformance: ## Replay conformance corpus against in-process emulator (offline; no creds needed)
	$(PYTEST) tests/conformance -m conformance

.PHONY: coverage-matrix
coverage-matrix: ## Regenerate docs/reference/conformance-coverage-matrix.md from the surface inventory + corpus
	$(PYTHON) scripts/generate_coverage_matrix.py

.PHONY: coverage-matrix-check
coverage-matrix-check: ## CI gate — fail if the committed matrix has drifted from the inventory or corpus
	$(PYTHON) scripts/generate_coverage_matrix.py --check

.PHONY: compat-matrix
compat-matrix: ## Regenerate the conformance snapshot inside docs/reference/compatibility-matrix.md
	$(PYTHON) scripts/generate_compatibility_matrix.py

.PHONY: compat-matrix-check
compat-matrix-check: ## CI gate — fail if the committed compatibility-matrix snapshot has drifted from the corpus
	$(PYTHON) scripts/generate_compatibility_matrix.py --check

.PHONY: function-mapping
function-mapping: ## Regenerate the rule registry inside docs/reference/sql-function-mapping.md
	$(PYTHON) scripts/generate_function_mapping.py

.PHONY: function-mapping-check
function-mapping-check: ## CI gate — fail if the committed function-mapping registry has drifted from the live rules
	$(PYTHON) scripts/generate_function_mapping.py --check

.PHONY: api-coverage
api-coverage: ## Regenerate the REST + gRPC inventory inside docs/reference/api-coverage.md
	$(PYTHON) scripts/generate_api_coverage.py

.PHONY: api-coverage-check
api-coverage-check: ## CI gate — fail if the committed API inventory has drifted from the live route handlers
	$(PYTHON) scripts/generate_api_coverage.py --check

.PHONY: generate-avro-fixtures
generate-avro-fixtures: ## Regenerate the reference .avro OCFs under tests/fixtures/avro/ (G3 / ADR 0030)
	$(PYTHON) scripts/generate_avro_fixtures.py

.PHONY: record-conformance
record-conformance: ## Re-record conformance baselines from real BigQuery (local-only; requires GOOGLE_APPLICATION_CREDENTIALS)
	@test -n "$${GOOGLE_APPLICATION_CREDENTIALS:-}" \
	  || (echo "GOOGLE_APPLICATION_CREDENTIALS not set — recording requires a real-BQ service-account JSON" && exit 1)
	@test -n "$${BQEMU_CONFORMANCE_PROJECT:-}" \
	  || (echo "BQEMU_CONFORMANCE_PROJECT not set — supply a BQ project you control to bill the recording jobs to" && exit 1)
	$(PYTHON) scripts/record_conformance_fixtures.py \
	  --project "$${BQEMU_CONFORMANCE_PROJECT}" \
	  --location "$${BQEMU_CONFORMANCE_LOCATION:-US}"

.PHONY: test-perf
test-perf: ## Performance benchmarks (Tier 6; compares against committed baseline; --benchmark-save is a deliberate operator action)
	@arch=$$($(PYTHON) -c "import platform, sys; m=platform.machine().lower(); p=sys.platform.lower(); print('darwin-arm64' if p.startswith('darwin') and m in ('arm64','aarch64') else 'linux-arm64' if p.startswith('linux') and m in ('arm64','aarch64') else 'linux-x86_64')") ; \
	baseline=tests/perf/baselines/$$arch.json ; \
	if [ -f $$baseline ] ; then \
	    echo "Comparing perf against committed baseline: $$baseline" ; \
	    $(PYTEST) tests/perf -m perf --benchmark-only ; \
	else \
	    echo "No committed baseline for $$arch yet — running without comparison gate." ; \
	    echo "Record one with: pytest tests/perf --benchmark-save=$$arch && python scripts/normalize_perf_baseline.py .benchmarks/<latest>.json $$baseline" ; \
	    $(PYTEST) tests/perf -m perf --benchmark-only ; \
	fi

.PHONY: test-chaos
test-chaos: ## Chaos tier — deliberately disruptive (Phase 11; nightly in CI)
	$(PYTEST) tests/chaos -m chaos --timeout=60

.PHONY: test-differential
test-differential: ## Differential tier — row-order perturbation of the conformance corpus (P8.f; manual-only CI per ADR 0028)
	$(PYTEST) tests/conformance/test_corpus_row_order_perturbed.py \
	    -m differential \
	    --junit-xml=differential-results.xml

.PHONY: test-fuzz
test-fuzz: ## Fuzz tier — Atheris coverage-guided fuzzing of translator + dyn-proto + arrow-bridge (manual-only CI per ADR 0031)
	@# Atheris 3.0.0 supports Python 3.11/3.12/3.13. Operators on a
	@# Python 3.14 venv (the asdf-default in the maintainer's user-home
	@# `~/.tool-versions`) hit an Atheris-incompatible interpreter — this
	@# target imports the package as a feasibility probe and prints a
	@# clear remediation message when it fails. The project's own
	@# `.tool-versions` pins only Java; Python comes from the operator's
	@# environment. Local-run setup:
	@#   asdf install python 3.13.<latest>
	@#   python3.13 -m venv .venv-fuzz && . .venv-fuzz/bin/activate
	@#   pip install -e ".[dev]" 'atheris>=3.0'
	@$(PYTHON) -c "import atheris" 2>/dev/null || { \
	    echo "ERROR: atheris not importable in $$($(PYTHON) -V) — Atheris 3.0.0 supports 3.11/3.12/3.13 only." ; \
	    echo "Set up a 3.13 venv: asdf install python 3.13.<latest>; python3.13 -m venv .venv-fuzz; . .venv-fuzz/bin/activate; pip install -e \".[dev]\" 'atheris>=3.0'." ; \
	    exit 1 ; \
	}
	@# Per-harness budget: 60 seconds locally (this target); CI bumps to
	@# 600 seconds in fuzz.yml. The bound is empirical — coverage-guided
	@# fuzzing has diminishing-returns characteristics past the first
	@# few minutes once the seed corpus is exhausted (ADR 0031 §5).
	$(PYTHON) fuzz/fuzz_sql_translator.py -max_total_time=60 fuzz/corpus/sql_translator
	$(PYTHON) fuzz/fuzz_dyn_proto.py -max_total_time=60 fuzz/corpus/dyn_proto
	$(PYTHON) fuzz/fuzz_arrow_bridge.py -max_total_time=60 fuzz/corpus/arrow_bridge

.PHONY: test-mutation
test-mutation: ## Mutation testing (slow — nightly; fails when score drops >2pp vs baseline)
	mutmut run
	mutmut export-cicd-stats
	$(PYTHON) scripts/check_mutation_baseline.py

.PHONY: test-mutation-baseline
test-mutation-baseline: ## Run mutmut and overwrite tests/mutation/baseline.json (operator action)
	mutmut run
	mutmut export-cicd-stats
	$(PYTHON) scripts/check_mutation_baseline.py --update-baseline

.PHONY: test
test: test-unit test-property test-integration ## Unit + property + integration

.PHONY: coverage-report
coverage-report: ## Generate HTML coverage report
	$(PYTEST) tests/unit tests/property tests/integration \
		--cov=bqemulator --cov-branch --cov-report=html --cov-report=term
	@echo "Open htmlcov/index.html"

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

.PHONY: docker-build
docker-build: ## Build multi-arch container image (tag: dev)
	docker buildx build \
		--platform $(DOCKER_PLATFORMS) \
		--tag $(DOCKER_IMAGE):$(DOCKER_TAG) \
		--load \
		.

.PHONY: docker-run
docker-run: ## Run the built container locally
	docker run --rm -p 9050:9050 -p 9060:9060 $(DOCKER_IMAGE):$(DOCKER_TAG)

# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

.PHONY: docs-serve
docs-serve: ## Live-reload documentation site
	$(MKDOCS) serve

.PHONY: docs-build
docs-build: ## Build documentation site (strict)
	$(MKDOCS) build --strict

.PHONY: docs-deploy
docs-deploy: ## Deploy docs to GitHub Pages via mike
	mike deploy --push --update-aliases $$($(PYTHON) -c "from bqemulator import __version__; print(__version__)") latest

# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

.PHONY: release-dry-run
# Override the bump kind with: ``make release-dry-run NEXT=minor`` (or major/patch).
# To pin an explicit version: ``make release-dry-run VERSION=1.0.0``.
NEXT ?= patch
release-dry-run: ## Dry run of the release pipeline (override with NEXT=minor or VERSION=X.Y.Z)
ifdef VERSION
	$(PYTHON) scripts/release.py --dry-run --version $(VERSION)
else
	$(PYTHON) scripts/release.py --dry-run --next $(NEXT)
endif

.PHONY: release
release: ## Apply the release pipeline (override with NEXT=minor or VERSION=X.Y.Z)
ifdef VERSION
	$(PYTHON) scripts/release.py --apply --version $(VERSION)
else
	$(PYTHON) scripts/release.py --apply --next $(NEXT)
endif

.PHONY: build
build: clean ## Build wheel and sdist
	$(PYTHON) -m build

# ---------------------------------------------------------------------------
# Composite gates
# ---------------------------------------------------------------------------

.PHONY: verify
verify: lint quality-complexity test test-coverage \
        coverage-matrix-check compat-matrix-check function-mapping-check api-coverage-check \
        docker-build test-e2e docs-build ## Full release-ready gate chain
	@echo ""
	@echo "All release-readiness gates passed."

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

.PHONY: run
run: ## Start an ephemeral emulator on :9050 / :9060
	bqemulator start --ephemeral

.PHONY: regenerate-protos
regenerate-protos: ## Regenerate gRPC proto stubs from vendored googleapis
	scripts/generate_protos.sh

.PHONY: matrix
matrix: ## Regenerate every auto-generated reference doc (compat-matrix + function-mapping + api-coverage)
	$(PYTHON) scripts/generate_compatibility_matrix.py
	$(PYTHON) scripts/generate_function_mapping.py
	$(PYTHON) scripts/generate_api_coverage.py
