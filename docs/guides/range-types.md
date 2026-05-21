# RANGE types

Status: shipped.

`RANGE<DATE>`, `RANGE<DATETIME>`, and `RANGE<TIMESTAMP>` are modeled
as `STRUCT<start T, "end" T>` in DuckDB storage, with the
`RANGE_*` SQL functions rewritten to struct-field arithmetic. ADR 0019
records the design.

## Defining a RANGE column

```python
from google.cloud import bigquery

# Use the REST API directly — the BigQuery Python client doesn't yet
# expose rangeElementType in SchemaField.
import httpx
httpx.post(
    f"{rest_url}/bigquery/v2/projects/p/datasets/ds/tables",
    json={
        "schema": {
            "fields": [
                {"name": "id", "type": "INT64", "mode": "REQUIRED"},
                {
                    "name": "duration",
                    "type": "RANGE",
                    "mode": "NULLABLE",
                    "rangeElementType": {"type": "DATE"},
                },
            ],
        },
        "tableReference": {
            "projectId": "p", "datasetId": "ds", "tableId": "subs",
        },
    },
)
```

## RANGE constructor

```sql
-- Build a half-open range.
SELECT RANGE(DATE '2024-01-01', DATE '2024-12-31') AS r
```

The constructor is pre-translated to a STRUCT literal —
`{'start':..., 'end':...}` — before SQLGlot transpile, so it never
collides with DuckDB's two-argument `range()` sequence generator
(used by `GENERATE_ARRAY`).

## RANGE_CONTAINS

Half-open semantics — `[start, end)`. The start is contained; the
end is not.

```sql
SELECT
  RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'),
                 DATE '2024-06-15') AS mid,        -- TRUE
  RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'),
                 DATE '2024-01-01') AS at_start,   -- TRUE
  RANGE_CONTAINS(RANGE(DATE '2024-01-01', DATE '2024-12-31'),
                 DATE '2024-12-31') AS at_end      -- FALSE
```

## RANGE_OVERLAPS

```sql
SELECT RANGE_OVERLAPS(
    RANGE(DATE '2024-01-01', DATE '2024-06-30'),
    RANGE(DATE '2024-04-01', DATE '2024-09-30')
);  -- TRUE
```

The expansion is the canonical `s1 < e2 AND s2 < e1` predicate, which
is commutative — `RANGE_OVERLAPS(a, b) == RANGE_OVERLAPS(b, a)` for
all inputs. The Hypothesis property test
`tests/property/test_range_invariants.py` asserts this invariant.

## RANGE_INTERSECT

Returns the intersected range as a struct, or `NULL` when the input
ranges are disjoint.

```sql
SELECT RANGE_INTERSECT(
    RANGE(DATE '2024-01-01', DATE '2024-06-30'),
    RANGE(DATE '2024-04-01', DATE '2024-09-30')
);  -- struct(start = 2024-04-01, end = 2024-06-30)
```

## GENERATE_RANGE_ARRAY

Splits a range into consecutive sub-ranges of length `step`.

```sql
SELECT GENERATE_RANGE_ARRAY(
    RANGE(DATE '2024-01-01', DATE '2024-01-04'),
    INTERVAL 1 DAY
);
-- [
--   {start: 2024-01-01, end: 2024-01-02},
--   {start: 2024-01-02, end: 2024-01-03},
--   {start: 2024-01-03, end: 2024-01-04}
-- ]
```

## RANGE_SESSIONIZE

Groups rows whose `RANGE<T>`-typed columns overlap or touch into
sessions. Returns each input row plus a `session_range` column
spanning the start/end of the session the row belongs to.

```sql
CREATE OR REPLACE TABLE events (
  user_id STRING,
  duration RANGE<DATE>
);
INSERT INTO events VALUES
  ("alice", RANGE<DATE> "[2024-01-01, 2024-01-03)"),
  ("alice", RANGE<DATE> "[2024-01-03, 2024-01-05)"),
  ("alice", RANGE<DATE> "[2024-01-10, 2024-01-12)");

SELECT user_id, duration, session_range
FROM RANGE_SESSIONIZE(
  TABLE events,
  'duration',
  ['user_id']
)
ORDER BY user_id, duration;
-- alice [2024-01-01, 2024-01-03) → session [2024-01-01, 2024-01-05)
-- alice [2024-01-03, 2024-01-05) → session [2024-01-01, 2024-01-05)
-- alice [2024-01-10, 2024-01-12) → session [2024-01-10, 2024-01-12)
```

The optional 4th argument selects the sessionize mode:

* `'MEETS'` (default, or `'OVERLAPS_OR_MEETS'` alias): a new session
  starts when the current row's range start is **strictly greater
  than** the running maximum of prior row ends — so ranges that
  meet (touching, current.start == max_prior_end) or overlap stay
  in the same session.
* `'OVERLAPS'`: a new session starts when the current row's range
  start is **greater than or equal to** the running maximum of
  prior row ends — touching ranges form *separate* sessions; only
  strict overlap keeps them together.

The emulator rewrites the call to a windowed gaps-and-islands
subquery before SQLGlot's BigQuery → DuckDB transpile; see
[`src/bqemulator/sql/rewriter/range_sessionize.py`](https://github.com/jjviscomi/bqemulator/blob/main/src/bqemulator/sql/rewriter/range_sessionize.py)
for the implementation. The rewrite is text-level because SQLGlot's
BigQuery parser doesn't accept the `TABLE <ref>` TVF-argument
keyword.

## See also

* [ADR 0019 — Specialized types](../adr/0019-specialized-types.md)
* [Architecture: specialized types](../architecture/specialized-types.md)
