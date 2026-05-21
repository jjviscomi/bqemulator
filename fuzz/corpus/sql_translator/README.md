# SQL-translator fuzz seed corpus

Seed inputs for [`fuzz_sql_translator.py`](../../fuzz_sql_translator.py).
Atheris's coverage-guided mutation expands from these via libFuzzer's
sampling+mutation engine; the seeds matter mostly as a starting
distribution that already exercises the major translator branches
(parse, transpile, post-process rule walk, error path).

Each file is one BigQuery SQL query. Atheris reads every file in this
directory at startup whenever the harness is invoked with the
directory path as a positional argument:

```
python fuzz/fuzz_sql_translator.py -max_total_time=60 \
    fuzz/corpus/sql_translator
```

Seeds intentionally cover:

* `select_one.sql` — minimal happy-path.
* `select_safe_divide.sql` — SAFE-prefixed function + WHERE + ORDER BY + LIMIT.
* `struct_array.sql` — STRUCT / ARRAY / UNNEST (the rewriter pipeline's main customers).
* `window.sql` — analytic window function.
* `cte.sql` — multi-CTE pipeline.
* `scripting.sql` — DECLARE + BEGIN/END + IF/THEN (the scripting interpreter's entry).
* `parse_error.sql` — guaranteed parse error (exercises the `sql_parse_error` path).
* `unterminated_string.sql` — lexer error (exercises the unterminated-string error mapper).
* `empty.sql` — zero-byte input (exercises the `Empty query` Err branch).
* `unsupported_ml.sql` — explicitly out-of-scope keyword
  (exercises the `sql_unsupported` early-reject branch).
