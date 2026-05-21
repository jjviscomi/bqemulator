# INTERVAL arithmetic

Status: shipped.

The emulator implements BigQuery's `INTERVAL` type by passing through
to DuckDB's native `INTERVAL` (a 3-tuple of months/days/microseconds).
Two BigQuery syntactic forms need rewriting before DuckDB will accept
them; both are handled transparently by the SQL translator.

## Single-unit intervals

DuckDB accepts these natively — no translation needed:

```sql
SELECT INTERVAL 1 YEAR;
SELECT INTERVAL '36' HOUR;
SELECT DATE '2024-01-15' + INTERVAL 1 DAY;            -- 2024-01-16
SELECT TIMESTAMP '2024-01-15 12:00:00 UTC' - INTERVAL 1 HOUR;
```

## Compound intervals (`YEAR TO SECOND` form)

BigQuery's compound literal `INTERVAL '1-2 3 4:5:6.789' YEAR TO SECOND`
is rejected outright by DuckDB's parser. The
[pre-translator rewriter](../architecture/specialized-types.md) parses
the literal in Python and expands it to a sum of single-unit
intervals:

```sql
-- Source:
SELECT INTERVAL '1-2 3 4:5:6.789' YEAR TO SECOND;

-- Rewritten before DuckDB sees it:
SELECT (INTERVAL '1' YEAR + INTERVAL '2' MONTH + INTERVAL '3' DAY
      + INTERVAL '4' HOUR + INTERVAL '5' MINUTE
      + INTERVAL '6.789' SECOND);
```

All `Y-M D H:M:S[.f]` shapes are supported: `YEAR TO MONTH`,
`DAY TO HOUR`, `DAY TO MINUTE`, `DAY TO SECOND`, `HOUR TO MINUTE`,
`HOUR TO SECOND`, `MINUTE TO SECOND`, and the full `YEAR TO SECOND`.

## MAKE_INTERVAL

SQLGlot's BigQuery → DuckDB transpiler converts
`MAKE_INTERVAL(1, 2, 3, 4, 5, 6)` to
`INTERVAL '1 year 2 month 3 day 4 hour 5 minute 6 second'` natively —
no emulator-specific work needed.

## JUSTIFY_HOURS / JUSTIFY_DAYS / JUSTIFY_INTERVAL

DuckDB lacks the `justify_*` scalar functions PostgreSQL exposes, so
the emulator synthesises them at translate time from the underlying
`extract` + `to_<unit>` primitives:

| BigQuery | Result |
|-------------------------------------------|---------------------------------|
| `JUSTIFY_HOURS(INTERVAL 36 HOUR)` | `1 day 12:00:00` |
| `JUSTIFY_DAYS(INTERVAL 40 DAY)` | `1 month 10 days` |
| `JUSTIFY_INTERVAL(INTERVAL 40 DAY + 36 HOUR)` | `1 month 11 days 12:00:00` |

`JUSTIFY_DAYS` follows the documented BigQuery/PostgreSQL rule —
30 days = 1 month.

## EXTRACT FROM INTERVAL

DuckDB's `EXTRACT(<unit> FROM interval)` works for all the units
BigQuery exposes (YEAR, MONTH, DAY, HOUR, MINUTE, SECOND,
MILLISECOND, MICROSECOND). The interval components are returned
faithfully — DuckDB folds e.g. `1 year 14 months` to `2 years
5 months` when emitting through Python's `timedelta`, but the
underlying interval values are preserved.

## Output format

When a query projects an `INTERVAL` value, the REST wire format uses
the BigQuery-canonical `Y-M D H:M:S[.ffffff]` string. The emulator
formats it via
[`bqemulator.types.interval.format_bq_interval`](../api.md).

## See also

* [ADR 0019 — Specialized types](../adr/0019-specialized-types.md)
* [Architecture: specialized types](../architecture/specialized-types.md)
* [GEOGRAPHY guide](./geography-spatial.md)
* [RANGE guide](./range-types.md)
