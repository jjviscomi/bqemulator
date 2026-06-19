/**
 * E2E: G2 upload-host endpoints (multipart + resumable) against a live
 * bqemulator container via the @google-cloud/bigquery Node.js client.
 *
 * The Node client's `table.load(streamOrFile)` API auto-selects
 * multipart vs resumable upload based on the payload size. Both
 * branches exercise `/upload/bigquery/v2/projects/{p}/jobs` rather
 * than the data-plane `/jobs` endpoint.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { Readable } = require("node:stream");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const PROJECT = "e2e-nodejs-g2";
const DATASET = "g2_node_ds";

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

describe("bqemulator G2 upload host (Node.js)", () => {
  let client;
  before(async () => {
    client = makeClient();
    await client.createDataset(DATASET, { location: "US" }).catch(() => {});
  });
  after(async () => {
    await cleanup(client);
  });

  it("loads a small CSV via multipart upload", async () => {
    await client.query(
      `CREATE TABLE IF NOT EXISTS \`${PROJECT}.${DATASET}.rows_mp\` ` +
        `(id INT64, name STRING)`,
    );

    const csv = Buffer.from("id,name\n1,alice\n2,bob\n3,carol\n", "utf-8");

    // @google-cloud/bigquery@7 requires a File object for table.load(),
    // not a Buffer / Readable. Use createWriteStream() to pipe arbitrary
    // bytes into the upload host — this exercises the same multipart
    // endpoint without depending on @google-cloud/storage.
    const writeStream = client
      .dataset(DATASET)
      .table("rows_mp")
      .createWriteStream({
        sourceFormat: "CSV",
        skipLeadingRows: 1,
        writeDisposition: "WRITE_TRUNCATE",
        schema: {
          fields: [
            { name: "id", type: "INTEGER" },
            { name: "name", type: "STRING" },
          ],
        },
      });
    // The "job" event fires with the Job once the upload is accepted;
    // "complete" fires when the upload stream is fully written. After
    // both fire, poll the Job's metadata for terminal state — the
    // upload-host endpoint synchronously transitions to DONE on the
    // emulator, so a single getMetadata() round-trip is enough.
    const job = await new Promise((resolve, reject) => {
      let receivedJob = null;
      writeStream.on("job", (j) => {
        receivedJob = j;
      });
      writeStream.on("complete", () => resolve(receivedJob));
      writeStream.on("error", reject);
      Readable.from(csv).pipe(writeStream);
    });
    const [meta] = await job.getMetadata();
    assert.equal(meta.status.state, "DONE");

    const [rows] = await client.query(
      `SELECT COUNT(*) AS n FROM \`${PROJECT}.${DATASET}.rows_mp\``,
    );
    assert.equal(Number(rows[0].n), 3);
  });

  it("loads a larger NDJSON via resumable upload", async () => {
    await client.query(
      `CREATE TABLE IF NOT EXISTS \`${PROJECT}.${DATASET}.rows_rs\` ` +
        `(id INT64, name STRING)`,
    );

    // Synthesize ~2 MiB of NDJSON to push the client into resumable mode.
    const lines = [];
    for (let i = 0; i < 60_000; i += 1) {
      lines.push(JSON.stringify({ id: i, name: `name-${i}` }));
    }
    const ndjson = Buffer.from(lines.join("\n") + "\n", "utf-8");
    assert.ok(ndjson.length > 1_000_000);

    // See the multipart test above for why we use createWriteStream
    // instead of table.load(stream, ...) — v7 of the BigQuery client
    // requires a @google-cloud/storage File object for direct load().
    const writeStream = client
      .dataset(DATASET)
      .table("rows_rs")
      .createWriteStream({
        sourceFormat: "NEWLINE_DELIMITED_JSON",
        writeDisposition: "WRITE_TRUNCATE",
        schema: {
          fields: [
            { name: "id", type: "INTEGER" },
            { name: "name", type: "STRING" },
          ],
        },
      });
    const job = await new Promise((resolve, reject) => {
      let receivedJob = null;
      writeStream.on("job", (j) => {
        receivedJob = j;
      });
      writeStream.on("complete", () => resolve(receivedJob));
      writeStream.on("error", reject);
      Readable.from(ndjson).pipe(writeStream);
    });
    const [meta] = await job.getMetadata();
    assert.equal(meta.status.state, "DONE");

    const [rows] = await client.query(
      `SELECT COUNT(*) AS n FROM \`${PROJECT}.${DATASET}.rows_rs\``,
    );
    assert.equal(Number(rows[0].n), 60_000);
  });
  it("should honor autodetect flag for CSV load", async () => {
    const tableId = "rows_autodetect";
    const csvData = Buffer.from("id,name,score\n1,alice,99.5\n2,bob,88.2\n", "utf-8");

    const writeStream = client
      .dataset(DATASET)
      .table(tableId)
      .createWriteStream({
        sourceFormat: "CSV",
        skipLeadingRows: 1,
        autodetect: true,
        writeDisposition: "WRITE_TRUNCATE",
        createDisposition: "CREATE_IF_NEEDED",
      });

    const job = await new Promise((resolve, reject) => {
      let receivedJob = null;
      writeStream.on("job", (j) => {
        receivedJob = j;
      });
      writeStream.on("complete", () => resolve(receivedJob));
      writeStream.on("error", reject);
      Readable.from(csvData).pipe(writeStream);
    });

    const [meta] = await job.getMetadata();
    assert.equal(meta.status.state, "DONE");

    const [rows] = await client.query(
      `SELECT COUNT(*) AS n FROM \`${PROJECT}.${DATASET}.${tableId}\``
    );
    assert.equal(Number(rows[0].n), 2);
  });
});
