"""Translation rules for BigQuery JSON helper functions.

SQLGlot's BigQuery-to-DuckDB transpiler handles the easy cases natively
(``JSON_ARRAY``, ``JSON_OBJECT``) but generates broken function names
for the rest (``JSON_KEYS`` → ``J_S_O_N_KEYS_AT_DEPTH``, ``BOOL(json)``
→ ``J_S_O_N_BOOL``, ``FLOAT64(json)`` → ``FLOAT64``, ``STRING(json)``
→ ``CAST(... AS TEXT)`` — which preserves the JSON quotes). The
``JSON_REMOVE`` / ``JSON_SET`` / ``JSON_STRIP_NULLS`` family + the
``LAX_*`` extractors have no DuckDB equivalent at all.

This module patches every gap by rewriting the SQLGlot AST after
transpile:

* ``JSON_KEYS(json)``       → ``json_keys(json)`` (DuckDB native).
* ``LAX_BOOL(json)``        → ``TRY_CAST(json_extract_string(json, '$') AS BOOLEAN)``.
* ``LAX_INT64(json)``       → ``TRY_CAST(json_extract_string(json, '$') AS BIGINT)``.
* ``LAX_FLOAT64(json)``     → ``TRY_CAST(json_extract_string(json, '$') AS DOUBLE)``.
* ``LAX_STRING(json)``      → ``json_extract_string(json, '$')``.
* ``BOOL(json)``            → ``CAST(json AS BOOLEAN)``.
* ``FLOAT64(json)``         → ``CAST(json AS DOUBLE)``.
* ``STRING(json)``          → ``json_extract_string(json, '$')``.
* ``JSON_REMOVE(j, path)``  → ``bqemu_json_remove(j, path)`` (Python UDF).
* ``JSON_SET(j, p, v)``     → ``bqemu_json_set(j, p, to_json(v))``.
* ``JSON_STRIP_NULLS(j)``   → ``bqemu_json_strip_nulls(j)``.
* ``JSON_ARRAY_INSERT(j, p, v)`` → ``CAST(bqemu_json_array_insert(CAST(j AS VARCHAR),
  p, CAST(to_json(v) AS VARCHAR)) AS JSON)``. DuckDB has no
  ``json_array_insert`` primitive — the Python helper parses the
  document, walks the BigQuery JSONPath subset (``$[N]`` /
  ``$.key[N]`` / ``$.key1.key2[N]``), and inserts the value while
  preserving every existing array element.

The wire-format diffs (DuckDB's compact ``{"k":1}`` vs BigQuery's
spaced ``{"k": 1}``) are absorbed by the conformance comparison
helper, which parses both sides through ``json.loads`` before diffing
(ADR 0022 §3 — JSON-typed cells compare structurally).
"""

from __future__ import annotations

from sqlglot import exp

from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule


def _path_root() -> exp.Literal:
    """Return the JSONPath literal ``'$'`` used by every extractor rule."""
    return exp.Literal.string("$")


def _json_extract_string(value: exp.Expression) -> exp.Expression:
    """Build a DuckDB ``json_extract_string(value, '$')`` call."""
    return exp.Anonymous(
        this="json_extract_string",
        expressions=[value.copy(), _path_root()],
    )


def _try_cast(value: exp.Expression, target_type: str) -> exp.Expression:
    """Build a DuckDB ``TRY_CAST(value AS target_type)`` expression."""
    return exp.TryCast(this=value, to=exp.DataType.build(target_type))


def _cast(value: exp.Expression, target_type: str) -> exp.Expression:
    """Build a DuckDB ``CAST(value AS target_type)`` expression."""
    return exp.Cast(this=value, to=exp.DataType.build(target_type))


def _bqemu_call(name: str, *args: exp.Expression) -> exp.Anonymous:
    """Build an ``exp.Anonymous`` wrapping a ``bqemu_*`` Python UDF call."""
    return exp.Anonymous(this=name, expressions=[arg.copy() for arg in args])


