/**
 * E2E: Phase 4 Storage Read API against the live container.
 *
 * Uses raw @grpc/grpc-js plus hand-encoded protobuf wire bytes so the
 * test doesn't depend on the BigQuery Storage client library's
 * auth/transport layer. Arrow record batches are decoded with the
 * bundled apache-arrow IPC stream reader. Mirrors the Python and Go
 * storage_read ship-criterion: seed three rows via REST, open a session
 * over gRPC, stream ReadRows, and assert all three rows round-trip.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const GRPC_ENDPOINT =
  process.env.BQEMU_GRPC_ENDPOINT || "localhost:9060";
const PROJECT = "e2e-nodejs-storage_read";
const DATASET = "storage_read_node_ds";
const TABLE = "places";

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

// --- hand-crafted proto wire helpers (same shape as storage_write.test.js) -------

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

// CreateReadSessionRequest:
//   parent=1 string, read_session=2 ReadSession, max_stream_count=3 int32
// ReadSession (only the input-side fields):
//   table=6 string, data_format=3 enum (ARROW=2)
function encodeCreateReadSessionRequest(parent, tablePath) {
  const readSession = Buffer.concat([
    encodeVarintField(3, 2 /* DataFormat.ARROW */),
    encodeString(6, tablePath),
  ]);
  return Buffer.concat([
    encodeString(1, parent),
    encodeLengthDelim(2, readSession),
    encodeVarintField(3, 1 /* max_stream_count */),
  ]);
}

// ReadRowsRequest: read_stream=1 string, offset=2 int64
function encodeReadRowsRequest(streamName) {
  return Buffer.concat([
    encodeString(1, streamName),
    encodeVarintField(2, 0 /* offset */),
  ]);
}

