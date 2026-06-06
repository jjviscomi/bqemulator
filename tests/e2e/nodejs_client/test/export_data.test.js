/**
 * E2E: EXPORT DATA → Cloud Storage (CSV) against a live bqemulator
 * container via the @google-cloud/bigquery Node.js client.
 *
 * EXPORT DATA runs as a query job: the inner SELECT is materialised and
 * written to the wildcard `uri` under `BQEMU_GCS_LOCAL_ROOT`. With a
 * single output shard the `*` expands to a 12-digit zero-padded counter,
 * so `export_nodejs/*.csv` becomes `export_nodejs/000000000000.csv`. The
 * Makefile target (`test-e2e-nodejs`) bind-mounts a host directory onto
 * `/var/lib/bqemu-gcs` and exports its host path as
 * `BQEMU_GCS_HOST_ROOT`, so we read the exported file straight off the
 * mount.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const GCS_HOST_ROOT = process.env.BQEMU_GCS_HOST_ROOT;
const PROJECT = "e2e-nodejs-export";
const DATASET = "export_node_ds";
const BUCKET = "g1-e2e";

function makeClient() {
  const { BigQuery } = require("@google-cloud/bigquery");
  const { OAuth2Client } = require("google-auth-library");

  const fake = new OAuth2Client();
  fake.credentials = { access_token: "anonymous" };

  return new BigQuery({
    projectId: PROJECT,
    apiEndpoint: REST_URL,
    authClient: fake,
    autoRetry: false,
  });
}

describe("bqemulator EXPORT DATA → CSV (Node.js)", () => {
  let client;
  before(async () => {
    client = makeClient();
    // Reset any dataset left by a crashed prior run so reruns are deterministic.
    try {
      await client.dataset(DATASET).delete({ force: true });
    } catch (_) {
      /* ignore */
    }
    await client.createDataset(DATASET, { location: "US" });
  });
  after(async () => {
    try {
      await client.dataset(DATASET).delete({ force: true });
    } catch (_) {
      /* ignore */
    }
  });

  it("exports query results to a sharded CSV under the wildcard uri", async (t) => {
    if (!GCS_HOST_ROOT) {
      t.skip("BQEMU_GCS_HOST_ROOT not set (run via `make test-e2e-nodejs`)");
      return;
    }

    await client.query(
      `CREATE TABLE \`${PROJECT}.${DATASET}.src\` (id INT64, name STRING)`,
    );
    await client.query(
      `INSERT INTO \`${PROJECT}.${DATASET}.src\` (id, name) ` +
        `VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma')`,
    );

    await client.query(
      `EXPORT DATA OPTIONS (` +
        `uri = 'gs://${BUCKET}/export_nodejs/*.csv', ` +
        `format = 'CSV', overwrite = true) AS ` +
        `SELECT id, name FROM \`${PROJECT}.${DATASET}.src\` ORDER BY id`,
    );

    const shard = path.join(GCS_HOST_ROOT, BUCKET, "export_nodejs", "000000000000.csv");
    assert.ok(fs.existsSync(shard), `expected export shard at ${shard}`);

    const lines = fs
      .readFileSync(shard, "utf8")
      .split("\n")
      .map((l) => l.replace(/\r$/, ""))
      .filter((l) => l.length > 0);
    assert.equal(lines[0], "id,name");
    assert.deepEqual(lines.slice(1), ["1,alpha", "2,beta", "3,gamma"]);
  });
});
