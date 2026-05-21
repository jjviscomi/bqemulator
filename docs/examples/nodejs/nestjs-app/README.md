# NestJS app with `bqemulator`-backed e2e tests

A minimal NestJS service exposing `GET /customers` backed by
`@google-cloud/bigquery`, plus a Jest e2e test that runs the full HTTP
stack against `bqemulator`.

## What it demonstrates

- Wiring `@google-cloud/bigquery` into a NestJS provider via DI
  (`BigQueryProvider`).
- Pointing the client at the emulator via `apiEndpoint` + custom auth
  options that disable real credentials.
- A `supertest`-driven e2e test that boots the full HTTP stack
  (`Test.createTestingModule().createNestApplication()`) and asserts
  the response shape.

## Layout

```
src/main.ts                — bootstrap
src/app.module.ts          — module wiring
src/customers/             — controller + service + BQ provider
test/customers.e2e-spec.ts — Jest e2e spec
```

## Run

```bash
make test
```

`make test`:

1. Starts a `bqemulator` container (via docker) on port 9050.
2. Seeds the test dataset by hitting the REST API directly.
3. Runs `npm run test:e2e`.
4. Tears the container down.

Set `BQEMU_REST_URL` (default `http://localhost:9050`) to point at an
already-running emulator.

## What to look for

- The `BigQuery` client is constructed in
  [`src/customers/bigquery.provider.ts`](src/customers/bigquery.provider.ts).
  In production, you'd drop the `apiEndpoint` and pass real
  credentials.
- The e2e test does not mock `@google-cloud/bigquery` — it exercises
  the real client library against the emulator.
