/**
 * E2E: Phase 8 row access policies + authorized views against a live
 * bqemulator container via the @google-cloud/bigquery Node.js client.
 *
 * Exercises the Phase 8 ship criterion:
 *   - A row access policy granting only `user:eu-analyst@example.com`
 *     rows where `region = 'EU'` is enforced.
 *   - Other callers see zero rows.
 *   - An authorized view still enforces RAP (no bypass — see ADR 0018).
 *
 * The X-Bqemu-Caller header is injected into every BigQuery client
 * request via authClient request hooks.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const PROJECT = "e2e-nodejs-row_access";
const DATASET = "row_access_node_ds";
const VIEW_DATASET = "row_access_node_v_ds";

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

  // Inject X-Bqemu-Caller via the BigQuery client's interceptor API.
  // Monkey-patching ``authClient.request`` does NOT work because the
  // BQ client uses ``gaxios``/``teeny-request`` directly for HTTP
  // traffic and only consults the auth client for access tokens.
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

async function rest(client, method, path, body) {
  const url = REST_URL + path;
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok && res.status !== 204) {
    throw new Error(`${method} ${path} -> ${res.status}: ${await res.text()}`);
  }
  return res;
}

async function cleanup() {
  for (const ds of [DATASET, VIEW_DATASET]) {
    try {
      await rest(
        null,
        "DELETE",
        `/bigquery/v2/projects/${PROJECT}/datasets/${ds}?deleteContents=true`,
      );
    } catch (_) {
      /* ignore */
    }
  }
}

describe("bqemulator Phase 8 row access policies (Node.js)", () => {
  let admin;
  before(async () => {
    admin = makeClient(null);
    await cleanup();
    await admin.createDataset(DATASET, { location: "US" }).catch(() => {});
    await admin
      .createDataset(VIEW_DATASET, { location: "US" })
      .catch(() => {});
    await admin.query(
      `CREATE TABLE \`${PROJECT}.${DATASET}.orders\` ` +
        `(id INT64, region STRING)`,
    );
    await admin.query(
      `INSERT INTO \`${PROJECT}.${DATASET}.orders\` ` +
        `VALUES (1, 'EU'), (2, 'EU'), (3, 'US'), (4, 'US')`,
    );
    // Authorized view setup via raw REST (the Node client doesn't
    // expose access_entries cleanly).
    await rest(
      null,
      "POST",
      `/bigquery/v2/projects/${PROJECT}/datasets/${VIEW_DATASET}/tables`,
      {
        tableReference: {
          projectId: PROJECT,
          datasetId: VIEW_DATASET,
          tableId: "all_orders",
        },
        view: {
          query: `SELECT id, region FROM \`${PROJECT}\`.${DATASET}.orders`,
        },
      },
    );
    await rest(
      null,
      "PATCH",
      `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}`,
      {
        access: [
          {
            view: {
              projectId: PROJECT,
              datasetId: VIEW_DATASET,
              tableId: "all_orders",
            },
          },
        ],
      },
    );
    // Create the row access policy.
    await rest(
      null,
      "POST",
      `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}/tables/orders/rowAccessPolicies`,
      {
        rowAccessPolicyReference: {
          projectId: PROJECT,
          datasetId: DATASET,
          tableId: "orders",
          policyId: "eu_only",
        },
        filterPredicate: "region = 'EU'",
        grantees: ["user:eu-analyst@example.com"],
      },
    );
  });
  after(async () => {
    await cleanup();
  });

  it("EU caller sees only EU rows", async () => {
    const eu = makeClient("user:eu-analyst@example.com");
    const [rows] = await eu.query(
      `SELECT id FROM \`${PROJECT}.${DATASET}.orders\` ORDER BY id`,
    );
    assert.deepEqual(
      rows.map((r) => Number(r.id)),
      [1, 2],
    );
  });

  it("Other caller sees zero rows", async () => {
    const other = makeClient("user:other@example.com");
    const [rows] = await other.query(
      `SELECT id FROM \`${PROJECT}.${DATASET}.orders\``,
    );
    assert.deepEqual(rows, []);
  });

  it("Authorized view still enforces RAP — no bypass", async () => {
    // P2.d follow-up #1 (2026-05-18) reversed the ADR 0018 authorized-
    // view bypass after empirical recording proved real BigQuery
    // enforces row-level security UNIVERSALLY through views; integration
    // and conformance fixtures were updated then. This E2E test was
    // missed and updated in P2.d follow-up #2 (2026-05-18).
    const other = makeClient("user:other@example.com");
    const [rows] = await other.query(
      `SELECT id FROM \`${PROJECT}.${VIEW_DATASET}.all_orders\` ORDER BY id`,
    );
    assert.deepEqual(
      rows.map((r) => Number(r.id)),
      [],
    );
  });

  it("INFORMATION_SCHEMA.ROW_ACCESS_POLICIES lists the active policy", async () => {
    // NOTE: backticking only the project segment matches BigQuery's
    // canonical INFORMATION_SCHEMA syntax shape. Backticking the full
    // 4-part path is also valid BigQuery but triggers a SQLGlot
    // tokenizer crash in the current emulator INFORMATION_SCHEMA
    // expander — tracked as a follow-up bug, not P1 scope.
    const [rows] = await admin.query(
      `SELECT policy_name, table_name FROM \`${PROJECT}\`.${DATASET}.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES`,
    );
    const found = rows.some(
      (r) => r.policy_name === "eu_only" && r.table_name === "orders",
    );
    assert.ok(found, "expected eu_only policy in INFORMATION_SCHEMA");
  });
});
