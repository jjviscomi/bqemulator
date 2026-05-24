#!/usr/bin/env bash
# Verifies the GitLab CI recipe is self-consistent.

set -euo pipefail

cd "$(dirname "$0")"

python - <<'PY'
import yaml

with open("gitlab-ci.yml") as f:
    doc = yaml.safe_load(f)

assert "test" in doc, "missing test job"
job = doc["test"]
assert "services" in job, "test job must declare bqemulator as a service"
svc = job["services"][0]
assert svc["alias"] == "bqemulator", f"service alias must be 'bqemulator', got {svc['alias']}"
assert "bqemulator" in svc["name"], f"image must be the emulator: {svc['name']}"
assert "BIGQUERY_EMULATOR_HOST" in doc["variables"], "must set BIGQUERY_EMULATOR_HOST"
assert doc["variables"]["BIGQUERY_EMULATOR_HOST"].startswith("bqemulator:")

print("OK: gitlab-ci.yml passes meta-tests")
PY
