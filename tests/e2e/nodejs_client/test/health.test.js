/**
 * E2E: health endpoints against a live bqemulator container.
 *
 * Run with: BQEMU_REST_URL=http://localhost:9050 npm test
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";

describe("bqemulator health (Node.js)", () => {
  it("GET /healthz returns ok", async () => {
    const res = await fetch(`${REST_URL}/healthz`);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.status, "ok");
  });

  it("GET /readyz returns ok", async () => {
    const res = await fetch(`${REST_URL}/readyz`);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.status, "ok");
  });
});
