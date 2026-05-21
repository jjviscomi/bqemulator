/**
 * E2E: Phase 7 versioning against a live bqemulator container via the
 * @google-cloud/bigquery Node.js client. Exercises:
 *   - FOR SYSTEM_TIME AS OF time travel
 *   - CREATE SNAPSHOT TABLE
 *   - CREATE TABLE ... CLONE
 *   - CREATE MATERIALIZED VIEW + auto-refresh
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const PROJECT = "e2e-nodejs-versioning";
const DATASET = "versioning_node_ds";

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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

describe("bqemulator Phase 7 versioning (Node.js)", () => {
  let client;
  before(async () => {
    client = makeClient();
    await client.createDataset(DATASET, { location: "US" }).catch(() => {});
    await client.query(
      `CREATE TABLE IF NOT EXISTS \`${PROJECT}.${DATASET}.orders\` ` +
        `(id INT64, country STRING, amount INT64)`,
    );
  });
  after(async () => {
    await cleanup(client);
  });

  it("FOR SYSTEM_TIME AS OF returns the pre-change rows", async () => {
    await client.query(
      `INSERT INTO \`${PROJECT}.${DATASET}.orders\` ` +
        `VALUES (1, 'US', 10), (2, 'US', 20)`,
    );

    await sleep(50);
    const boundary = new Date()
      .toISOString()
      .replace("T", " ")
      .replace("Z", "");
    await sleep(50);

    await client.query(
      `INSERT INTO \`${PROJECT}.${DATASET}.orders\` VALUES (3, 'CA', 30)`,
    );

    const [historical] = await client.query(
      `SELECT id FROM \`${PROJECT}.${DATASET}.orders\` ` +
        `FOR SYSTEM_TIME AS OF TIMESTAMP '${boundary}' ORDER BY id`,
    );
    assert.deepEqual(
      historical.map((r) => r.id),
      [1, 2],
    );
  });

  it("CREATE SNAPSHOT TABLE captures an immutable copy", async () => {
    await client.query(
      `CREATE SNAPSHOT TABLE \`${PROJECT}.${DATASET}.orders_snap\` ` +
        `CLONE \`${PROJECT}.${DATASET}.orders\``,
    );
    await client.query(
      `INSERT INTO \`${PROJECT}.${DATASET}.orders\` VALUES (4, 'NZ', 40)`,
    );
    const [snap] = await client.query(
      `SELECT id FROM \`${PROJECT}.${DATASET}.orders_snap\` ORDER BY id`,
    );
    const ids = snap.map((r) => r.id);
    assert.ok(!ids.includes(4));
  });

  it("CREATE TABLE ... CLONE diverges independently", async () => {
    await client.query(
      `CREATE TABLE \`${PROJECT}.${DATASET}.workcopy\` ` +
        `CLONE \`${PROJECT}.${DATASET}.orders\``,
    );
    await client.query(
      `INSERT INTO \`${PROJECT}.${DATASET}.workcopy\` VALUES (99, 'NZ', 999)`,
    );
    const [src] = await client.query(
      `SELECT id FROM \`${PROJECT}.${DATASET}.orders\` WHERE id = 99`,
    );
    const [clone] = await client.query(
      `SELECT id FROM \`${PROJECT}.${DATASET}.workcopy\` WHERE id = 99`,
    );
    assert.equal(src.length, 0);
    assert.equal(clone.length, 1);
  });

  it("CREATE MATERIALIZED VIEW auto-refreshes on base-table change", async () => {
    await client.query(
      `CREATE MATERIALIZED VIEW \`${PROJECT}.${DATASET}.country_totals\` AS ` +
        `SELECT country, SUM(amount) AS total ` +
        `FROM \`${PROJECT}.${DATASET}.orders\` GROUP BY country`,
    );
    const [before] = await client.query(
      `SELECT total FROM \`${PROJECT}.${DATASET}.country_totals\` ` +
        `WHERE country = 'US'`,
    );
    await client.query(
      `INSERT INTO \`${PROJECT}.${DATASET}.orders\` VALUES (5, 'US', 100)`,
    );
    const [after] = await client.query(
      `SELECT total FROM \`${PROJECT}.${DATASET}.country_totals\` ` +
        `WHERE country = 'US'`,
    );
    assert.notEqual(before[0].total, after[0].total);
  });
});
