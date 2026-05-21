# docker-compose quickstart

Drop bqemulator into your `docker-compose.yml`:

```yaml
services:
  bqemulator:
    image: ghcr.io/jjviscomi/bqemulator:latest
    ports:
      - "9050:9050"
      - "9060:9060"
    environment:
      BQEMU_DATA_DIR: /var/lib/bqemulator
    volumes:
      - bqemu-data:/var/lib/bqemulator
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://127.0.0.1:9050/healthz').raise_for_status()"]
      interval: 10s

  app:
    build: .
    environment:
      BIGQUERY_EMULATOR_HOST: bqemulator:9050
      BIGQUERY_API_ENDPOINT: http://bqemulator:9050
    depends_on:
      bqemulator:
        condition: service_healthy

volumes:
  bqemu-data:
```

Run:

```bash
docker compose up
```

Your app reads `BIGQUERY_EMULATOR_HOST` (and/or `BIGQUERY_API_ENDPOINT`)
and talks to bqemulator across the compose network.

See [examples/docker-compose](https://github.com/jjviscomi/bqemulator/tree/main/docs/examples/docker-compose)
for a fully-wired example.
