/**
 * E2E: ``SESSION_USER()`` inside a RAP filter predicate (ADR 0038),
 * exercised through the official @google-cloud/bigquery Node.js
 * client.
 *
 * The canonical "tenant isolation by email domain" pattern:
 *   - Seed a ``tenants`` table with rows for two domains.
 *   - Create a RAP filter
 *     ``REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = tenant_id`` granted
 *     to ``allAuthenticatedUsers``.
 *   - Each caller sees only their own tenant's rows.
 *
 * The ``X-Bqemu-Caller`` header is injected per-client via the
 * BigQuery client's interceptor API (same pattern as
 * ``row_access.test.js``).
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const PROJECT = "e2e-nodejs-row_access_session_user";
const DATASET = "session_user_node_ds";

function makeClient(callerHeader) {
  const { BigQuery } = require("@google-cloud/bigquery");
  const { OAuth2Client } = require("google-auth-library");
  const fake = new OAuth2Client();
  fake.credentials = { access_token: "anonymous" };
  const client = new BigQuery({
    projectId: PROJECT,
    apiEndpoint: REST_URL,
    authClient: fake,
    autoRetry: false,
  });
  if (callerHeader) {
    client.interceptors.push({
      request: (reqOpts) => ({
        ...reqOpts,
        headers: {
          ...(reqOpts.headers || {}),
          "X-Bqemu-Caller": callerHeader,
        },
      }),
    });
  }
  return client;
}

// Cap REST calls at 15s so a stalled emulator can't hang the test
// run indefinitely (CodeRabbit thread PRRT_kwDOSkfuJ86EVwPB).
const REST_TIMEOUT_MS = 15_000;

async function rest(method, path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REST_TIMEOUT_MS);
  try {
    const res = await fetch(REST_URL + path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    if (!res.ok && res.status !== 204) {
      throw new Error(`${method} ${path} -> ${res.status}: ${await res.text()}`);
    }
    return res;
  } finally {
    clearTimeout(timer);
  }
}

async function cleanup() {
  try {
    await rest(
      "DELETE",
      `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}?deleteContents=true`,
    );
  } catch (_) {
    /* ignore */
  }
}

describe("bqemulator RAP via SESSION_USER (Node.js)", () => {
  let admin;
  before(async () => {
    admin = makeClient(null);
    await cleanup();
    await admin.createDataset(DATASET, { location: "US" }).catch(() => {});
    await admin.query(
      `CREATE TABLE \`${PROJECT}.${DATASET}.tenants\` ` +
        `(id INT64, tenant_id STRING)`,
    );
    await admin.query(
      `INSERT INTO \`${PROJECT}.${DATASET}.tenants\` ` +
        `VALUES (1, 'example.com'), (2, 'example.com'), ` +
        `(3, 'other.com'), (4, 'other.com')`,
    );
    await rest(
      "POST",
      `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}` +
        `/tables/tenants/rowAccessPolicies`,
      {
        rowAccessPolicyReference: {
          projectId: PROJECT,
          datasetId: DATASET,
          tableId: "tenants",
          policyId: "tenant_by_session_user",
        },
        filterPredicate:
          "REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = tenant_id",
        grantees: ["allAuthenticatedUsers"],
      },
    );
  });

  after(async () => {
    await cleanup();
  });

  it("@example.com caller sees only example.com rows", async () => {
    const eu = makeClient("user:alice@example.com");
    const [rows] = await eu.query(
      `SELECT id FROM \`${PROJECT}.${DATASET}.tenants\` ORDER BY id`,
    );
    assert.deepEqual(
      rows.map((r) => Number(r.id)),
      [1, 2],
    );
  });

  it("@other.com caller sees only other.com rows", async () => {
    const other = makeClient("user:bob@other.com");
    const [rows] = await other.query(
      `SELECT id FROM \`${PROJECT}.${DATASET}.tenants\` ORDER BY id`,
    );
    assert.deepEqual(
      rows.map((r) => Number(r.id)),
      [3, 4],
    );
  });

  it("SELECT SESSION_USER() returns the caller's bare email", async () => {
    const claire = makeClient("user:claire@example.com");
    const [rows] = await claire.query("SELECT SESSION_USER() AS who");
    assert.equal(rows[0].who, "claire@example.com");
  });
});
