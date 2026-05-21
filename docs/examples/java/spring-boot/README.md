# Spring Boot + `bqemulator` (Testcontainers)

A minimal Spring Boot service whose `CustomerRepository` talks to
BigQuery via `google-cloud-bigquery`. The integration test starts
`bqemulator` via Testcontainers and asserts the round-trip.

## What it demonstrates

- A Spring `@Configuration` that constructs `BigQueryOptions` with a
  conditional `setHost(...)` override driven by the
  `bqemulator.endpoint` property.
- Using `NoCredentials.getInstance()` for the emulator path so no real
  GCP credentials are needed.
- A JUnit 5 integration test that:
  - Starts the `ghcr.io/jjviscomi/bqemulator:dev` image via the
    Testcontainers Java client.
  - Sets `BIGQUERY_EMULATOR_HOST` via `@DynamicPropertySource` before
    the Spring context starts.
  - Seeds a small dataset and verifies the repository returns the
    expected rows.

## Layout

```
pom.xml
src/main/java/com/example/bqemu/
  BqemuApplication.java
  BigQueryConfig.java
  CustomerRepository.java
src/main/resources/application.properties
src/test/java/com/example/bqemu/
  CustomerRepositoryIT.java
```

## Run

```bash
make test
```

`make test` runs `mvn -B verify`, which executes the Testcontainers-
driven integration tests. Requires Docker to be running.

## What to look for

- Production wiring (no `bqemulator.endpoint`) uses ADC and the public
  BigQuery host — same code path as a real deployment.
- Test wiring (Testcontainers) sets `bqemulator.endpoint` so the
  `BigQueryOptions` builder calls `setHost(...)`. This is the
  recommended idiom for swapping endpoints in Spring Boot.