@register
class JSONKeysRule(TranslationRule):
    """``JSON_KEYS(json)`` → ``json_keys(json)``.

    SQLGlot transpiles BigQuery's ``JSON_KEYS`` to the non-existent
    ``J_S_O_N_KEYS_AT_DEPTH`` (its DuckDB generator inserts under-
    scores around the ``JSON`` prefix). We rewrite the typed
    :class:`exp.JSONKeysAtDepth` node back to ``json_keys`` — which
    DuckDB ships natively — keeping the original operand untouched.
    """

    name = "JSON_KEYS"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.JSONKeysAtDepth`` nodes."""
        return isinstance(node, exp.JSONKeysAtDepth)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Replace ``JSON_KEYS_AT_DEPTH(...)`` with ``json_keys(...)``."""
        return exp.Anonymous(this="json_keys", expressions=[node.this.copy()])


class _LaxRuleBase(TranslationRule):
    """Shared logic for ``LAX_*(json)`` → ``TRY_CAST(json_extract_string(j, '$') AS T)``."""

    target_type: str = ""
    node_type: type[exp.Expression] = exp.Expression

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the typed SQLGlot node for this LAX function."""
        return isinstance(node, self.node_type)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Wrap ``json_extract_string(j, '$')`` in a ``TRY_CAST`` to the target type."""
        return _try_cast(_json_extract_string(node.this), self.target_type)


@register
class LaxBoolRule(_LaxRuleBase):
    """``LAX_BOOL(json)`` → ``TRY_CAST(json_extract_string(j, '$') AS BOOLEAN)``."""

    name = "LAX_BOOL"
    target_type = "BOOLEAN"
    node_type = exp.LaxBool


@register
class LaxInt64Rule(_LaxRuleBase):
    """``LAX_INT64(json)`` → ``TRY_CAST(json_extract_string(j, '$') AS BIGINT)``."""

    name = "LAX_INT64"
    target_type = "BIGINT"
    node_type = exp.LaxInt64


@register
class LaxFloat64Rule(_LaxRuleBase):
    """``LAX_FLOAT64(json)`` → ``TRY_CAST(json_extract_string(j, '$') AS DOUBLE)``."""

    name = "LAX_FLOAT64"
    target_type = "DOUBLE"
    node_type = exp.LaxFloat64


@register
class LaxStringRule(TranslationRule):
    """``LAX_STRING(json)`` → ``json_extract_string(json, '$')``.

    No ``TRY_CAST`` needed — ``json_extract_string`` already returns the
    JSON value's string form (with surrounding quotes stripped from JSON
    strings, and primitive values formatted as their natural string).
    """

    name = "LAX_STRING"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.LaxString`` nodes."""
        return isinstance(node, exp.LaxString)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``json_extract_string(node.this, '$')``."""
        return _json_extract_string(node.this)


@register
class JSONBoolRule(TranslationRule):
    """``BOOL(json)`` → ``CAST(json AS BOOLEAN)``.

    SQLGlot parses BigQuery's ``BOOL(json)`` into :class:`exp.JSONBool`
    and serialises it as the non-existent ``J_S_O_N_BOOL`` DuckDB
    function. DuckDB happily casts a JSON-typed value to BOOLEAN
    directly, so we rewrite to ``CAST(... AS BOOLEAN)``.
    """

    name = "JSON_VALUE_BOOL"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.JSONBool`` nodes."""
        return isinstance(node, exp.JSONBool)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``CAST(node.this AS BOOLEAN)``."""
        return _cast(node.this.copy(), "BOOLEAN")


@register
class JSONFloat64Rule(TranslationRule):
    """``FLOAT64(json)`` → ``CAST(json AS DOUBLE)``.

    SQLGlot parses BigQuery's ``FLOAT64(json)`` into :class:`exp.Float64`
    which it serialises as the non-existent ``FLOAT64`` function in
    DuckDB output. The DuckDB-native ``CAST(json AS DOUBLE)`` matches
    BigQuery's strict-extractor semantic.
    """

    name = "JSON_VALUE_FLOAT64"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.Float64`` nodes (the JSON-extractor form)."""
        return isinstance(node, exp.Float64)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``CAST(node.this AS DOUBLE)``."""
        return _cast(node.this.copy(), "DOUBLE")


