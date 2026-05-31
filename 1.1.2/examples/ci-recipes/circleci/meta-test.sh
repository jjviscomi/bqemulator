#!/usr/bin/env bash
# Verifies the CircleCI recipe is self-consistent.

set -euo pipefail

cd "$(dirname "$0")"

python - <<'PY'
import yaml

with open("config.yml") as f:
    doc = yaml.safe_load(f)

assert doc["version"] == 2.1, "must be CircleCI 2.1"

# Docker executor pattern.
docker_job = doc["jobs"]["test-docker"]
images = [img["image"] for img in docker_job["docker"]]
assert any("bqemulator" in img for img in images), "secondary bqemulator image required"
assert "BIGQUERY_EMULATOR_HOST" in docker_job["environment"]
assert docker_job["environment"]["BIGQUERY_EMULATOR_HOST"].startswith("localhost:")

# Machine executor pattern.
machine_job = doc["jobs"]["test-machine"]
assert "machine" in machine_job, "machine executor pattern required"

# Workflows wire both.
wf = doc["workflows"]["pull-request"]
assert "test-docker" in wf["jobs"], "workflow must run test-docker"
assert "test-machine" in wf["jobs"], "workflow must run test-machine"

print("OK: config.yml passes meta-tests")
PY
