# Adding a SQL function (translation rule)

Every BigQuery-specific SQL construct we translate is a single class in
`src/bqemulator/sql/rules/<group>.py`. Here is the full workflow.

## 1. Write the rule

```python
# src/bqemulator/sql/rules/safe_math.py
from sqlglot import exp

from bqemulator.sql.rules._base import TranslationRule


class SafeDivideRule(TranslationRule):
    """SAFE_DIVIDE(a, b) -> CASE WHEN b = 0 THEN NULL ELSE a / b END."""

    name = "SAFE_DIVIDE"

    def applies_to(self, node: exp.Expression) -> bool:
        return (
            isinstance(node, exp.Anonymous)
            and node.this.upper() == "SAFE_DIVIDE"
        )

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        a, b = node.expressions
        return exp.If(
            this=exp.EQ(this=b, expression=exp.Literal.number(0)),
            true=exp.Null(),
            false=exp.Div(this=a, expression=b),
        )
```

## 2. Register it

Rules self-register in the module's import. Make sure
`src/bqemulator/sql/rules/__init__.py` imports the new module:

```python
from bqemulator.sql.rules import safe_math  # noqa: F401
```

## 3. Unit test

```python
# tests/unit/sql/rules/test_safe_math.py
from bqemulator.sql.translator import SQLTranslator

def test_safe_divide_rewrites_to_case():
    t = SQLTranslator()
    assert t.translate("SELECT SAFE_DIVIDE(a, b) FROM t") == (
        "SELECT CASE WHEN b = 0 THEN NULL ELSE a / b END FROM t"
    )
```

## 4. Conformance test

Add a canonical fixture at
`tests/conformance/sql_corpus/safe_divide.sql`:

```sql
-- expected_snapshot: safe_divide
SELECT SAFE_DIVIDE(10.0, 0) AS zero_case,
       SAFE_DIVIDE(10.0, 4) AS normal_case;
```

The conformance runner diffs this against a saved snapshot of real
BigQuery's output.

## 5. Update docs

- Add an entry to `docs/reference/sql-function-mapping.md` (auto-generated
  — just run `make matrix`).
- Update `docs/reference/compatibility-matrix.md` if the feature moves
  from 🚧 to ✅.
- Add a CHANGELOG entry under `Unreleased`.
