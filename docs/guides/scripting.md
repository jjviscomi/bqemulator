# Procedural scripting

bqemulator's scripting engine implements BigQuery's procedural SQL
surface via a tree-walking interpreter (see
[ADR 0011](../adr/0011-tree-walking-scripting-interpreter.md) and
[ADR 0015](../adr/0015-scripting-execution-model.md)).

Every multi-statement job — or any job containing a control-flow
keyword — is routed through the interpreter. Single-statement ``SELECT``
queries follow the same fast path as ordinary queries, so you pay no
cost for ordinary workloads.

## Supported constructs

| Construct | Example |
|---|---|
| ``DECLARE`` | ``DECLARE x INT64 DEFAULT 0;`` |
| ``SET`` | ``SET x = x + 1;`` / ``SET (a, b) = (SELECT 1, 2);`` |
| ``IF`` / ``ELSEIF`` / ``ELSE`` | ``IF x > 0 THEN … ELSEIF x < 0 THEN … ELSE … END IF;`` |
| ``WHILE`` | ``WHILE x < 10 DO … END WHILE;`` |
| ``LOOP`` | ``LOOP … BREAK; … END LOOP;`` |
| ``FOR`` | ``FOR row IN (SELECT …) DO … END FOR;`` |
| ``BREAK`` / ``LEAVE`` | Exit the nearest loop. |
| ``CONTINUE`` / ``ITERATE`` | Skip to the next iteration. |
| ``BEGIN`` / ``END`` | Lexical scope + optional exception handler. |
| ``EXCEPTION WHEN ERROR THEN`` | Catch any ``DomainError`` raised in the block. |
| ``RAISE`` | ``RAISE USING MESSAGE = 'oops';`` |
| ``CALL`` | ``CALL my_ds.proc(arg1, arg2);`` |
| ``EXECUTE IMMEDIATE`` | ``EXECUTE IMMEDIATE 'SELECT ?' INTO v USING 42;`` |
| ``RETURN`` | Exit the current procedure (optionally with a value). |
| ``CREATE [OR REPLACE] FUNCTION/PROCEDURE`` | Registers the routine. |

## Quick start

```sql
DECLARE total INT64 DEFAULT 0;
FOR order_row IN (SELECT amount FROM sales.orders) DO
  SET total = total + order_row.amount;
END FOR;
SELECT total;
```

## Variable references

Script variables are referenced in expressions by their declared name —
**without** the BigQuery ``@`` prefix (which is reserved for
query-parameter binding):

```sql
DECLARE n INT64 DEFAULT 5;
SELECT n * 2 AS v;  -- correct
-- SELECT @n * 2;   -- wrong: @n is a query parameter
```

The interpreter walks every SQL statement inside the script, finds
column references that match a declared variable, and substitutes a
DuckDB positional placeholder (``?``) bound to the current value. No
user string ever reaches DuckDB unescaped, so scripting is safe to use
with untrusted inputs.

## Exception handling

``BEGIN... EXCEPTION WHEN ERROR THEN... END`` wraps any block. A
``DomainError`` raised by any statement inside the block — including
DuckDB execution errors, translator errors, and ``RAISE`` — is caught
by the handler. Unmatched errors propagate as a job failure.

```sql
DECLARE log_message STRING DEFAULT 'ok';
BEGIN
  SELECT CAST('not a number' AS INT64);
EXCEPTION WHEN ERROR THEN
  SET log_message = 'caught';
END;
SELECT log_message;  -- 'caught'
```

Inside the handler the implicit variable ``__error_message__`` holds
the raised error's message.

## Dynamic SQL

``EXECUTE IMMEDIATE`` builds a SQL string at runtime. Positional
``USING`` values and ``INTO`` assignment work just as in BigQuery:

```sql
DECLARE name STRING;
EXECUTE IMMEDIATE
  'SELECT name FROM users WHERE id = ?'
  INTO name
  USING 42;
SELECT name;
```

``INTO`` rejects multi-row results (``InvalidQueryError``).

## CALL and stored procedures

Procedures open a fresh frame; they don't see the caller's locals.

```sql
CREATE OR REPLACE PROCEDURE my_ds.square(x INT64)
BEGIN
  SELECT x * x AS sq;
END;

CALL my_ds.square(7);  -- answer: 49
```

Return values from a ``RETURN`` inside a procedure exit the procedure
without stopping the outer script.

## Quotas

| Environment variable | Default |
|---|---|
| ``BQEMU_SCRIPTING_MAX_STATEMENTS`` | 10 000 |
| ``BQEMU_SCRIPTING_MAX_LOOP_ITERATIONS`` | 1 000 000 |

Exceeding either cap raises ``QuotaExceededError`` (HTTP 429), matching
BigQuery's quota-error shape.

## Script statistics

Jobs that run through the interpreter return a
``statistics.scriptStatistics`` block:

```json
{
  "scriptStatistics": {
    "statementCount": "42",
    "evaluationKind": "STATEMENT"
  }
}
```

The count reflects every *executed* statement (including those inside
loops), not just the lexical count.
