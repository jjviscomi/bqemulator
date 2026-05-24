"""SQL rewriter passes.

Rewriters transform the BigQuery AST BEFORE it reaches SQLGlot's
transpiler. Each rewriter is a function that takes a parsed SQLGlot
AST (in BigQuery dialect) and returns a (possibly modified) AST.
"""

from __future__ import annotations
