# Node.js quickstart

```bash
npm install @google-cloud/bigquery
```

```typescript
import { BigQuery } from "@google-cloud/bigquery";

process.env.BIGQUERY_EMULATOR_HOST = "localhost:9050";

const bigquery = new BigQuery({
  projectId: "my-project",
  apiEndpoint: "http://localhost:9050",
});

await bigquery.createDataset("sales");

const [rows] = await bigquery.query(
  "SELECT COUNT(*) AS n FROM sales.orders",
);
console.log(rows);
```

See the [Node.js client reference](https://docs.cloud.google.com/nodejs/docs/reference/bigquery/latest)
for the full API surface. All operations supported by the REST backend
work against bqemulator. See the
[compatibility matrix](../reference/compatibility-matrix.md).
