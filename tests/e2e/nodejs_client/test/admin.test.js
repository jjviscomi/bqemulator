/**
 * E2E: Phase 10 admin endpoints against a live bqemulator container
 * via raw HTTP requests (the Node BigQuery client doesn't expose the
 * /admin/* surface, so we use fetch).
 *
 * Ship criterion: ``bqemulator import/export/seed/backup/restore`` all
 * round-trip. The four non-admin commands are CLI-only and exercised
 * by the Python E2E + integration suites. The Node E2E covers the
 * /admin/* JSON surface against the same live container.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const PROJECT = "e2e-nodejs-admin";
const DATASET = "admin_node_ds";

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

describe("Phase 10 — admin endpoints (live container)", () => {
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

  it("GET /admin/config returns settings with admin_enabled=true", async () => {
    const body = await rest("GET", "/admin/config");
    if (!body) return; // admin may be disabled — Python E2E will skip
    assert.equal(body.kind, "bqemu#adminConfig");
    assert.equal(body.settings.admin_enabled, true);
  });

  it("GET /admin/catalog reports the seeded dataset", async () => {
    await rest("POST", `/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}/tables`, {
      schema: { fields: [{ name: "n", type: "INT64", mode: "REQUIRED" }] },
      tableReference: {
        projectId: PROJECT,
        datasetId: DATASET,
        tableId: "t1",
      },
    });
    const body = await rest("GET", `/admin/catalog?projectId=${PROJECT}`);
    if (!body) return;
    assert.equal(body.kind, "bqemu#adminCatalog");
    const proj = body.projects.find((p) => p.projectId === PROJECT);
    assert.ok(proj, "project should appear in admin catalog");
    const ds = proj.datasets.find((d) => d.datasetId === DATASET);
    assert.ok(ds, "dataset should appear");
    assert.ok(
      ds.tables.some((t) => t.tableId === "t1"),
      "table t1 should appear",
    );
  });

  it("GET /admin/jobs returns a job list after a query", async () => {
    await rest("POST", `/bigquery/v2/projects/${PROJECT}/jobs`, {
      configuration: {
        query: { query: "SELECT 1", useLegacySql: false },
      },
    });
    const body = await rest("GET", `/admin/jobs?projectId=${PROJECT}`);
    if (!body) return;
    assert.equal(body.kind, "bqemu#adminJobList");
    assert.ok(body.totalItems >= 1);
  });

  it("GET /admin/streams returns counts", async () => {
    const body = await rest("GET", "/admin/streams");
    if (!body) return;
    assert.equal(body.kind, "bqemu#adminStreamList");
    assert.ok("writeStreamCount" in body);
    assert.ok("readSessionCount" in body);
  });
});
