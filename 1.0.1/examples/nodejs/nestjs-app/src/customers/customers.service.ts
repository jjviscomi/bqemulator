import { Inject, Injectable } from '@nestjs/common';
import { BigQuery } from '@google-cloud/bigquery';
import { BIGQUERY } from './bigquery.provider';

export interface Customer {
  id: number;
  name: string;
}

@Injectable()
export class CustomersService {
  private readonly project: string;
  private readonly dataset: string;

  constructor(@Inject(BIGQUERY) private readonly bq: BigQuery) {
    this.project = process.env.BQ_PROJECT || 'bqemu-demo';
    this.dataset = process.env.BQ_DATASET || 'demo';
  }

  async list(): Promise<Customer[]> {
    const sql = `SELECT id, name FROM \`${this.project}.${this.dataset}.customers\` ORDER BY id`;
    const [rows] = await this.bq.query({ query: sql, useLegacySql: false });
    return rows.map((row) => ({
      id: Number(row.id),
      name: String(row.name),
    }));
  }
}
