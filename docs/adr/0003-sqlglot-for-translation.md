# ADR 0003: SQLGlot for BigQuery → DuckDB translation

- **Status**: Accepted

## Context

GoogleSQL is BigQuery's dialect; DuckDB has its own. We need a reliable
way to translate queries. Options:

1. **SQLGlot** — Python transpiler supporting both dialects as first-class.
2. **ZetaSQL** — Google's open-source parser (C++); used by goccy. Heavy
   native build, slow to compile.
3. **Hand-written translator** — maximum control, enormous ongoing cost.
4. **Accept DuckDB dialect** — user writes DuckDB SQL; defeats the emulator.

## Decision

SQLGlot as the core transpiler, plus a rule layer in `bqemulator.sql.rules`
for BigQuery functions SQLGlot does not translate correctly. The rule
layer uses a strategy pattern — each rule is a small class registered
with the translator.

## Consequences

- **Positive**: fast feedback loop; Python-native; active project; easy
  contribution (add a rule = add a class + a test).
- **Positive**: regressions become test cases in our corpus — no opaque
  native compiler to debug.
- **Negative**: some BigQuery edge cases (wildcard tables with
  `_TABLE_SUFFIX`, complex UNNEST patterns) require custom pre-SQLGlot
  rewrites. Tracked in `docs/architecture/sql-translation.md`.
