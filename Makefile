# Makefile — canonical entry points for bqemulator development.
#
# Every target is documented. `make help` lists them.
# `make verify` runs the full release-ready gate chain.

.DEFAULT_GOAL := help
SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c

PYTHON := python3
PIP := $(PYTHON) -m pip
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

.PHONY: dev-setup
dev-setup: ## Install dependencies and pre-commit hooks
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	pre-commit install --install-hooks
	pre-commit install --hook-type commit-msg
	@echo "Dev environment ready."

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
	ruff format src tests scripts
	ruff check --fix src tests scripts

.PHONY: lint
lint: ## Lint + typecheck + security scan
	ruff check src tests scripts
	ruff format --check src tests scripts
	mypy src
	bandit -c pyproject.toml -r src -ll -q
	# Filter ``.pip-audit-ignore`` for advisory IDs only (skip comments
	# + blank lines) and feed each as a ``--ignore-vuln`` flag. The
	# previous form used ``xargs -a <file>`` which is GNU-only and
	# silently treated comment lines as advisory IDs on BSD systems.
	awk '/^(PYSEC|CVE|GHSA)-/ {printf "--ignore-vuln %s\n", $$1}' .pip-audit-ignore | xargs pip-audit
	interrogate src
	typos .

# ---------------------------------------------------------------------------
# Quality gates (non-blocking; see ADR 0035)
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
#     ``continue-on-error`` on its CI step). ADR 0036 ratcheted the
#     threshold from rank E to rank C and promoted the gate.
#   - ``quality-duplication`` and ``quality-dead-code`` are still
#     non-blocking in CI; their own promote-to-required PRs come
#     when the baselines settle.

.PHONY: quality-complexity
quality-complexity: ## Cyclomatic-complexity ceiling via xenon — REQUIRED gate (ratcheted to C in ADR 0036)
	# Threshold: every function in src/bqemulator must rank C or
	# better (cyclomatic complexity ≤ 20), every module average must
	# stay ≤ rank C, and the project-wide average must stay ≤ rank A.
	# ADR 0036 documents the audit + the bucket-A/B refactors that
	# closed the original 10 D+E functions. Any new function above
	# rank C either gets a behavior-preserving refactor (typically a
	# dispatch table or helper extraction) or a separate PR adding
	# a documented xenon ``--exclude`` carve-out (a new bucket-C
	# irreducibility verdict — bar is genuine domain-shaped
	# complexity, not accumulated cruft).
	xenon --max-absolute C --max-modules C --max-average A src/bqemulator

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
quality: quality-complexity quality-duplication quality-dead-code ## Run all non-blocking quality gates

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

.PHONY: test-unit
test-unit: ## Fast hermetic unit tests (no coverage gate — see test-coverage)
	pytest tests/unit -m unit

.PHONY: test-property
test-property: ## Hypothesis property-based tests
	pytest tests/property -m property

.PHONY: test-integration
test-integration: ## In-process emulator + client integration tests
	pytest tests/integration -m integration

.PHONY: test-coverage
test-coverage: ## Combined U+P+I coverage gate (90% line+branch; matches STATUS.md methodology)
	pytest tests/unit tests/property tests/integration \
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
	diff-cover coverage.xml \
		--compare-branch=$(PATCH_COVERAGE_BASE) \
		--fail-under=70 \
		--include-untracked

.PHONY: test-e2e
test-e2e: test-e2e-python test-e2e-nodejs test-e2e-go test-e2e-java test-e2e-bq-cli ## E2E against live container (all five conformance clients)

.PHONY: test-e2e-python
test-e2e-python: docker-build ## E2E — Python client (uses testcontainers; auto-manages container lifecycle)
	BQEMU_IMAGE="$(DOCKER_IMAGE):$(DOCKER_TAG)" pytest tests/e2e/python_client -m e2e

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
	    echo "  https://cloud.google.com/sdk/docs/install" ; \
	    exit 1 ; \
	}
	BQEMU_IMAGE="$(DOCKER_IMAGE):$(DOCKER_TAG)" pytest tests/e2e/bq_cli_client -m e2e

.PHONY: test-conformance
test-conformance: ## Replay conformance corpus against in-process emulator (offline; no creds needed)
	pytest tests/conformance -m conformance

.PHONY: coverage-matrix
coverage-matrix: ## Regenerate docs/reference/conformance-coverage-matrix.md from the surface inventory + corpus
	python scripts/generate_coverage_matrix.py

