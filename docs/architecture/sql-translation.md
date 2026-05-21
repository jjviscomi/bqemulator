# SQL translation

Pipeline (implemented in `src/bqemulator/sql/`):

```
BigQuery SQL
    │
    ▼  preprocess (bqemulator.sql.rewriter.pre_sqlglot)
    ▼  sqlglot.transpile(read="bigquery", write="duckdb")
    ▼  postprocess (bqemulator.sql.rewriter.post_sqlglot)
    ▼  rule engine apply (bqemulator.sql.rules.*)
DuckDB SQL
```

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
