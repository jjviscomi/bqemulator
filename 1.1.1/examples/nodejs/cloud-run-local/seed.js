'use strict';

const { BigQuery } = require('@google-cloud/bigquery');
const { PassThroughClient } = require('google-auth-library');

const PROJECT = process.env.BQ_PROJECT || 'bqemu-demo';
const DATASET = process.env.BQ_DATASET || 'cr_demo';
const REST_URL = process.env.BQEMU_REST_URL || 'http://localhost:9050';

async function main() {
  // PassThroughClient skips ADC entirely — the documented pattern for
  // pointing the SDK at a local emulator.
  const bq = new BigQuery({
    projectId: PROJECT,
    apiEndpoint: REST_URL,
    authClient: new PassThroughClient(),
  });
  try {
    await bq.createDataset(DATASET, { location: 'US' });
  } catch (err) {
    if (!/already exists/i.test(String(err))) throw err;
  }
  const dataset = bq.dataset(DATASET);
  try {
    await dataset.createTable('customers', {
      schema: [
        { name: 'id', type: 'INTEGER' },
        { name: 'name', type: 'STRING' },
      ],
    });
  } catch (err) {
    if (!/already exists/i.test(String(err))) throw err;
  }
  await dataset.table('customers').insert([
    { id: 1, name: 'Alice' },
    { id: 2, name: 'Bob' },
    { id: 3, name: 'Carol' },
  ]);
  console.log(`seeded ${PROJECT}.${DATASET}.customers with 3 rows`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
