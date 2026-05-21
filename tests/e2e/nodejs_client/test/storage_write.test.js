/**
 * E2E: Phase 5 Storage Write API against the live container.
 *
 * Uses the @google-cloud/bigquery-storage client only to get at the
 * proto-js definitions it bundles — the test itself talks to the
 * emulator via raw @grpc/grpc-js so it doesn't depend on the
 * client library's auth layer.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

const REST_URL = process.env.BQEMU_REST_URL || "http://localhost:9050";
const GRPC_ENDPOINT =
  process.env.BQEMU_GRPC_ENDPOINT || "localhost:9060";
const PROJECT = "e2e-nodejs";
const DATASET = "e2e_write";
const TABLE = "write_target";

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
  ];
  await dataset.createTable(TABLE, { schema });
}

async function cleanup() {
  try {
    const client = makeBqClient();
    await client.dataset(DATASET).delete({ force: true });
  } catch (_) {
    /* ignore */
  }
}

async function countRows() {
  const res = await fetch(
    `${REST_URL}/bigquery/v2/projects/${PROJECT}/datasets/${DATASET}/tables/${TABLE}/data`,
  );
  const body = await res.json();
  return Number(body.totalRows || 0);
}

// --- hand-crafted proto wire helpers --------------------------------------

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

// --- Row {id: int64, name: string} ---------------------------------------

function encodeRow(id, name) {
  return Buffer.concat([
    encodeVarintField(1, id),
    encodeString(2, name),
  ]);
}

// --- DescriptorProto for {id, name} --------------------------------------

function buildDescriptorBytes() {
  // FieldDescriptorProto:
  //   name=1 (string), number=3 (int32), label=4 (enum), type=5 (enum)
  // Label: OPTIONAL=1; Types: INT64=3, STRING=9
  const idField = Buffer.concat([
    encodeString(1, "id"),
    encodeVarintField(3, 1),
    encodeVarintField(4, 1),
    encodeVarintField(5, 3),
  ]);
  const nameField = Buffer.concat([
    encodeString(1, "name"),
    encodeVarintField(3, 2),
    encodeVarintField(4, 1),
    encodeVarintField(5, 9),
  ]);
  // DescriptorProto: name=1, field=2 (repeated FieldDescriptorProto)
  return Buffer.concat([
    encodeString(1, "Row"),
    encodeLengthDelim(2, idField),
    encodeLengthDelim(2, nameField),
  ]);
}

// --- CreateWriteStreamRequest --------------------------------------------
// Fields: parent=1 (string), write_stream=2 (WriteStream).
// WriteStream: type=2 (enum) — COMMITTED=2.

// WriteStream.Type enum values in BigQuery Storage v1:
//   TYPE_UNSPECIFIED = 0
//   COMMITTED        = 1
//   PENDING          = 2
//   BUFFERED         = 3
function encodeCreateWriteStream(parent, type = 1 /* COMMITTED */) {
  const writeStream = encodeVarintField(2, type);
  return Buffer.concat([
    encodeString(1, parent),
    encodeLengthDelim(2, writeStream),
  ]);
}

// WriteStream response: name=1 (string).
function decodeStreamName(buf) {
  // Find field 1 (wire type 2) and decode.
  let i = 0;
  while (i < buf.length) {
    const tag = buf[i++];
    const fn = tag >> 3;
    const wt = tag & 0x7;
    if (wt === 2) {
      // read varint length
      let len = 0;
      let shift = 0;
      while (true) {
        const b = buf[i++];
        len |= (b & 0x7f) << shift;
        if ((b & 0x80) === 0) break;
        shift += 7;
      }
      const payload = buf.slice(i, i + len);
      if (fn === 1) return payload.toString("utf8");
      i += len;
    } else if (wt === 0) {
      while ((buf[i++] & 0x80) !== 0) {
        /* skip */
      }
    } else {
      throw new Error(`unknown wire type ${wt}`);
    }
  }
  return null;
}

