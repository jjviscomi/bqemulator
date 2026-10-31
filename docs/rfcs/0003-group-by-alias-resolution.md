---
rfc: "0003"
title: "GROUP BY <alias> resolution"
status: Draft
authors:
  - "@cozima0210"
created: 2026-09-18
updated: 2026-09-18
supersedes: null
superseded-by: null
---

# RFC 0003: GROUP BY \<alias\> resolution

## Summary

Resolve a bare (unqualified) `GROUP BY` item that names one of the query's own
`SELECT`-list aliases to that alias's ordinal position, matching BigQuery's
"`GROUP BY` clauses may also refer to aliases" rule, instead of letting
DuckDB's binder resolve it against a same-named real column first. Alias
matching follows BigQuery's identifier rules — always case-insensitive,
regardless of backtick quoting on either side — and raises an error, not a
guess, when the `GROUP BY` item names an alias that the `SELECT` list
assigned to more than one projection.

## Motivation

DuckDB's binder resolves a bare `GROUP BY` identifier against the
`FROM`-clause's real columns *before* falling back to a `SELECT`-list alias of
the same name — the opposite priority of BigQuery's alias rule, which always
means "group by this `SELECT`-list slot" regardless of what real column
shares its name. A query that aliases an output column to a name that also
exists as a real column reachable through the join graph either silently
groups by the wrong column or trips DuckDB's `"column ... must appear in the
GROUP BY clause"` binder error — reduced from a real production
`weekly-hours` report:

```sql
SELECT pr.id AS project_id, pr.name AS project_name
FROM placements pl
JOIN tasks t ON t.id = pl.task_id
JOIN processes proc ON proc.id = t.process_id
JOIN projects pr ON pr.id = proc.project_id
GROUP BY project_id, project_name
```

`project_id` here is meant to reference the `SELECT`-list alias (`pr.id`), but
`processes.project_id` is a real column two joins closer to the `FROM`
clause, and DuckDB's binder tries that one first.

## Guide-level explanation

When the emulator translates a query, any `GROUP BY` item that is a bare
column reference (no table qualifier) and matches one of that same
`SELECT`'s own output aliases is rewritten to the alias's 1-based ordinal
position before the query reaches DuckDB. An ordinal is positional, not
name-based, so it carries no ambiguity with a real column of the same name.

```sql
-- as written
SELECT pr.id AS project_id, pr.name AS project_name, ...
GROUP BY project_id, project_name

-- as executed
SELECT pr.id AS project_id, pr.name AS project_name, ...
GROUP BY 1, 2
```

Matching follows BigQuery's own identifier-resolution rules:

- **Always case-insensitive, quoted or not.** `GROUP BY project_id` matches
  `AS Project_ID`, and a backtick-quoted `GROUP BY \`Project_ID\`` matches
  an unquoted `AS project_id` just the same — backticks are identifier
  *syntax* (escaping reserved words, allowing special characters), not a
  switch to case-sensitive matching, unlike some other SQL dialects.
- **Ambiguous when the alias is duplicated and referenced.** BigQuery
  permits a `SELECT` list to reuse the same alias on more than one
  projection, *as long as nothing in the query refers to it*. A `GROUP BY`
  item naming a duplicated alias is such a reference, and is rejected with an
  `invalidQuery` error rather than silently resolved to whichever projection
  happened to be assigned to the alias last.

A `GROUP BY` item that names no `SELECT`-list alias — because it's a plain
column reference, or a table-qualified reference, or doesn't match any
alias — is left untouched and resolves the normal way.

## Reference-level explanation

Implemented in `SQLTranslator._expand_group_by_aliases_to_ordinals`
(`src/bqemulator/sql/translator.py`), run unconditionally in `_apply_rules`
against the post-transpile DuckDB AST, before the schema-aware `qualify()` /
`annotate_types()` pass (which performs the same column-before-alias lookup
DuckDB's binder does, so it must not see the ambiguous form first) and before
the per-node rule walk.

For each `SELECT` with a `GROUP BY` clause:

1. Build a map from alias key to 1-based `SELECT`-list ordinal, over every
   projection that is an explicit `exp.Alias`. The key is the alias's
   identifier text, lower-cased regardless of quoting. A second alias
   producing an already-seen key marks that key ambiguous instead of
   overwriting the first ordinal.
2. For each `GROUP BY` item that is a bare (no table-qualifier) column
   reference, compute the same lower-cased key from its own identifier.
   - If the key is marked ambiguous, raise `InvalidQueryError` ("Column name
     `<name>` is ambiguous").
   - Else if the key matches a recorded alias, replace the item with a
     `exp.Literal` ordinal pointing at that alias's `SELECT`-list position.
   - Else leave the item untouched.

An implicit (un-aliased) `SELECT`-list column is not itself a candidate key —
BigQuery's alias-priority rule is specifically about aliases introduced by
`AS`; a bare `SELECT pr.id ... GROUP BY id` has no alias to prioritize over
the real column, and DuckDB's ordinary name-based resolution already picks
the same column BigQuery would.

### Error shape

The ambiguous-duplicate-alias case returns a `400` `InvalidQueryError`
(`bq_reason: invalidQuery`), matching how the emulator reports other
semantic-analysis failures caught before DuckDB ever sees the query.

## Drawbacks

- The rewrite runs on every `SELECT` with a `GROUP BY`, adding a bounded
  linear-in-projections pass even when no alias collision exists (mitigated
  by the early `if not alias_positions: continue` skip).
- Ordinal rewriting changes the emitted DuckDB SQL text, which callers that
  inspect the translated query (rather than only its result) would see
  differently from the BigQuery source. No known caller does this today.

## Rationale and alternatives

**Alternative: qualify every real column instead of rewriting `GROUP BY`.**
Considered and rejected — the ambiguity is inherent to DuckDB's binder
priority, not to how the *other* columns are written; qualifying the `FROM`-
clause columns doesn't change which one a bare, unqualified `GROUP BY`
identifier binds to.

**Alternative: raise `InvalidQueryError` for every alias/column collision,
matching BigQuery's `ambiguous alias` error for the general case.** BigQuery
does error when a `GROUP BY`/`ORDER BY` name is ambiguous *and does not
resolve to the same underlying object* — but the collision this RFC targets
is the specific, common case where the intended target is unambiguous to a
human reader (the query clearly means "my alias") even though DuckDB's binder
would pick the wrong object. Rewriting to an ordinal preserves that intent
instead of rejecting a query real BigQuery accepts and runs correctly.

## Prior art

BigQuery's own documentation, ["Query syntax" / "Aliases"](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/query-syntax#using_aliases):
"`GROUP BY` clauses may also refer to aliases. If a query contains aliases in
the `SELECT` clause, those aliases override names in the corresponding `FROM`
clause," and the same page's "Duplicate aliases" / "Ambiguous aliases"
sections, which this RFC's duplicate-alias behavior mirrors directly.

## Unresolved questions

None at the time of writing.

## Future possibilities

- Extending ordinal rewriting to `HAVING`/`ORDER BY` alias references that
  hit the same DuckDB binder priority, if a concrete failing query surfaces
  one (`ORDER BY` already benefits from DuckDB's own alias-friendly default;
  no reproduction exists yet for `HAVING`).
- Deriving a key for implicit (path-expression) aliases if a concrete
  BigQuery query is found where DuckDB's own implicit-alias inference
  diverges from BigQuery's.
