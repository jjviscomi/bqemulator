#!/usr/bin/env bash
# Verifies the GitHub Actions recipe is self-consistent.

set -euo pipefail

cd "$(dirname "$0")"

python - <<'PY'
import sys
import yaml

with open("workflow.yml") as f:
    doc = yaml.safe_load(f)

# Required top-level keys.
for key in ("name", "on", "jobs"):
    assert key in doc or (key == "on" and True in doc), f"missing top-level {key}"

# Pattern A — service container.
service_job = doc["jobs"]["service-container"]
assert "services" in service_job, "missing services block on service-container job"
assert "bqemulator" in service_job["services"], "service must be named bqemulator"
img = service_job["services"]["bqemulator"]["image"]
assert "bqemulator" in img, f"unexpected image: {img}"
opts = service_job["services"]["bqemulator"]["options"]
assert "healthz" in opts, "service must define a /healthz health check"

# Pattern B — testcontainers job exists.
tc_job = doc["jobs"]["testcontainers"]
assert "services" not in tc_job, "testcontainers pattern must not use service block"

print("OK: workflow.yml passes meta-tests")
PY