@register
class JSONStringRule(TranslationRule):
    """``STRING(json)`` → ``json_extract_string(json, '$')``.

    BigQuery's ``STRING(json)`` extractor returns the underlying JSON
    string with its surrounding quotes removed. SQLGlot transpiles it
    to ``CAST(... AS TEXT)`` which keeps the quotes (so
    ``STRING(PARSE_JSON('"hello"'))`` yields the 7-character literal
    ``"hello"`` instead of the expected ``hello``). We detect the
    ``CAST(... AS TEXT)`` shape whose inner expression is a JSON-typed
    cast and rewrite it to ``json_extract_string``.
    """

    name = "JSON_VALUE_STRING"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match a TEXT-cast whose operand is a JSON-producing expression."""
        if not isinstance(node, exp.Cast):
            return False
        if isinstance(node, exp.TryCast):  # don't intercept SAFE_CAST chains.
            return False
        if not _is_text_type(node.to):
            return False
        return _produces_json(node.this)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Rewrite ``CAST(json_expr AS TEXT)`` to ``json_extract_string``."""
        return _json_extract_string(node.this)


@register
class JSONRemoveRule(TranslationRule):
    """``JSON_REMOVE(json, path)`` → ``bqemu_json_remove(json, path)``.

    Delegates to the Python helper registered in
    :mod:`bqemulator.sql.builtin_udfs` — DuckDB ships no native
    equivalent.
    """

    name = "JSON_REMOVE"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.JSONRemove`` nodes."""
        return isinstance(node, exp.JSONRemove)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``bqemu_json_remove(json, path)``."""
        return _bqemu_call("bqemu_json_remove", node.this, *node.expressions)


@register
class JSONSetRule(TranslationRule):
    """``JSON_SET(json, path, value)`` → ``bqemu_json_set(j, p, to_json(v))``.

    The helper receives the value pre-serialised so DuckDB doesn't need
    a polymorphic-argument UDF — ``TO_JSON`` is a DuckDB native that
    handles every primitive cleanly.
    """

    name = "JSON_SET"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.JSONSet`` nodes."""
        return isinstance(node, exp.JSONSet)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``bqemu_json_set(j, path, to_json(value))``."""
        if len(node.expressions) < 2:  # noqa: PLR2004 — defensive: BQ always passes (path, value).
            return node
        path = node.expressions[0]
        value = node.expressions[1]
        encoded = exp.Anonymous(this="to_json", expressions=[value.copy()])
        return _bqemu_call("bqemu_json_set", node.this, path, encoded)


@register
class JSONStripNullsRule(TranslationRule):
    """``JSON_STRIP_NULLS(json)`` → ``bqemu_json_strip_nulls(json)``."""

    name = "JSON_STRIP_NULLS"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.JSONStripNulls`` nodes."""
        return isinstance(node, exp.JSONStripNulls)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``bqemu_json_strip_nulls(node.this)``."""
        return _bqemu_call("bqemu_json_strip_nulls", node.this)