// AppendRowsResponse: append_result=1 (AppendResult), error=2 (google.rpc.Status).
function decodeAppendResponse(buf) {
  let i = 0;
  const result = { hasError: false };
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
      if (fn === 2) {
        // google.rpc.Status: code=1 varint, message=2 string
        const payload = buf.slice(i, i + len);
        let j = 0;
        while (j < payload.length) {
          const ttag = payload[j++];
          const tfn = ttag >> 3;
          const twt = ttag & 0x7;
          if (twt === 0) {
            let v = 0;
            let s = 0;
            while (true) {
              const b = payload[j++];
              v |= (b & 0x7f) << s;
              if ((b & 0x80) === 0) break;
              s += 7;
            }
            if (tfn === 1 && v !== 0) {
              result.hasError = true;
              result.code = v;
            }
          } else if (twt === 2) {
            let l = 0;
            let s = 0;
            while (true) {
              const b = payload[j++];
              l |= (b & 0x7f) << s;
              if ((b & 0x80) === 0) break;
              s += 7;
            }
            const data = payload.slice(j, j + l);
            j += l;
            if (tfn === 2) result.message = data.toString("utf8");
          }
        }
      }
      i += len;
    } else if (wt === 0) {
      while ((buf[i++] & 0x80) !== 0) {
        /* skip */
      }
    }
  }
  return result;
}

// AppendRowsRequest: write_stream=1, proto_rows=4, offset=2 (Int64Value).
// ProtoData: writer_schema=1 (ProtoSchema), rows=2 (ProtoRows).
// ProtoSchema: proto_descriptor=1 (DescriptorProto).
// ProtoRows: serialized_rows=1 (repeated bytes).

function encodeAppendRequest(streamName, descriptorBytes, rowBytes) {
  const protoSchema = encodeLengthDelim(1, descriptorBytes);
  const serializedRows = rowBytes.map((r) => encodeLengthDelim(1, r));
  const protoRows = Buffer.concat(serializedRows);
  const protoData = Buffer.concat([
    encodeLengthDelim(1, protoSchema),
    encodeLengthDelim(2, protoRows),
  ]);
  // Int64Value: value=1 varint
  const int64Value = encodeVarintField(1, 0);
  return Buffer.concat([
    encodeString(1, streamName),
    encodeLengthDelim(2, int64Value), // offset
    encodeLengthDelim(4, protoData), // proto_rows
  ]);
}

// FinalizeWriteStreamRequest: name=1 string
function encodeFinalizeRequest(streamName) {
  return encodeString(1, streamName);
}

// BatchCommitWriteStreamsRequest: parent=1 string, write_streams=2 repeated string
function encodeBatchCommitRequest(parent, streamNames) {
  return Buffer.concat([
    encodeString(1, parent),
    ...streamNames.map((n) => encodeString(2, n)),
  ]);
}

// BatchCommitWriteStreamsResponse: stream_errors=2 (repeated StorageError).
// We just need to know whether the response carried any errors.
function batchCommitHasErrors(buf) {
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
      if (fn === 2 && len > 0) return true;
      i += len;
    } else if (wt === 0) {
      while ((buf[i++] & 0x80) !== 0) {
        /* skip */
      }
    }
  }
  return false;
}