.PHONY: coverage-matrix-check
coverage-matrix-check: ## CI gate — fail if the committed matrix has drifted from the inventory or corpus
	python scripts/generate_coverage_matrix.py --check

.PHONY: compat-matrix
compat-matrix: ## Regenerate the conformance snapshot inside docs/reference/compatibility-matrix.md
	python scripts/generate_compatibility_matrix.py

.PHONY: compat-matrix-check
compat-matrix-check: ## CI gate — fail if the committed compatibility-matrix snapshot has drifted from the corpus
	python scripts/generate_compatibility_matrix.py --check

.PHONY: function-mapping
function-mapping: ## Regenerate the rule registry inside docs/reference/sql-function-mapping.md
	python scripts/generate_function_mapping.py

.PHONY: function-mapping-check
function-mapping-check: ## CI gate — fail if the committed function-mapping registry has drifted from the live rules
	python scripts/generate_function_mapping.py --check

.PHONY: api-coverage
api-coverage: ## Regenerate the REST + gRPC inventory inside docs/reference/api-coverage.md
	python scripts/generate_api_coverage.py

.PHONY: api-coverage-check
api-coverage-check: ## CI gate — fail if the committed API inventory has drifted from the live route handlers
	python scripts/generate_api_coverage.py --check

.PHONY: generate-avro-fixtures
generate-avro-fixtures: ## Regenerate the reference .avro OCFs under tests/fixtures/avro/ (G3 / ADR 0030)
	python scripts/generate_avro_fixtures.py

.PHONY: record-conformance
record-conformance: ## Re-record conformance baselines from real BigQuery (local-only; requires GOOGLE_APPLICATION_CREDENTIALS)
	@test -n "$${GOOGLE_APPLICATION_CREDENTIALS:-}" \
	  || (echo "GOOGLE_APPLICATION_CREDENTIALS not set — recording requires a real-BQ service-account JSON" && exit 1)
	@test -n "$${BQEMU_CONFORMANCE_PROJECT:-}" \
	  || (echo "BQEMU_CONFORMANCE_PROJECT not set — supply a BQ project you control to bill the recording jobs to" && exit 1)
	python scripts/record_conformance_fixtures.py \
	  --project "$${BQEMU_CONFORMANCE_PROJECT}" \
	  --location "$${BQEMU_CONFORMANCE_LOCATION:-US}"

.PHONY: test-perf
test-perf: ## Performance benchmarks (Tier 6; compares against committed baseline; --benchmark-save is a deliberate operator action)
	@arch=$$($(PYTHON) -c "import platform, sys; m=platform.machine().lower(); p=sys.platform.lower(); print('darwin-arm64' if p.startswith('darwin') and m in ('arm64','aarch64') else 'linux-arm64' if p.startswith('linux') and m in ('arm64','aarch64') else 'linux-x86_64')") ; \
	baseline=tests/perf/baselines/$$arch.json ; \
	if [ -f $$baseline ] ; then \
	    echo "Comparing perf against committed baseline: $$baseline" ; \
	    pytest tests/perf -m perf --benchmark-only ; \
	else \
	    echo "No committed baseline for $$arch yet — running without comparison gate." ; \
	    echo "Record one with: pytest tests/perf --benchmark-save=$$arch && python scripts/normalize_perf_baseline.py .benchmarks/<latest>.json $$baseline" ; \
	    pytest tests/perf -m perf --benchmark-only ; \
	fi

.PHONY: test-chaos
test-chaos: ## Chaos tier — deliberately disruptive (Phase 11; nightly in CI)
	pytest tests/chaos -m chaos --timeout=60

.PHONY: test-differential
test-differential: ## Differential tier — row-order perturbation of the conformance corpus (P8.f; manual-only CI per ADR 0028)
	pytest tests/conformance/test_corpus_row_order_perturbed.py \
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
	python scripts/check_mutation_baseline.py

.PHONY: test-mutation-baseline
test-mutation-baseline: ## Run mutmut and overwrite tests/mutation/baseline.json (operator action)
	mutmut run
	mutmut export-cicd-stats
	python scripts/check_mutation_baseline.py --update-baseline

.PHONY: test
test: test-unit test-property test-integration ## Unit + property + integration

.PHONY: coverage-report
coverage-report: ## Generate HTML coverage report
	pytest tests/unit tests/property tests/integration \
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
	mkdocs serve

.PHONY: docs-build
docs-build: ## Build documentation site (strict)
	mkdocs build --strict

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