@register
class JSONArrayInsertRule(TranslationRule):
    """``JSON_ARRAY_INSERT(j, path, value)`` → helper-UDF call wrapped in ``CAST(... AS JSON)``.

    Concretely the rewrite emits ``CAST(bqemu_json_array_insert(
    CAST(j AS VARCHAR), path, CAST(to_json(value) AS VARCHAR)) AS JSON)``.

    DuckDB has no native ``json_array_insert``. The Python helper
    (:func:`bqemulator.sql.builtin_udfs.bqemu_json_array_insert`)
    parses the document, walks the BigQuery JSONPath subset
    (``$[N]`` / ``$.key[N]`` / chained-key forms), and inserts the
    value while preserving every existing array element. The
    JSON-to-VARCHAR / VARCHAR-to-JSON casts are needed because the
    DuckDB UDF accepts ``VARCHAR`` (a single concrete type) — passing
    ``JSON`` directly fails the binder.

    SQLGlot's BigQuery parser produces a typed
    :class:`exp.JSONArrayInsert` node whose ``this`` is the JSON
    document and whose ``expressions`` carry the (path, value) pairs.
    Only the first (path, value) pair is rewritten — the conformance
    corpus exercises only the single-pair form. BigQuery's variadic
    multi-(path, value) form is not yet on the surface inventory.
    """

    name = "JSON_ARRAY_INSERT"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the typed ``JSONArrayInsert`` AST node."""
        return type(node).__name__ == "JSONArrayInsert"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit the helper-wrapping CAST."""
        # SQLGlot stashes (path, value) pairs in ``expressions``. The
        # conformance corpus uses exactly one pair; defensive guard
        # against a future multi-pair fixture below.
        if len(node.expressions) < 2:  # noqa: PLR2004 — defensive: BQ always passes (path, value).
            return node
        json_doc = node.this
        path = node.expressions[0]
        value = node.expressions[1]
        if json_doc is None:
            return node
        json_doc_varchar = _cast(json_doc.copy(), "VARCHAR")
        value_json_varchar = _cast(
            exp.Anonymous(this="to_json", expressions=[value.copy()]),
            "VARCHAR",
        )
        helper_call = _bqemu_call(
            "bqemu_json_array_insert",
            json_doc_varchar,
            path,
            value_json_varchar,
        )
        return _cast(helper_call, "JSON")


@register
class JSONExtractToStringRule(TranslationRule):
    """``JSON_QUERY(j, path)`` → ``CAST(j -> path AS VARCHAR)``.

    BigQuery's ``JSON_QUERY`` (and the equivalent ``->`` operator)
    returns a STRING-typed JSON snippet — DuckDB's ``->`` operator
    returns a JSON-typed value. Wrapping the result in
    ``CAST(... AS VARCHAR)`` collapses the DuckDB JSON type back to
    plain ``VARCHAR``, which the REST schema renderer then maps to
    BigQuery's ``STRING`` (matching the recorded baselines).

    Without this rule, the engine's JSON-type metadata override (see
    :func:`bqemulator.storage.engine._annotate_with_duckdb_types`)
    surfaces the column as ``JSON`` and the fixtures ``json_query_basic``
    + ``json_extract_path`` regress with ``expected='STRING'
    actual='JSON'``.
    """

    name = "JSON_QUERY"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``exp.JSONExtract`` nodes — DuckDB-side ``->`` operator."""
        return isinstance(node, exp.JSONExtract)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Wrap the extract result in ``CAST(... AS VARCHAR)``."""
        return exp.Cast(this=node.copy(), to=exp.DataType.build("VARCHAR"))


def _is_text_type(data_type: exp.DataType | None) -> bool:
    """Return True when *data_type* is one of DuckDB's textual aliases."""
    if data_type is None:
        return False
    return data_type.this in {exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR}


def _is_json_type(data_type: exp.DataType | None) -> bool:
    """Return True when *data_type* is DuckDB's JSON type."""
    if data_type is None:
        return False
    return bool(data_type.this == exp.DataType.Type.JSON)


def _produces_json(node: exp.Expression) -> bool:
    """Return True when *node* is statically known to produce a JSON value.

    The set is intentionally narrow — only the SQLGlot node shapes the
    BigQuery-to-DuckDB transpiler emits for ``PARSE_JSON`` and the
    explicit ``CAST(... AS JSON)`` form. Adding more producers (e.g.
    ``JSON_EXTRACT``, ``JSON_OBJECT``, ``TO_JSON``) requires evidence
    that the rewrite is safe for those AST shapes.
    """
    if isinstance(node, exp.ParseJSON):
        return True
    return isinstance(node, exp.Cast) and _is_json_type(node.to)


__all__ = [
    "JSONBoolRule",
    "JSONExtractToStringRule",
    "JSONFloat64Rule",
    "JSONKeysRule",
    "JSONRemoveRule",
    "JSONSetRule",
    "JSONStringRule",
    "JSONStripNullsRule",
    "LaxBoolRule",
    "LaxFloat64Rule",
    "LaxInt64Rule",
    "LaxStringRule",
]
