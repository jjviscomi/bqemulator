# Full-stack docker-compose: app + emulator + observability

A "production-shaped" compose stack:

- `bqemulator` (the emulator, exposing Prometheus metrics).
- `app` — a tiny Python service that reads from `bqemulator`.
- `prometheus` — scrapes the emulator's `/metrics` endpoint.
- `grafana` — pre-provisioned with a Prometheus datasource and a
  starter dashboard.

This is the canonical local-development setup for teams adopting
`bqemulator` who want one `docker compose up` to give them the
complete dev environment.

## Layout

```
docker-compose.yml
app/Dockerfile
app/server.py
prometheus/prometheus.yml
grafana/provisioning/datasources/prometheus.yml
grafana/provisioning/dashboards/dashboards.yml
grafana/provisioning/dashboards/bqemulator.json
seed.py                — seeds dataset + table on host
smoke-test.sh          — asserts each service is healthy and serving
```

## Run

```bash
make test
```

`make test`:

1. `docker compose up -d --build`.
2. Waits for `bqemulator`, `app`, `prometheus`, `grafana` to be healthy.
3. Runs `seed.py` against the emulator.
4. Hits `app:8080/customers` and asserts a 3-row response.
5. Hits `prometheus:9090/api/v1/query?query=up` and asserts the
   emulator is up.
6. Hits `grafana:3000/api/health` and asserts `database: ok`.
7. `docker compose down -v`.

## What to look for

- The emulator publishes Prometheus metrics on `/metrics` (see
  [`docs/guides/observability.md`](../../../guides/observability.md)).
- Grafana is pre-provisioned via the `provisioning/` directory — no
  manual UI clicks needed.
- The app container is intentionally tiny; the point is the
  composition, not the app code.
