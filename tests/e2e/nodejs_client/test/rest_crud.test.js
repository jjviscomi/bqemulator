/**
 * E2E: Phase 1 REST CRUD + query against the live container via the
 * @google-cloud/bigquery Node.js client.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const PROJECT = "e2e-nodejs";
const DATASET = "e2e_node";
const TABLE = "customers";

function makeClient() {
  const { BigQuery } = require("@google-cloud/bigquery");
  const { OAuth2Client } = require("google-auth-library");

  // The Node.js BigQuery client doesn't honor any ``EMULATOR_HOST``
  // variable, so we supply an OAuth2Client pre-seeded with a dummy
  // access token. The emulator never checks the token — but the
  // client library will refuse to run without one.
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

describe("bqemulator Phase 1 REST (Node.js)", () => {
  let client;
  before(() => {
    client = makeClient();
  });
  after(async () => {
    await cleanup(client);
  });

  it("creates dataset + table + inserts + queries", async () => {
    // Setup fresh state each run.
    await cleanup(client);

    const [dataset] = await client.createDataset(DATASET);
    assert.equal(dataset.id, DATASET);

    const schema = [
      { name: "id", type: "INT64", mode: "REQUIRED" },
      { name: "name", type: "STRING" },
    ];
    const [table] = await dataset.createTable(TABLE, { schema });
    assert.equal(table.id, TABLE);

    const rows = [
      { id: 1, name: "Alice" },
      { id: 2, name: "Bob" },
    ];
    await table.insert(rows);

    const [queryRows] = await client.query({
      query: `SELECT COUNT(*) AS n FROM \`${PROJECT}.${DATASET}.${TABLE}\``,
      location: "US",
    });
    assert.equal(Number(queryRows[0].n), 2);
  });

  it("drops a table via query and removes it from the catalog", async () => {
    const dropDs = "e2e_node_drop";
    try {
      await client.dataset(dropDs).delete({ force: true });
    } catch (_) {
      /* ignore */
    }
    const [dataset] = await client.createDataset(dropDs);
    const [table] = await dataset.createTable(TABLE, {
      schema: [{ name: "id", type: "INT64" }],
    });

    // Visible before the drop.
    const [existsBefore] = await table.exists();
    assert.equal(existsBefore, true);

    await client.query({
      query: `DROP TABLE \`${PROJECT}.${dropDs}.${TABLE}\``,
      location: "US",
    });

    // Gone from tables.get and tables.list, matching BigQuery.
    const [existsAfter] = await table.exists();
    assert.equal(existsAfter, false);
    const [tables] = await dataset.getTables();
    assert.ok(!tables.some((t) => t.id === TABLE));

    try {
      await client.dataset(dropDs).delete({ force: true });
    } catch (_) {
      /* ignore */
    }
  });

  it("returns BigQuery's result shape for single-statement DDL", async () => {
    // CREATE TABLE returns the declared schema with zero rows (not a
    // status row); CTAS returns the SELECT's schema with zero rows;
    // DROP TABLE returns a fully empty result. Pinned by the
    // rest_crud/ddl_result_* conformance corpus recorded from real
    // BigQuery.
    const ddlDs = "e2e_node_ddl_result";
    await client
      .dataset(ddlDs)
      .delete({ force: true })
      .catch(() => {});
    await client.createDataset(ddlDs, { location: "US" });
    try {
      const [createJob] = await client.createQueryJob({
        query: `CREATE TABLE \`${PROJECT}.${ddlDs}.t\` (id INT64, name STRING)`,
        location: "US",
      });
      const [createRows, , createResp] = await createJob.getQueryResults();
      assert.equal(createRows.length, 0);
      const createFields = (createResp.schema && createResp.schema.fields) || [];
      assert.deepEqual(
        createFields.map((f) => [f.name, f.type]),
        [
          ["id", "INTEGER"],
          ["name", "STRING"],
        ],
      );
      const [createMeta] = await createJob.getMetadata();
      assert.equal(createMeta.statistics.query.ddlOperationPerformed, "CREATE");

      const [ctasRows] = await client.query({
        query: `CREATE TABLE \`${PROJECT}.${ddlDs}.t2\` AS SELECT 1 AS id, 'x' AS nm`,
        location: "US",
      });
      assert.equal(ctasRows.length, 0);

      const [dropJob] = await client.createQueryJob({
        query: `DROP TABLE \`${PROJECT}.${ddlDs}.t\``,
        location: "US",
      });
      const [dropRows, , dropResp] = await dropJob.getQueryResults();
      assert.equal(dropRows.length, 0);
      const dropFields = (dropResp.schema && dropResp.schema.fields) || [];
      assert.equal(dropFields.length, 0);
    } finally {
      await client
        .dataset(ddlDs)
        .delete({ force: true })
        .catch(() => {});
    }
  });
});
