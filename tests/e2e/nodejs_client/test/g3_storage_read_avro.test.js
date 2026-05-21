/**
 * E2E: G3 Storage Read API Avro wire format against the live container.
 *
 * Mirrors the storage_read Arrow test but requests AVRO instead. Decodes
 * row bytes via `avsc` (a canonical Node Avro implementation) and
 * asserts the decoded values equal the seed data — proves the
 * emulator's bytes are real Avro, not just proto-valid noise.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const GRPC_ENDPOINT =
  process.env.BQEMU_GRPC_ENDPOINT || "localhost:9060";
const PROJECT = "e2e-nodejs-g3";
const DATASET = "g3_node_ds";
const TABLE = "avro_rows";

function makeBqClient() {
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

async function seed() {
  const client = makeBqClient();
  try {
    await client.dataset(DATASET).delete({ force: true });
  } catch (_) {
    /* ignore */
  }
  const [dataset] = await client.createDataset(DATASET);
  const schema = [
    { name: "id", type: "INT64", mode: "REQUIRED" },
    { name: "name", type: "STRING" },
    { name: "score", type: "INT64" },
  ];
  const [table] = await dataset.createTable(TABLE, { schema });
  await table.insert([
    { id: 1, name: "Alice", score: 90 },
    { id: 2, name: "Bob", score: 70 },
    { id: 3, name: "Carol", score: 85 },
  ]);
}

async function cleanup() {
  try {
    const client = makeBqClient();
    await client.dataset(DATASET).delete({ force: true });
  } catch (_) {
    /* ignore */
  }
}

// --- proto wire helpers (lifted from storage_read.test.js) -----------------------

function varintTag(fieldNumber, wireType) {
  return (fieldNumber << 3) | wireType;
}
function encodeVarint(value) {
  const bytes = [];
  while (value > 0x7f) {
    bytes.push((value & 0x7f) | 0x80);
    value = Math.floor(value / 128);
  }
  bytes.push(value & 0x7f);
  return Buffer.from(bytes);
}
function encodeField(buf) {
  return Buffer.concat([encodeVarint(buf.length), buf]);
}
function encodeLengthDelim(fieldNumber, payload) {
  return Buffer.concat([
    Buffer.from([varintTag(fieldNumber, 2)]),
    encodeField(payload),
  ]);
}
function encodeVarintField(fieldNumber, value) {
  return Buffer.concat([
    Buffer.from([varintTag(fieldNumber, 0)]),
    encodeVarint(value),
  ]);
}
function encodeString(fieldNumber, str) {
  return encodeLengthDelim(fieldNumber, Buffer.from(str, "utf8"));
}

// AVRO = 1 (vs ARROW = 2).
function encodeCreateReadSessionRequestAvro(parent, tablePath) {
  const readSession = Buffer.concat([
    encodeVarintField(3, 1 /* DataFormat.AVRO */),
    encodeString(6, tablePath),
  ]);
  return Buffer.concat([
    encodeString(1, parent),
    encodeLengthDelim(2, readSession),
    encodeVarintField(3, 1 /* max_stream_count */),
  ]);
}

function encodeReadRowsRequest(streamName) {
  return Buffer.concat([
    encodeString(1, streamName),
    encodeVarintField(2, 0 /* offset */),
  ]);
}

function* iterFields(buf) {
  let i = 0;
  while (i < buf.length) {
    const tag = buf[i++];
    const fn = tag >> 3;
    const wt = tag & 0x7;
    if (wt === 2) {
      let len = 0,
        shift = 0;
      while (true) {
        const b = buf[i++];
        len |= (b & 0x7f) << shift;
        if ((b & 0x80) === 0) break;
        shift += 7;
      }
      yield [fn, wt, buf.slice(i, i + len)];
      i += len;
    } else if (wt === 0) {
      let v = 0,
        s = 0;
      while (true) {
        const b = buf[i++];
        v |= (b & 0x7f) << s;
        if ((b & 0x80) === 0) break;
        s += 7;
      }
      yield [fn, wt, v];
    } else {
      throw new Error(`unsupported wire type ${wt} for field ${fn}`);
    }
  }
}

