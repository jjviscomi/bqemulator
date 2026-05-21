# Scripting interpreter

Implementation in `src/bqemulator/scripting/`. See
[ADR 0011](../adr/0011-tree-walking-scripting-interpreter.md).

## Parse

`parser.py` uses SQLGlot's BigQuery dialect to parse the entire script
into a single AST rooted at a `Script` node. Multi-statement scripts
produce a tree with child statements.

## Interpret

`interpreter.py` walks the tree. For each node:

- **DECLARE**: allocate a variable in the current frame with the
  declared type; initialize if a DEFAULT clause is present.
- **SET**: evaluate an expression and assign to a named variable.
- **IF / ELSEIF / ELSE**: evaluate the predicate; execute the matching
  branch.
- **WHILE / LOOP / FOR**: Python-level loop driving the body.
- **BREAK / CONTINUE**: thrown as typed exceptions caught by the
  enclosing loop.
- **BEGIN … EXCEPTION WHEN … END**: exception boundary; raised errors
  are matched against WHEN handlers and routed.
- **CALL**: invoke a stored procedure (resolved from the catalog).
- **EXECUTE IMMEDIATE**: translate + execute a dynamic SQL string.
- **RETURN**: exit the enclosing procedure.
- **SQL statement** (SELECT, INSERT, UPDATE, …): translate and execute
  through the standard SQL pipeline.

## Scoping

`frames.py` maintains a stack of lexical scopes. Each BEGIN/END and each
procedure invocation pushes a new frame. Variable resolution walks the
stack outward.

## Error mapping

Uncaught script errors map to `InvalidQueryError` (syntax) or the
appropriate domain error subclass. Errors caught by a handler are
swallowed and execution continues with the handler block.
