"""SQL rewriter passes.

Rewriters transform the BigQuery AST BEFORE it reaches SQLGlot's
transpiler. Each rewriter is a function that takes a parsed SQLGlot
AST (in BigQuery dialect) and returns a (possibly modified) AST.

Phase 1 ships this package empty — the first rewriters (partition
pruning, wildcard expansion, pseudo-column injection) land in Phase 3.
"""

from __future__ import annotations