// ReadSession proto: streams=10 (repeated), avro_schema=4 (record) per the
// canonical google/cloud/bigquery/storage/v1/stream.proto. (Field 8 is
// ``read_options``, not avro_schema — earlier comment here was wrong.)
function decodeReadSessionAvro(buf) {
  const out = { streamNames: [], avroSchema: null };
  for (const [fn, wt, payload] of iterFields(buf)) {
    if (fn === 10 && wt === 2) {
      for (const [innerFn, innerWt, innerPayload] of iterFields(payload)) {
        if (innerFn === 1 && innerWt === 2) {
          out.streamNames.push(innerPayload.toString("utf8"));
        }
      }
    } else if (fn === 4 && wt === 2) {
      // AvroSchema.schema = 1 (string).
      for (const [innerFn, innerWt, innerPayload] of iterFields(payload)) {
        if (innerFn === 1 && innerWt === 2) {
          out.avroSchema = innerPayload.toString("utf8");
        }
      }
    }
  }
  return out;
}

// ReadRowsResponse: avro_rows=3 (AvroRows), row_count=6 (int64).
// AvroRows.serialized_binary_rows=1 (bytes).
function decodeReadRowsAvro(buf) {
  const out = { rowBytes: null, rowCount: 0 };
  for (const [fn, wt, payload] of iterFields(buf)) {
    if (fn === 3 && wt === 2) {
      for (const [innerFn, innerWt, innerPayload] of iterFields(payload)) {
        if (innerFn === 1 && innerWt === 2) {
          out.rowBytes = innerPayload;
        }
      }
    } else if (fn === 6 && wt === 0) {
      out.rowCount = payload;
    }
  }
  return out;
}

describe("bqemulator G3 Storage Read Avro (Node.js)", () => {
  before(async () => {
    await seed();
  });
  after(async () => {
    await cleanup();
  });

  it("decodes Avro rows via avsc and asserts decoded equality", async () => {
    const grpc = require("@grpc/grpc-js");
    const avsc = require("avsc");

    const channel = new grpc.Client(
      GRPC_ENDPOINT,
      grpc.credentials.createInsecure(),
    );
    try {
      const tablePath =
        `projects/${PROJECT}/datasets/${DATASET}/tables/${TABLE}`;
      const sessionBytes = await new Promise((resolve, reject) => {
        channel.makeUnaryRequest(
          "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
          (x) => x,
          (x) => x,
          encodeCreateReadSessionRequestAvro(`projects/${PROJECT}`, tablePath),
          (err, value) => (err ? reject(err) : resolve(value)),
        );
      });
      const { streamNames, avroSchema } = decodeReadSessionAvro(sessionBytes);
      assert.ok(streamNames.length >= 1, "no streams returned");
      assert.ok(avroSchema, "session missing avro_schema");
      const type = avsc.Type.forSchema(JSON.parse(avroSchema));

      const decodedRows = [];
      await new Promise((resolve, reject) => {
        const stream = channel.makeServerStreamRequest(
          "/google.cloud.bigquery.storage.v1.BigQueryRead/ReadRows",
          (x) => x,
          (x) => x,
          encodeReadRowsRequest(streamNames[0]),
          new grpc.Metadata(),
        );
        stream.on("data", (buf) => {
          const { rowBytes, rowCount } = decodeReadRowsAvro(buf);
          if (!rowBytes || rowBytes.length === 0) return;
          // Naked rows — decode rowCount records back-to-back.
          let offset = 0;
          for (let i = 0; i < rowCount; i++) {
            const { value, offset: nextOffset } = type.decode(rowBytes, offset);
            assert.ok(value, "avsc decode returned no value");
            decodedRows.push(value);
            offset = nextOffset;
          }
        });
        stream.on("end", resolve);
        stream.on("error", reject);
      });

      assert.equal(decodedRows.length, 3, "expected 3 decoded Avro rows");
      const byId = Object.fromEntries(
        decodedRows.map((r) => [Number(r.id), r]),
      );
      assert.equal(byId[1].name, "Alice");
      assert.equal(Number(byId[1].score), 90);
      assert.equal(byId[3].name, "Carol");
    } finally {
      channel.close();
    }
  });
});