// Iterate the top-level fields of a proto message — yields
// [fieldNumber, wireType, payloadBufferOrVarintValue].
function* iterFields(buf) {
  let i = 0;
  while (i < buf.length) {
    const tag = buf[i++];
    const fn = tag >> 3;
    const wt = tag & 0x7;
    if (wt === 2) {
      let len = 0;
      let shift = 0;
      while (true) {
        const b = buf[i++];
        len |= (b & 0x7f) << shift;
        if ((b & 0x80) === 0) break;
        shift += 7;
      }
      yield [fn, wt, buf.slice(i, i + len)];
      i += len;
    } else if (wt === 0) {
      let v = 0;
      let s = 0;
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

// ReadSession response: streams=10 (repeated ReadStream). ReadStream.name=1.
function decodeReadSessionStreams(buf) {
  const names = [];
  for (const [fn, wt, payload] of iterFields(buf)) {
    if (fn === 10 && wt === 2) {
      for (const [innerFn, innerWt, innerPayload] of iterFields(payload)) {
        if (innerFn === 1 && innerWt === 2) {
          names.push(innerPayload.toString("utf8"));
        }
      }
    }
  }
  return names;
}

// ReadSession.arrow_schema=5 (ArrowSchema, in the ``oneof schema`` block).
// ArrowSchema.serialized_schema=1 (bytes).
function decodeReadSessionArrowSchema(buf) {
  for (const [fn, wt, payload] of iterFields(buf)) {
    if (fn === 5 && wt === 2) {
      for (const [innerFn, innerWt, innerPayload] of iterFields(payload)) {
        if (innerFn === 1 && innerWt === 2) {
          return innerPayload;
        }
      }
    }
  }
  return null;
}

// ReadRowsResponse: arrow_record_batch=4 (ArrowRecordBatch).
// ArrowRecordBatch.serialized_record_batch=1 (bytes).
function decodeReadRowsBatch(buf) {
  for (const [fn, wt, payload] of iterFields(buf)) {
    if (fn === 4 && wt === 2) {
      for (const [innerFn, innerWt, innerPayload] of iterFields(payload)) {
        if (innerFn === 1 && innerWt === 2) {
          return innerPayload;
        }
      }
    }
  }
  return null;
}

describe("bqemulator Phase 4 Storage Read (Node.js via gRPC + Arrow IPC)", () => {
  before(async () => {
    await seed();
  });
  after(async () => {
    await cleanup();
  });

  it("creates a read session and streams Arrow record batches", async () => {
    const grpc = require("@grpc/grpc-js");
    const apacheArrow = require("apache-arrow");

    const channel = new grpc.Client(
      GRPC_ENDPOINT,
      grpc.credentials.createInsecure(),
    );

    try {
      // CreateReadSession.
      const tablePath =
        `projects/${PROJECT}/datasets/${DATASET}/tables/${TABLE}`;
      const sessionBytes = await new Promise((resolve, reject) => {
        channel.makeUnaryRequest(
          "/google.cloud.bigquery.storage.v1.BigQueryRead/CreateReadSession",
          (x) => x,
          (x) => x,
          encodeCreateReadSessionRequest(`projects/${PROJECT}`, tablePath),
          (err, value) => (err ? reject(err) : resolve(value)),
        );
      });
      const streamNames = decodeReadSessionStreams(sessionBytes);
      assert.ok(
        streamNames.length >= 1,
        "CreateReadSession returned no streams",
      );
      const schemaBytes = decodeReadSessionArrowSchema(sessionBytes);
      // Validate length BEFORE slicing the trailing EOS marker (8
      // bytes — 0xFFFFFFFF continuation + 0x00000000 zero-length).
      // The Go and Java tests do the same. A truthy-but-too-short
      // ``schemaBytes`` would silently produce a garbage
      // ``schemaOnly`` and the test would fail downstream with an
      // opaque Arrow parse error.
      assert.ok(
        schemaBytes && schemaBytes.length > 8,
        "CreateReadSession returned no arrow_schema or schema is malformed (need > 8 bytes for EOS marker stripping)",
      );

      // ReadRows on the first stream.
      //
      // The emulator emits ``serialized_record_batch`` as a BARE Arrow
      // IPC record-batch message (no schema-message prefix, no EOS
      // marker) — that's the BigQuery contract; v1.0.0 was wrong and
      // packed a full stream, fixed in #15. The schema travels on the
      // session's ``arrow_schema.serialized_schema`` field (which we
      // emit as a one-message stream with trailing EOS — same shape
      // pyarrow's ``new_stream(schema).close()`` produces).
      //
      // ``apache-arrow`` JS's ``RecordBatchReader.from`` parses a full
      // stream, not a bare message. To consume the new format with
      // the stock JS API, synthesise a single-batch stream by
      // concatenating the schema-stream bytes (minus their trailing
      // EOS) + the batch message + an EOS marker.
      const EOS_MARKER = Buffer.from([
        0xff, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00,
      ]);
      // ``schemaBytes`` is ``[schema-msg][EOS]`` — strip the EOS.
      const schemaOnly = schemaBytes.slice(0, schemaBytes.length - 8);

      let total = 0;
      await new Promise((resolve, reject) => {
        const stream = channel.makeServerStreamRequest(
          "/google.cloud.bigquery.storage.v1.BigQueryRead/ReadRows",
          (x) => x,
          (x) => x,
          encodeReadRowsRequest(streamNames[0]),
          new grpc.Metadata(),
        );
        stream.on("data", (buf) => {
          const batchBytes = decodeReadRowsBatch(buf);
          if (!batchBytes || batchBytes.length === 0) return;
          const synthetic = Buffer.concat([
            schemaOnly,
            batchBytes,
            EOS_MARKER,
          ]);
          const reader = apacheArrow.RecordBatchReader.from(
            new Uint8Array(synthetic),
          );
          for (const recordBatch of reader) {
            total += recordBatch.numRows;
          }
        });
        stream.on("end", resolve);
        stream.on("error", reject);
      });

      assert.equal(total, 3, "expected 3 rows from ReadRows");
    } finally {
      channel.close();
    }
  });
});
