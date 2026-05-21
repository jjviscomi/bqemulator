# ADR 0011: Tree-walking interpreter for BigQuery procedural scripting

- **Status**: Accepted

## Context

BigQuery supports a scripting language (DECLARE, SET, IF, WHILE, LOOP,
FOR, BREAK, CONTINUE, BEGIN/END, EXCEPTION WHEN, CALL, EXECUTE IMMEDIATE,
RETURN) embedded in the query language. DuckDB has limited procedural
SQL.

Options:

1. **Translate to DuckDB procedural SQL** — rejected, DuckDB procedural
   SQL is far less capable than BigQuery's.
2. **Tree-walking interpreter in Python** — parse the script, walk the
   AST, dispatch each statement either as a SQLGlot-translated query to
   DuckDB or as an interpreted control-flow statement in Python.
3. **Decompose into sequential queries** — impossible for control flow.

## Decision

Tree-walking interpreter in `bqemulator.scripting`:

- `parser.py` uses SQLGlot's BigQuery dialect to parse the full script.
- `interpreter.py` walks the AST, maintains a lexically-scoped variable
  frame stack in `frames.py`, and handles exception propagation in
  `exceptions.py`.
- Each SQL statement within the script is translated and executed
  individually by the existing `SQLTranslator` + `DuckDBEngine`.
- `EXECUTE IMMEDIATE` concatenates a dynamic SQL string, translates it,
  and runs it.

## Consequences

- **Positive**: correct by construction for control flow; reuses the
  existing SQL translation pipeline for DML / DQL within the script.
- **Positive**: debuggable — each step of the script produces a structured
  log event.
- **Negative**: `BEGIN TRANSACTION` / `COMMIT` inside scripts requires
  coordination with the transaction manager; complexity contained in
  `bqemulator.transactions`.
