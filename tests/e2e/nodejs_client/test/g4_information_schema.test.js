/**
 * E2E: G4 INFORMATION_SCHEMA virtual views against the live container.
 *
 * Two tests: scan INFORMATION_SCHEMA.TABLES filtered by table_type,
 * and scan INFORMATION_SCHEMA.COLUMNS ordered by ordinal_position.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const PROJECT = "e2e-g4-node";
const DATASET = "g4_node_ds";

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

describe("bqemulator G4 INFORMATION_SCHEMA (Node.js)", () => {
  let client;
  before(() => {
    client = makeClient();
  });
  after(async () => {
    await cleanup(client);
  });

  it("lists base tables from INFORMATION_SCHEMA.TABLES", async () => {
    await cleanup(client);
    const [dataset] = await client.createDataset(DATASET);
    await dataset.createTable("orders", {
      schema: [{ name: "id", type: "INT64" }],
    });
    await dataset.createTable("customers", {
      schema: [{ name: "id", type: "INT64" }],
    });

    const [rows] = await client.query({
      query: `SELECT table_name FROM \`${PROJECT}.${DATASET}\`.INFORMATION_SCHEMA.TABLES WHERE table_type = 'BASE TABLE' ORDER BY table_name`,
      location: "US",
    });
    const names = rows.map((r) => r.table_name);
    assert.deepEqual(names, ["customers", "orders"]);
  });

  it("orders columns by ordinal_position", async () => {
    await cleanup(client);
    const [dataset] = await client.createDataset(DATASET);
    await dataset.createTable("events", {
      schema: [
        { name: "id", type: "INT64", mode: "REQUIRED" },
        { name: "ts", type: "TIMESTAMP" },
        { name: "payload", type: "STRING" },
      ],
    });

    const [rows] = await client.query({
      query: `SELECT column_name, data_type FROM \`${PROJECT}.${DATASET}\`.INFORMATION_SCHEMA.COLUMNS WHERE table_name = 'events' ORDER BY ordinal_position`,
      location: "US",
    });
    assert.deepEqual(
      rows.map((r) => r.column_name),
      ["id", "ts", "payload"],
    );
    assert.equal(rows[0].data_type, "INT64");
    assert.equal(rows[1].data_type, "TIMESTAMP");
    assert.equal(rows[2].data_type, "STRING");
  });
});
