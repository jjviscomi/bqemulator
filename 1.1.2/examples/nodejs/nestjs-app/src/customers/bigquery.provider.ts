import { BigQuery, BigQueryOptions } from '@google-cloud/bigquery';
import { PassThroughClient } from 'google-auth-library';
import { FactoryProvider } from '@nestjs/common';

export const BIGQUERY = Symbol('BIGQUERY');

export const BigQueryProvider: FactoryProvider<BigQuery> = {
  provide: BIGQUERY,
  useFactory: (): BigQuery => {
    const apiEndpoint = process.env.BQEMU_REST_URL;
    const projectId = process.env.BQ_PROJECT || 'bqemu-demo';

    if (apiEndpoint) {
      // Emulator mode: skip auth, point at the local endpoint.
      // PassThroughClient is the documented pattern for emulators — it
      // returns empty auth headers and never tries to reach the metadata
      // server or ADC. `BigQueryOptions.authClient` is typed against
      // `JSONClient` so we widen via cast; runtime accepts any AuthClient.
      return new BigQuery({
        projectId,
        apiEndpoint,
        authClient: new PassThroughClient(),
      } as BigQueryOptions);
    }

    // Production: rely on Application Default Credentials.
    return new BigQuery({ projectId });
  },
};
