# CI/CD patterns

## GitHub Actions — in-process

```yaml
jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]" bqemulator
      - run: pytest tests/
```

The pytest fixture starts the emulator in-process — no container needed.

## GitHub Actions — container-based

```yaml
jobs:
  tests:
    runs-on: ubuntu-latest
    services:
      bqemulator:
        image: ghcr.io/jjviscomi/bqemulator:latest
        ports: ["9050:9050", "9060:9060"]
        options: >-
          --health-cmd "python -c 'import httpx; httpx.get(\"http://127.0.0.1:9050/healthz\").raise_for_status()'"
          --health-interval 10s --health-timeout 3s --health-retries 5
    env:
      BIGQUERY_EMULATOR_HOST: localhost:9050
    steps:
      - uses: actions/checkout@v4
      - run: pytest tests/
```

## GitLab CI

```yaml
tests:
  image: python:3.11
  services:
    - name: ghcr.io/jjviscomi/bqemulator:latest
      alias: bqemulator
  variables:
    BIGQUERY_EMULATOR_HOST: bqemulator:9050
  script:
    - pip install -r requirements.txt
    - pytest tests/
```

## CircleCI

```yaml
jobs:
  tests:
    docker:
      - image: cimg/python:3.11
      - image: ghcr.io/jjviscomi/bqemulator:latest
        name: bqemulator
    environment:
      BIGQUERY_EMULATOR_HOST: bqemulator:9050
    steps:
      - checkout
      - run: pip install -r requirements.txt
      - run: pytest tests/
```
