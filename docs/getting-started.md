# Getting started

## Install

```bash
pip install bqemulator
```

Or run from Docker:

```bash
docker run --rm -p 9050:9050 -p 9060:9060 ghcr.io/jjviscomi/bqemulator:latest
```

## Start the emulator

```bash
bqemulator start --ephemeral
```

This binds REST on `localhost:9050` and gRPC on `localhost:9060`. For
persistent storage that survives restart, use `--data-dir`:

```bash
bqemulator start --data-dir ~/.bqemulator
```

Check the health endpoint:

```bash
curl http://localhost:9050/healthz
# {"status":"ok","version":"1.1.2"}
```

## Point a client at it

Every official Google Cloud BigQuery client library supports endpoint
override. Pick your language:

- [Python quickstart](quickstart/python.md)
- [Node.js quickstart](quickstart/nodejs.md)
- [Go quickstart](quickstart/go.md)
- [Java quickstart](quickstart/java.md)

All clients follow the same pattern: set `BIGQUERY_EMULATOR_HOST=host:port`
and pass an `api_endpoint` option to the client constructor.

## Next

- Use the [pytest fixture](quickstart/pytest.md) for integration tests.
- Check what features are supported in the
 [compatibility matrix](reference/compatibility-matrix.md).
- Understand the [architecture](architecture/overview.md).
