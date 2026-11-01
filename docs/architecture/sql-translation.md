# SQL translation

Pipeline (implemented in `bqemulator.sql.translator.SQLTranslator`):

```
BigQuery SQL
    │
    ▼  text pre-translators (bqemulator.sql.rewriter.*)
    ▼  sqlglot.parse(read="bigquery")
    ▼  BigQuery-AST rewrites (bqemulator.sql.rewriter.datetime_helpers)
    ▼  DuckDB generation
    ▼  rule engine (bqemulator.sql.rules.*): re-parse as DuckDB, qualify +
       annotate types against the catalog schema snapshot, apply rules
       post-order
DuckDB SQL
```

Text pre-translators rewrite constructs the BigQuery parser or the
transpile cannot take in their original form. Rewrites that depend on
distinctions only the BigQuery AST carries — `DATE_ADD(...)` versus
`date + INTERVAL`, `DATE_TRUNC` versus `TIMESTAMP_TRUNC`,
`WEEK(<WEEKDAY>)` — run on the parsed BigQuery statement immediately
before DuckDB generation rather than on text, because re-serialising
BigQuery SQL is lossy (SQLGlot's BigQuery generator strips `CAST` type
parameters and reshapes `AT TIME ZONE` and `VALUES … AS v(…)`).

When such a rewrite also needs operand types, which exist only once the
rule engine annotates the DuckDB AST, it emits a `BQEMU_*` handoff call
that a rule expands: `DATE_TRUNC(x, WEEK(MONDAY))` becomes
`BQEMU_DATE_TRUNC(x, 'WEEK(MONDAY)')`, which `DateTruncWeekRule`
expands into a `DATE` or `TIMESTAMP` truncation according to the type
of `x`.

## Rule engine

Each BigQuery function or construct that requires custom translation is
represented by a `TranslationRule` subclass in `bqemulator.sql.rules/`.
Rules register themselves with the `SQLTranslator` registry at import
time.

```python
class SafeDivideRule(TranslationRule):
    name = "SAFE_DIVIDE"
    matches = sqlglot.exp.SafeDivide

    def apply(self, node: sqlglot.exp.Expression) -> sqlglot.exp.Expression:
        # Replace SAFE_DIVIDE(a, b) with CASE WHEN b = 0 THEN NULL ELSE a / b END
        ...
```

Adding a new rule is documented in
[contributing/adding-sql-functions.md](contributing/adding-sql-functions.md).

## Parameter binding

Query parameters (positional `?` and named `@name`) are bound through
DuckDB's prepared-statement parameter list. Array and struct parameters
use DuckDB's native list/struct literals.

## Rewrite passes

- `partition_pruning.py` — inject pruning predicates
- `row_access_filter.py` — inject row access policy filters
- `wildcard_expander.py` — expand `dataset.events_*` into UNION of
  matching tables
- `pseudo_columns.py` — rewrite `_PARTITIONTIME`, `_PARTITIONDATE`,
  `_TABLE_SUFFIX`, `_FILE_NAME`
- `policy_injection.py` — orchestrates all policy-driven rewrites

## Errors

Translation failures (parse errors, unsupported constructs) surface as
`InvalidQueryError` or `UnsupportedFeatureError`. The API layer renders
them to BigQuery's ErrorProto shape.