describe("bqemulator Phase 5 Storage Write (Node.js via raw gRPC)", () => {
  before(async () => {
    await seed();
  });
  after(async () => {
    await cleanup();
  });

  it("appends proto rows to a COMMITTED stream", async () => {
    const grpc = require("@grpc/grpc-js");
    const channel = new grpc.Client(
      GRPC_ENDPOINT,
      grpc.credentials.createInsecure(),
    );

    try {
      // CreateWriteStream.
      const parent = `projects/${PROJECT}/datasets/${DATASET}/tables/${TABLE}`;
      const createRespBytes = await new Promise((resolve, reject) => {
        channel.makeUnaryRequest(
          "/google.cloud.bigquery.storage.v1.BigQueryWrite/CreateWriteStream",
          (x) => x,
          (x) => x,
          encodeCreateWriteStream(parent, 1 /* COMMITTED */),
          (err, value) => (err ? reject(err) : resolve(value)),
        );
      });
      const streamName = decodeStreamName(createRespBytes);
      assert.ok(
        streamName && streamName.includes("/streams/"),
        `bad stream name: ${streamName}`,
      );

      // AppendRows.
      const descriptor = buildDescriptorBytes();
      const rows = [encodeRow(1, "alice"), encodeRow(2, "bob")];
      const appendBytes = encodeAppendRequest(streamName, descriptor, rows);

      const responses = await new Promise((resolve, reject) => {
        const duplex = channel.makeBidiStreamRequest(
          "/google.cloud.bigquery.storage.v1.BigQueryWrite/AppendRows",
          (x) => x,
          (x) => x,
          new grpc.Metadata(),
        );
        const acc = [];
        duplex.on("data", (buf) => acc.push(decodeAppendResponse(buf)));
        duplex.on("end", () => resolve(acc));
        duplex.on("error", reject);
        duplex.write(appendBytes);
        duplex.end();
      });
      assert.equal(responses.length, 1, "expected one AppendRows response");
      assert.equal(
        responses[0].hasError,
        false,
        `AppendRows error: ${JSON.stringify(responses[0])}`,
      );
    } finally {
      channel.close();
    }

    assert.equal(await countRows(), 2);
  });

  it("PENDING stream is invisible until Finalize + BatchCommit", async () => {
    const grpc = require("@grpc/grpc-js");
    const channel = new grpc.Client(
      GRPC_ENDPOINT,
      grpc.credentials.createInsecure(),
    );

    // The PENDING test reuses the seeded dataset/table but with a
    // fresh stream — cleanup() resets the table state between tests.
    await cleanup();
    await seed();

    try {
      const parent = `projects/${PROJECT}/datasets/${DATASET}/tables/${TABLE}`;
      const createRespBytes = await new Promise((resolve, reject) => {
        channel.makeUnaryRequest(
          "/google.cloud.bigquery.storage.v1.BigQueryWrite/CreateWriteStream",
          (x) => x,
          (x) => x,
          encodeCreateWriteStream(parent, 2 /* PENDING */),
          (err, value) => (err ? reject(err) : resolve(value)),
        );
      });
      const streamName = decodeStreamName(createRespBytes);
      assert.ok(streamName && streamName.includes("/streams/"));

      // AppendRows.
      const descriptor = buildDescriptorBytes();
      const rows = [encodeRow(10, "pending-a"), encodeRow(20, "pending-b")];
      const appendBytes = encodeAppendRequest(streamName, descriptor, rows);
      const responses = await new Promise((resolve, reject) => {
        const duplex = channel.makeBidiStreamRequest(
          "/google.cloud.bigquery.storage.v1.BigQueryWrite/AppendRows",
          (x) => x,
          (x) => x,
          new grpc.Metadata(),
        );
        const acc = [];
        duplex.on("data", (buf) => acc.push(decodeAppendResponse(buf)));
        duplex.on("end", () => resolve(acc));
        duplex.on("error", reject);
        duplex.write(appendBytes);
        duplex.end();
      });
      assert.equal(responses[0].hasError, false);

      // Pre-commit: rows are not visible.
      assert.equal(await countRows(), 0);

      await new Promise((resolve, reject) => {
        channel.makeUnaryRequest(
          "/google.cloud.bigquery.storage.v1.BigQueryWrite/FinalizeWriteStream",
          (x) => x,
          (x) => x,
          encodeFinalizeRequest(streamName),
          (err, value) => (err ? reject(err) : resolve(value)),
        );
      });

      const commitRespBytes = await new Promise((resolve, reject) => {
        channel.makeUnaryRequest(
          "/google.cloud.bigquery.storage.v1.BigQueryWrite/BatchCommitWriteStreams",
          (x) => x,
          (x) => x,
          encodeBatchCommitRequest(
            `projects/${PROJECT}/datasets/${DATASET}`,
            [streamName],
          ),
          (err, value) => (err ? reject(err) : resolve(value)),
        );
      });
      assert.equal(
        batchCommitHasErrors(commitRespBytes),
        false,
        "BatchCommit returned stream_errors",
      );

      assert.equal(await countRows(), 2);
    } finally {
      channel.close();
    }
  });
});
