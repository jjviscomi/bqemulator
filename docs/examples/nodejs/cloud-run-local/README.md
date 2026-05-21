# Cloud Run service simulated locally against `bqemulator`

A minimal Express-on-Node Cloud Run-shaped service: stateless,
container-packaged, listens on `$PORT`, reads from BigQuery.

Demonstrates running the same image **locally** against `bqemulator`
that you'd push to Cloud Run, with the only change being two
environment variables (`BQEMU_REST_URL`, `BQ_PROJECT`).

## What it demonstrates

- A Cloud Run-ready Express service in
  [`src/server.js`](src/server.js): single endpoint, `process.env.PORT`,
  graceful shutdown.
- A `Dockerfile` that builds the service into a slim image.
- A `docker-compose.yml` that runs the service + `bqemulator` together,
  identical to how Cloud Run would compose against real BigQuery.
- A `curl`-based smoke test in [`smoke-test.sh`](smoke-test.sh) that
  asserts the service returns the expected three rows.

## Layout

```
src/server.js         — Express + @google-cloud/bigquery service
package.json          — pinned deps
Dockerfile            — slim Node image
docker-compose.yml    — service + emulator side-by-side
seed.js               — one-off script that seeds the demo dataset
smoke-test.sh         — post-up `curl` assertion
```

## Run

```bash
make test
```

`make test`:

1. `docker compose up -d` (starts emulator + service).
2. Waits for both to be healthy.
3. Runs `node seed.js` to create dataset + table + 3 rows.
4. Hits `GET http://localhost:8080/customers` via `curl`.
5. Asserts the JSON response contains three rows.
6. `docker compose down`.

## What to look for

- The service does not know it's hitting an emulator. The Cloud Run
  deploy uses ADC and omits `BQEMU_REST_URL`; the local compose sets
  it explicitly.
- The image is plain Cloud Run shape — `EXPOSE 8080`, `CMD ["node",
  "server.js"]`, no docker-specific assumptions.
