# ADR 0015: Scripting execution model — lexer/parser/AST/interpreter with signal-based control flow

- **Status**: Accepted

## Context

[ADR 0011](0011-tree-walking-scripting-interpreter.md) locked the
tree-walking interpreter for BigQuery procedural scripting. That ADR
left open concrete design points that must be decided before
implementation:

1. How to parse BigQuery scripting syntax given that SQLGlot's parser
   falls back to a raw `Command` node for most control-flow constructs.
2. How non-local control flow (BREAK, CONTINUE, RETURN, exception
   propagation) flows through the interpreter.
3. How lexical scope works across nested BEGIN/END blocks and across
   procedure calls.
4. How dynamic SQL (`EXECUTE IMMEDIATE`) evaluates parameters and
   returns result rows.

Three implementation options were considered:

1. **Pure SQLGlot fallback.** Rely on `sqlglot.parse` and treat every
   fallback `Command` node as opaque. Rejected: SQLGlot rolls IF/WHILE
   bodies into a single `Command`, so we can never walk into them.
2. **Preprocess to one-statement-per-call.** Split the script on
   semicolons, feed each statement to the translator. Rejected:
   control flow boundaries (`IF... END IF`) span multiple statements.
3. **Custom lexer + recursive-descent parser + AST nodes.** Tokenise
   with a small lexer that understands BigQuery keywords and strings,
   parse into a typed AST covering every Phase 6 scripting construct,
   walk the AST.

## Decision

Option 3: a small self-contained lexer + parser + AST + interpreter
in `bqemulator.scripting`.

### Module layout

```
scripting/
├── ast.py          # frozen dataclasses: Statement + Expression hierarchies
├── lexer.py        # token stream with BigQuery-aware string/identifier rules
├── parser.py       # recursive-descent parser → AST
├── frames.py       # FrameStack with push/pop/declare/set/lookup
├── exceptions.py   # ScriptRaise + control-flow signals
└── interpreter.py  # walks AST + executes SQL statements via the engine
```

### Parser scope

Covers the full Phase 6 surface:

- **Declarations:** `DECLARE name [, name]* TYPE [DEFAULT expr];`
- **Assignment:** `SET name = expr;` and `SET (a, b) = (SELECT...);`
- **Conditionals:** `IF expr THEN... ELSEIF expr THEN... ELSE... END IF;`
- **Loops:** `WHILE expr DO... END WHILE;`, `LOOP... END LOOP;`,
  `FOR name IN (SELECT...) DO... END FOR;`
- **Branch control:** `BREAK;` / `LEAVE;`, `CONTINUE;` / `ITERATE;`
- **Blocks:** `BEGIN... [EXCEPTION WHEN ERROR THEN...] END;`
- **Dynamic SQL:** `EXECUTE IMMEDIATE sql_expr [INTO names] [USING values];`
- **Invocations:** `CALL proj.ds.proc(args);`, `RETURN [expr];`
- **Nested DDL:** `CREATE [OR REPLACE] [TEMP] FUNCTION/PROCEDURE...`

Every statement not recognised by the scripting parser is passed through
to the existing SQL translator as a single SQL statement. This keeps
the parser narrowly focused on control flow and defers every data-plane
statement (SELECT, INSERT, UPDATE, MERGE, DELETE, TRUNCATE, CREATE TABLE)
to the SQL pipeline. If the body is a single SQL statement, the script
interpreter is not even entered.

### Signal-based control flow

Non-local transfer is represented by exceptions that inherit from a
module-private `_ControlSignal` base (not a `DomainError`):

- `BreakSignal` — caught by the loop frame.
- `ContinueSignal` — caught by the loop frame.
- `ReturnSignal(value)` — caught by the procedure-call frame; bubbles
  up through nested loops/blocks without being absorbed.
- `ScriptRaise(domain_error)` — caught by the nearest matching
  `EXCEPTION WHEN` handler.

Because every signal is an exception, the interpreter can let them
bubble up through nested `execute_*` dispatch methods; each construct
that should absorb a given signal simply catches it at the right layer.
Any `DomainError` raised during SQL execution is wrapped in a
`ScriptRaise` so handlers match it uniformly.

### Frame stack + lexical scope

- `Frame` holds a dict of name → value and a reference to its parent.
- `FrameStack.push()` opens a new frame; `pop()` discards it.
- `declare(name, type, default)` inserts in the *current* frame only;
  shadowing an outer name is a parse-time error to match BigQuery.
- `set(name, value)` walks outward to find the first frame owning the
  name; `SET nonexistent =...` raises an `InvalidQueryError`.
- `lookup(name)` walks outward; unresolved names become
  `InvalidQueryError`. Type coercion uses the declared BigQuery type.

Procedures open a new frame with only the parameter bindings — they do
not see the caller's locals. This matches BigQuery's stored-procedure
scoping (arguments + session variables, nothing else).

### Expression evaluator

Variables inside scripting expressions are resolved by the interpreter
*before* the SQL is handed to the engine: every `@var_name` reference
in a SELECT / SET / INSERT body is rewritten to a DuckDB `$param` and
the resolved value is passed as a bound parameter. This lets ordinary
SQL use script variables without a string-concat vulnerability.

### EXECUTE IMMEDIATE

`EXECUTE IMMEDIATE sql_expr` first evaluates `sql_expr` to a string,
then runs the string through the *full* translation pipeline (wildcard
expander + translator + table rewriter). `USING v1, v2` binds
positional parameters; `INTO v1, v2` writes the first row of the result
into the named variables (error on multi-row results).

### Result accumulation

The top-level `run_script(ctx, sql)` returns the final SELECT result
(if the last executed statement was a query), matching BigQuery's
scripting job statistics shape. Earlier queries emit a structured log
event but do not accumulate into an output table — jobs only stream
back one result at a time.

## Consequences

- **Positive:** Correct by construction across every Phase 6 scripting
  construct. No hidden fallbacks.
- **Positive:** Every data-plane statement still goes through the
  existing SQL rule registry — one pipeline, one place to audit.
- **Positive:** Signals are plain exceptions → Python's existing
  stack-unwinding semantics drive the right behaviour without a bespoke
  trampoline.
- **Positive:** SQL injection is impossible at the scripting/SQL
  boundary because script variables always reach DuckDB as bound
  parameters.
- **Negative:** A small custom parser is code we now own. Mitigated
  by: aggressive unit coverage + Hypothesis property tests +
  conformance tests against real BigQuery scripting output.
- **Negative:** Scripts that rely on BigQuery's raw error-message text
  will see a different (but stable) shape from the emulator. Documented
  in the scripting guide.
