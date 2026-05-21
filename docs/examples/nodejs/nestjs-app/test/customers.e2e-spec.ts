import { Test, TestingModule } from '@nestjs/testing';
import { INestApplication } from '@nestjs/common';
import * as request from 'supertest';
import { BigQuery } from '@google-cloud/bigquery';
import { AppModule } from '../src/app.module';

const REST_URL = process.env.BQEMU_REST_URL || 'http://localhost:9050';
const PROJECT = process.env.BQ_PROJECT || 'bqemu-demo';
const DATASET = `nestjs_demo_${Date.now().toString(36)}`;

describe('Customers (e2e) against bqemulator', () => {
  let app: INestApplication;
  let bq: BigQuery;

  beforeAll(async () => {
    process.env.BQ_DATASET = DATASET;
    process.env.BQEMU_REST_URL = REST_URL;

    bq = new BigQuery({ projectId: PROJECT, apiEndpoint: REST_URL, token: 'dummy' });

    // Seed a dataset + customers table with 3 rows.
    await bq.createDataset(DATASET, { location: 'US' });
    const dataset = bq.dataset(DATASET);
    await dataset.createTable('customers', {
      schema: [
        { name: 'id', type: 'INTEGER' },
        { name: 'name', type: 'STRING' },
      ],
    });
    await dataset.table('customers').insert([
      { id: 1, name: 'Alice' },
      { id: 2, name: 'Bob' },
      { id: 3, name: 'Carol' },
    ]);

    const moduleFixture: TestingModule = await Test.createTestingModule({
      imports: [AppModule],
    }).compile();
    app = moduleFixture.createNestApplication();
    await app.init();
  });

  afterAll(async () => {
    await bq.dataset(DATASET).delete({ force: true });
    if (app) await app.close();
  });

  it('GET /customers returns three seeded rows', async () => {
    const response = await request(app.getHttpServer()).get('/customers');
    expect(response.status).toBe(200);
    expect(response.body).toHaveLength(3);
    expect(response.body.map((c: { id: number }) => c.id).sort()).toEqual([1, 2, 3]);
  });
});
