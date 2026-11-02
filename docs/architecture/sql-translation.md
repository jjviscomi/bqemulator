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
       annotate types (with the catalog schema snapshot's column
       types), apply rules post-order, typing each replacement
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

When such a rewrite must be finished after DuckDB generation, it emits a
`BQEMU_*` handoff call that a rule expands: `DATE_TRUNC(x, WEEK(MONDAY))`
becomes `BQEMU_DATE_TRUNC(x, 'WEEK(MONDAY)')`, which `DateTruncWeekRule`
expands to `time_bucket(INTERVAL '7' DAY, x)`. Where possible the
expansion lets DuckDB pick the result type when it binds the query
(`time_bucket` returns `DATE` for a `DATE` operand), because the
translator cannot see the type of a query parameter, `UNNEST` element
or UDF argument.

The rule engine's type annotation runs `qualify` and SQLGlot's type
annotator on a copy of the statement and copies the resulting types back
onto the original nodes, so rules see operand types while the
translated SQL keeps the user's identifiers, `*` projections and
column names exactly as written.

Types describe the DuckDB SQL being rewritten, as DuckDB evaluates it:
the annotator uses SQLGlot's DuckDB dialect typing (`time_bucket` keeps
its operand's type, date-part functions return `BIGINT`), types
`DATE ± INTERVAL` as `TIMESTAMP` and `TRY(x)` as `x`, types calls to
the engine's Python-backed helpers by the return types they are
registered with (`bqemulator.sql.builtin_udfs.BUILTIN_UDFS`), and types
each `BQEMU_*` handoff call as its expansion. Every statement is annotated;
the catalog schema snapshot supplies the column types of the tables it
references, so a statement that reads no catalog table is still typed
from its literals, casts, functions and `UNNEST` / CTE columns. When
`qualify` cannot resolve a statement, its nodes are typed from their
own structure and only column references stay unknown.

Each rule replacement is typed the same way as soon as it is made, so
a rule that reads an operand's type sees the type of the SQL that
replaced the operand: in `DATE_TRUNC(DATE_TRUNC(d, MONTH), ISOYEAR)`
the outer rule sees the inner `time_bucket` as `DATE`.

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
