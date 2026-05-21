/**
 * E2E: Phase 9 GEOGRAPHY / RANGE / INTERVAL against a live bqemulator
 * container via the @google-cloud/bigquery Node.js client.
 *
 * Ship criterion: queries using ST_DWITHIN, ST_INTERSECTS,
 * RANGE_CONTAINS, and INTERVAL arithmetic return correct results
 * against ghcr.io/jjviscomi/bqemulator:dev.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const PROJECT = "e2e-nodejs-specialized_types";
const DATASET = "specialized_types_node_ds";

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

async function rest(method, path, body) {
  const res = await fetch(REST_URL + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok && method !== "DELETE") {
    throw new Error(`${method} ${path} → ${res.status}: ${await res.text()}`);
  }
  return res.status === 204 ? null : res.json().catch(() => null);
}

describe("Phase 9 — specialized types (live container)", () => {
  const client = makeClient();

  before(async () => {
    await rest("POST", `/bigquery/v2/projects/${PROJECT}/datasets`, {
      datasetReference: { projectId: PROJECT, datasetId: DATASET },
    });
  });

  after(async () => {
    await rest(
      "DELETE",
      `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}?deleteContents=true`,
    );
  });

  it("ST_DWITHIN filters geographic points by radius", async () => {
    const table = "places_dw";
    await rest(
      "POST",
      `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}/tables`,
      {
        schema: {
          fields: [
            { name: "id", type: "INT64", mode: "REQUIRED" },
            { name: "loc", type: "GEOGRAPHY" },
          ],
        },
        tableReference: {
          projectId: PROJECT,
          datasetId: DATASET,
          tableId: table,
        },
      },
    );

    await rest(
      "POST",
      `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}/tables/${table}/insertAll`,
      {
        rows: [
          { json: { id: "1", loc: "POINT(0 0)" } },
          { json: { id: "2", loc: "POINT(3 4)" } },
          { json: { id: "3", loc: "POINT(10 10)" } },
        ],
      },
    );

    const [rows] = await client.query(
      `SELECT id FROM \`${PROJECT}.${DATASET}.${table}\` ` +
        "WHERE ST_DWITHIN(loc, ST_GEOGFROMTEXT('POINT(0 0)'), 600000) ORDER BY id",
    );
    assert.deepEqual(
      rows.map((r) => Number(r.id)),
      [1, 2],
    );
  });

  it("ST_INTERSECTS detects geometric crossings", async () => {
    const table = "shapes_int";
    await rest(
      "POST",
      `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}/tables`,
      {
        schema: {
          fields: [
            { name: "name", type: "STRING", mode: "REQUIRED" },
            { name: "shape", type: "GEOGRAPHY" },
          ],
        },
        tableReference: {
          projectId: PROJECT,
          datasetId: DATASET,
          tableId: table,
        },
      },
    );
    await rest(
      "POST",
      `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}/tables/${table}/insertAll`,
      {
        rows: [
          { json: { name: "crossing", shape: "LINESTRING(0 0, 5 5)" } },
          { json: { name: "vertical", shape: "LINESTRING(2 0, 2 5)" } },
          { json: { name: "far", shape: "LINESTRING(100 100, 200 200)" } },
        ],
      },
    );

    const [rows] = await client.query(
      `SELECT name FROM \`${PROJECT}.${DATASET}.${table}\` ` +
        "WHERE ST_INTERSECTS(shape, ST_GEOGFROMTEXT('LINESTRING(2 0, 2 5)')) " +
        "ORDER BY name",
    );
    assert.deepEqual(
      rows.map((r) => r.name).sort(),
      ["crossing", "vertical"].sort(),
    );
  });

  it("RANGE_CONTAINS evaluates half-open membership", async () => {
    const [rows] = await client.query(
      "SELECT " +
        "RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), " +
        "  DATE '2024-06-15') AS mid, " +
        "RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'), " +
        "  DATE '2024-12-31') AS at_end",
    );
    assert.equal(rows[0].mid, true);
    assert.equal(rows[0].at_end, false);
  });

  it("INTERVAL arithmetic shifts dates and timestamps", async () => {
    const [rows] = await client.query(
      "SELECT DATE '2024-01-15' + INTERVAL 1 DAY AS d_next",
    );
    // BigQuery widens DATE + INTERVAL to TIMESTAMP. The Node client
    // exposes it as either a Date or a BigQueryTimestamp wrapper.
    const v = rows[0].d_next;
    const iso = v.value ? v.value : v.toISOString();
    assert.ok(iso.startsWith("2024-01-16"));
  });

  it("REST schema round-trip preserves GEOGRAPHY / RANGE / INTERVAL", async () => {
    const table = "schema_rt";
    await rest(
      "POST",
      `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}/tables`,
      {
        schema: {
          fields: [
            { name: "g", type: "GEOGRAPHY" },
            { name: "i", type: "INTERVAL" },
            {
              name: "r",
              type: "RANGE",
              rangeElementType: { type: "DATE" },
            },
          ],
        },
        tableReference: {
          projectId: PROJECT,
          datasetId: DATASET,
          tableId: table,
        },
      },
    );
    const got = await rest(
      "GET",
      `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}/tables/${table}`,
    );
    const types = Object.fromEntries(
      got.schema.fields.map((f) => [f.name, f.type]),
    );
    assert.deepEqual(types, {
      g: "GEOGRAPHY",
      i: "INTERVAL",
      r: "RANGE",
    });
    const r = got.schema.fields.find((f) => f.name === "r");
    assert.equal(r.rangeElementType.type, "DATE");
  });
});
