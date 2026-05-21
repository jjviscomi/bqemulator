/**
 * E2E: G1 load Avro + extract Avro against a live bqemulator container
 * via the @google-cloud/bigquery Node.js client.
 *
 * The Makefile target (`test-e2e-nodejs`) bind-mounts a host directory
 * onto `/var/lib/bqemu-gcs` and pre-stages canonical Avro/ORC files via
 * `scripts/stage_g1_e2e_fixtures.py`. We reference them through
 * `gs://g1-e2e/<name>` URIs; the executor's `_resolve_uri` strips the
 * scheme and resolves them under `BQEMU_GCS_LOCAL_ROOT`.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const PROJECT = "e2e-nodejs-g1";
const DATASET = "g1_node_ds";
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

async function postJob(body) {
  const url = `${REST_URL}/bigquery/v2/projects/${PROJECT}/jobs`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ configuration: body }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`POST ${url} -> ${res.status} ${text}`);
  }
  return res.json();
}

async function cleanup(client) {
  try {
    await client.dataset(DATASET).delete({ force: true });
  } catch (_) {
    /* ignore */
  }
}

describe("bqemulator G1 load/extract Avro+ORC (Node.js)", () => {
  let client;
  before(async () => {
    client = makeClient();
    await client.createDataset(DATASET, { location: "US" }).catch(() => {});
  });
  after(async () => {
    await cleanup(client);
  });

  it("loads a 3-row Avro file via jobs.insert", async () => {
    await client.query(
      `CREATE TABLE IF NOT EXISTS \`${PROJECT}.${DATASET}.items_avro\` ` +
        `(id INT64, name STRING)`,
    );

    await postJob({
      load: {
        destinationTable: {
          projectId: PROJECT,
          datasetId: DATASET,
          tableId: "items_avro",
        },
        sourceUris: [`gs://${BUCKET}/load_avro_basic.avro`],
        sourceFormat: "AVRO",
        writeDisposition: "WRITE_TRUNCATE",
      },
    });

    const [rows] = await client.query(
      `SELECT id, name FROM \`${PROJECT}.${DATASET}.items_avro\` ORDER BY id`,
    );
    assert.equal(rows.length, 3);
    assert.equal(rows[0].name, "alpha");
    assert.equal(rows[2].name, "gamma");
  });

  it("round-trips: extract to Avro, then load it back", async () => {
    // Seed src.
    await client.query(
      `CREATE TABLE IF NOT EXISTS \`${PROJECT}.${DATASET}.rt_src\` ` +
        `(id INT64, val STRING)`,
    );
    await client.query(
      `INSERT INTO \`${PROJECT}.${DATASET}.rt_src\` (id, val) ` +
        `VALUES (1, 'alpha'), (2, 'beta')`,
    );
    await client.query(
      `CREATE TABLE IF NOT EXISTS \`${PROJECT}.${DATASET}.rt_dst\` ` +
        `(id INT64, val STRING)`,
    );

    // Extract.
    await postJob({
      extract: {
        sourceTable: {
          projectId: PROJECT,
          datasetId: DATASET,
          tableId: "rt_src",
        },
        destinationUris: [`gs://${BUCKET}/extract_nodejs.avro`],
        destinationFormat: "AVRO",
      },
    });

    // Load the extract back into rt_dst — proves the Avro file is well-formed
    // without needing a Node Avro decoder.
    await postJob({
      load: {
        destinationTable: {
          projectId: PROJECT,
          datasetId: DATASET,
          tableId: "rt_dst",
        },
        sourceUris: [`gs://${BUCKET}/extract_nodejs.avro`],
        sourceFormat: "AVRO",
        writeDisposition: "WRITE_TRUNCATE",
      },
    });

    const [srcRows] = await client.query(
      `SELECT id, val FROM \`${PROJECT}.${DATASET}.rt_src\` ORDER BY id`,
    );
    const [dstRows] = await client.query(
      `SELECT id, val FROM \`${PROJECT}.${DATASET}.rt_dst\` ORDER BY id`,
    );
    assert.deepEqual(
      srcRows.map((r) => ({ id: Number(r.id), val: r.val })),
      dstRows.map((r) => ({ id: Number(r.id), val: r.val })),
    );
  });
});
