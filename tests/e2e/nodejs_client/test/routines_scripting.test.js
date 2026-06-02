/**
 * E2E: Phase 6 routines + scripting against the live container via the
 * @google-cloud/bigquery Node.js client.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const PROJECT = "e2e-nodejs-routines_scripting";
const DATASET = "routines_scripting_node_ds";

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

async function cleanup(client) {
  try {
    await client.dataset(DATASET).delete({ force: true });
  } catch (_) {
    /* ignore */
  }
}

async function createRoutine(client, body) {
  // The Node.js SDK's routines API uses request() under the hood via
  // dataset().routine(id).create(metadata). The metadata must match
  // the REST wire format exactly.
  const ds = client.dataset(DATASET);
  await ds.routine(body.routineReference.routineId).create(body);
}

describe("bqemulator Phase 6 routines + scripting (Node.js)", () => {
  let client;
  before(async () => {
    client = makeClient();
    await client.createDataset(DATASET, { location: "US" }).catch(() => {});
  });
  after(async () => {
    await cleanup(client);
  });

  it("registers SQL + JS + TVF and runs the ship-criterion script", async () => {
    const ref = (rid) => ({
      projectId: PROJECT,
      datasetId: DATASET,
      routineId: rid,
    });

    await createRoutine(client, {
      routineReference: ref("sql_inc"),
      routineType: "SCALAR_FUNCTION",
      language: "SQL",
      arguments: [{ name: "x", dataType: { typeKind: "INT64" } }],
      returnType: { typeKind: "INT64" },
      definitionBody: "x + 1",
    });

    await createRoutine(client, {
      routineReference: ref("js_double"),
      routineType: "SCALAR_FUNCTION",
      language: "JAVASCRIPT",
      arguments: [{ name: "x", dataType: { typeKind: "INT64" } }],
      returnType: { typeKind: "INT64" },
      definitionBody: "return x * 2;",
    });

    await createRoutine(client, {
      routineReference: ref("one_to_n"),
      routineType: "TABLE_VALUED_FUNCTION",
      language: "SQL",
      arguments: [{ name: "n", dataType: { typeKind: "INT64" } }],
      definitionBody:
        "SELECT i AS value FROM UNNEST(GENERATE_ARRAY(1, n)) AS i",
    });

    const script = `
DECLARE n INT64 DEFAULT 3;
DECLARE total INT64 DEFAULT 0;
BEGIN
  FOR row IN (SELECT value FROM ${DATASET}.one_to_n(n)) DO
    SET total = total + ${DATASET}.js_double(${DATASET}.sql_inc(row.value));
  END FOR;
EXCEPTION WHEN ERROR THEN
  SET total = -1;
END;
IF total > 0 THEN
  SELECT total AS answer;
ELSE
  SELECT -1 AS answer;
END IF;
`;
    const [rows] = await client.query(script);
    assert.equal(rows[0].answer, 18);
  });

  it("INFORMATION_SCHEMA.ROUTINES lists everything created", async () => {
    const sql = `SELECT routine_name FROM \`${PROJECT}\`.${DATASET}.INFORMATION_SCHEMA.ROUTINES ORDER BY routine_name`;
    const [rows] = await client.query(sql);
    const names = rows.map((r) => r.routine_name);
    assert.deepEqual(names, ["js_double", "one_to_n", "sql_inc"]);
  });

  it("registers a CREATE SCHEMA created inside a multi-statement script", async () => {
    // A single-statement CREATE SCHEMA takes the executor fast path; the
    // trailing SELECT tips this job into the scripting interpreter, whose
    // DDL-sync hook must register the dataset so it surfaces via
    // datasets.list and datasets.get.
    const scriptedDs = "scripted_created_schema_node_ds";
    // Guard against a stale dataset left by an interrupted run.
    await client
      .dataset(scriptedDs)
      .delete({ force: true })
      .catch(() => {});
    try {
      await client.query(`CREATE SCHEMA \`${scriptedDs}\`;\nSELECT 1 AS n;`);

      const [datasets] = await client.getDatasets();
      const ids = datasets.map((d) => d.id);
      assert.ok(
        ids.includes(scriptedDs),
        `dataset ${scriptedDs} absent from datasets.list after scripted CREATE SCHEMA`,
      );

      const [exists] = await client.dataset(scriptedDs).exists();
      assert.ok(exists, `datasets.get failed for ${scriptedDs}`);
    } finally {
      await client
        .dataset(scriptedDs)
        .delete({ force: true })
        .catch(() => {});
    }
  });

  it("returns an empty result for a script ending in DDL", async () => {
    // Last-statement-wins: a trailing DDL has no result set, so the prior
    // SELECT's rows must not leak into the script result.
    const ds = "script_result_ddl_last_node";
    await client
      .dataset(ds)
      .delete({ force: true })
      .catch(() => {});
    await client.createDataset(ds, { location: "US" });
    try {
      const script = `SELECT 1 AS a;\nCREATE TABLE \`${PROJECT}.${ds}.trailing\` (id INT64)`;
      const [rows] = await client.query(script);
      assert.equal(rows.length, 0);
    } finally {
      await client
        .dataset(ds)
        .delete({ force: true })
        .catch(() => {});
    }
  });
});
