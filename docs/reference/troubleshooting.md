# Troubleshooting

## My client cannot connect

Check:

1. Emulator is running: `curl http://localhost:9050/healthz` returns
   `{"status":"ok"}`.
2. Both env var and client option are set:
   `BIGQUERY_EMULATOR_HOST=host:port` AND the client's `api_endpoint`
   override.
3. For gRPC clients, the gRPC port (default 9060) is distinct from the
   REST port (default 9050).
4. On Docker, the ports are published: `-p 9050:9050 -p 9060:9060`.

## My query works on real BigQuery but not on the emulator

1. Check the [compatibility matrix](compatibility-matrix.md) for feature
   status.
2. Check [out-of-scope.md](out-of-scope.md) for explicit exclusions.
3. Check the error response — `UnsupportedFeatureError` indicates a
   documented exclusion; `invalidQuery` indicates a translation bug.
4. If you believe the query should work and does not, open a
   [bug report](https://github.com/jjviscomi/bqemulator/issues/new?template=bug_report.yml).

## Coverage for JavaScript UDFs is missing

Install the optional extra:

```bash
pip install "bqemulator[udf-js]"
```

## Persistent mode is not retaining data

`--persistent` requires `--data-dir` to be set to a writable directory.
Check the logs for `persistence_mode=persistent` and
`data_dir=/path/to/dir`.

## Port already in use

Use random free ports:

```bash
bqemulator start --rest-port 0 --grpc-port 0
```

The startup logs show the bound ports.
