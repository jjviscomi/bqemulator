'use strict';

const express = require('express');
const { BigQuery } = require('@google-cloud/bigquery');
const { PassThroughClient } = require('google-auth-library');

const PORT = Number(process.env.PORT) || 8080;
const PROJECT = process.env.BQ_PROJECT || 'bqemu-demo';
const DATASET = process.env.BQ_DATASET || 'cr_demo';
const REST_URL = process.env.BQEMU_REST_URL;

function buildBigQuery() {
  if (REST_URL) {
    // PassThroughClient skips ADC entirely — the documented pattern for
    // pointing the SDK at a local emulator.
    return new BigQuery({
      projectId: PROJECT,
      apiEndpoint: REST_URL,
      authClient: new PassThroughClient(),
    });
  }
  return new BigQuery({ projectId: PROJECT });
}

const bq = buildBigQuery();
const app = express();

app.get('/healthz', (_req, res) => res.status(200).send('ok'));

app.get('/customers', async (_req, res) => {
  try {
    const sql = `SELECT id, name FROM \`${PROJECT}.${DATASET}.customers\` ORDER BY id`;
    const [rows] = await bq.query({ query: sql, useLegacySql: false });
    res.json(rows.map((row) => ({ id: Number(row.id), name: String(row.name) })));
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

const server = app.listen(PORT, () => {
  console.log(`cloud-run-local listening on :${PORT} (BQ endpoint=${REST_URL || '(prod)'})`);
});

function shutdown() {
  server.close(() => process.exit(0));
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
