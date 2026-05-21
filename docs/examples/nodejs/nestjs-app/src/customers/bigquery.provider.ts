import { BigQuery } from '@google-cloud/bigquery';
import { FactoryProvider } from '@nestjs/common';

export const BIGQUERY = Symbol('BIGQUERY');

export const BigQueryProvider: FactoryProvider<BigQuery> = {
  provide: BIGQUERY,
  useFactory: (): BigQuery => {
    const apiEndpoint = process.env.BQEMU_REST_URL;
    const projectId = process.env.BQ_PROJECT || 'bqemu-demo';

    if (apiEndpoint) {
      // Emulator mode: skip auth, point at the local endpoint.
      return new BigQuery({
        projectId,
        apiEndpoint,
        // The emulator accepts any token; this avoids the SDK
        // attempting an ADC lookup against the metadata server.
        token: 'dummy',
      });
    }

    // Production: rely on Application Default Credentials.
    return new BigQuery({ projectId });
  },
};
