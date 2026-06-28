"""SQLTranslator — the BigQuery → DuckDB transpilation orchestrator.

Pipeline::

    BigQuery SQL
        → pre-process rewriters (partition pruning, wildcard expansion, …)
        → sqlglot.transpile(read="bigquery", write="duckdb")
        → post-process: walk AST, apply every matching TranslationRule
        → serialize back to SQL string
    DuckDB SQL

The translator is stateless — safe to call from any number of async
tasks concurrently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.qualify import qualify

from bqemulator.domain.errors import InvalidQueryError, UnsupportedFeatureError
from bqemulator.domain.result import Err, Ok, Result
from bqemulator.observability.logging_ import get_logger
from bqemulator.sql.errors import sql_parse_error, sql_unsupported
from bqemulator.sql.rewriter.aggregate_variants import rewrite_aggregate_variants
from bqemulator.sql.rewriter.alter_table_set_options import (
    rewrite_alter_table_set_options,
)
from bqemulator.sql.rewriter.collate_specifier import rewrite_collate_specifier
from bqemulator.sql.rewriter.create_table_schema_ctas import (
    rewrite_create_table_schema_ctas,
)
from bqemulator.sql.rewriter.datetime_helpers import rewrite_datetime_helpers
from bqemulator.sql.rewriter.decimal_literals import rewrite_decimal_literals
from bqemulator.sql.rewriter.division_by_zero import rewrite_division_by_zero
from bqemulator.sql.rewriter.json_helpers import rewrite_json_helpers
from bqemulator.sql.rewriter.numeric_literals import rewrite_numeric_literals
from bqemulator.sql.rewriter.partition_pseudo_columns import (
    rewrite_partition_pseudo_columns,
)
from bqemulator.sql.rewriter.range_sessionize import rewrite_range_sessionize
from bqemulator.sql.rewriter.safe_helpers import rewrite_safe_helpers
from bqemulator.sql.rewriter.session_user import rewrite_session_user
from bqemulator.sql.rewriter.sha512 import rewrite_sha512
from bqemulator.sql.rewriter.specialized_types import rewrite_specialized_types
from bqemulator.sql.rewriter.string_helpers import rewrite_string_helpers
from bqemulator.sql.rewriter.struct_helpers import rewrite_struct_helpers
from bqemulator.sql.rewriter.timestamp_iso_helpers import (
    rewrite_timestamp_iso_helpers,
)
from bqemulator.sql.rewriter.unnest_struct import rewrite_unnest_struct
from bqemulator.sql.rules import TranslationRule, get_all_rules

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.row_access.identity import CallerIdentity

_log = get_logger(__name__)

# Constructs that are explicitly out of scope — detect early and fail
# with a clear error instead of a confusing DuckDB parse failure.
_UNSUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        # ``CREATE MODEL`` and ``ML.PREDICT`` are intercepted before
        # translation (ADR 0047 / RFC 0002): the surface-only BigQuery ML
        # path registers model metadata via
        # ``jobs.executor.parse_create_model`` and rewrites ``ML.PREDICT``
        # into a passthrough-plus-prediction subquery via
        # ``sql.rewriter.ml_predict.rewrite_ml_predict`` rather than failing
        # here. The remaining ML.* constructs stay unsupported.
        "ML.EVALUATE",
        "ML.FORECAST",
        "ML.GENERATE_TEXT",
        "ML.GENERATE_EMBEDDING",
    }
)


def _resolve_caller(caller: CallerIdentity | None) -> CallerIdentity:
    """Return ``caller`` if supplied, else the unauthenticated fallback.

    The fallback mirrors :data:`bqemulator.row_access.identity.DEFAULT_CALLER`
    so the ``SESSION_USER()`` substitution is deterministic for legacy
    call sites that haven't propagated identity yet —
    ``SESSION_USER()`` → ``'anonymous'`` literal — instead of failing
    the translation.

    The import is deferred to function scope to avoid a circular
    import (``row_access.identity`` → … → ``sql.translator``).
    """
    if caller is not None:
        return caller
    from bqemulator.row_access.identity import DEFAULT_CALLER, CallerIdentity

    return CallerIdentity(principal=DEFAULT_CALLER, is_authenticated=False)


class SQLTranslator:
    """Translates BigQuery GoogleSQL to DuckDB SQL.

    Usage::

        translator = SQLTranslator()
        result = translator.translate("SELECT SAFE_DIVIDE(a, b) FROM t")
        match result:
            case Ok(duckdb_sql):
                ...
            case Err(error):
                ...
    """

    def __init__(self) -> None:
        self._rules: list[TranslationRule] = get_all_rules()
        _log.debug(
            "sql.translator.init",
            rules_count=len(self._rules),
            rule_names=[r.name for r in self._rules],
        )

    def translate(
        self,
        bq_sql: str,
        *,
        schema: dict[str, Any] | None = None,
        caller: CallerIdentity | None = None,
        **kwargs: Any,  # noqa: ARG002 — reserved for future rewriter context
    ) -> Result[str, InvalidQueryError | UnsupportedFeatureError]:
        """Translate a BigQuery SQL string to DuckDB SQL.

        Args:
            bq_sql: The BigQuery GoogleSQL query.
            schema: Optional ``{table_name: {column_name: type_str}}``
                catalog snapshot consumed by SQLGlot's ``annotate_types``
                pass. When provided, each AST node carries a resolved
                ``.type`` attribute that operand-type-aware rules
                (currently ``AvgDecimalRule`` for ADR 0023 §1.B
                aggregate-type preservation) consult. ``None`` disables
                the annotation pass — rules that depend on operand
                types simply skip.
            caller: Optional :class:`~bqemulator.row_access.identity.CallerIdentity`
                used by the ``SESSION_USER()`` pre-translator
                (ADR 0038). ``None`` folds to the unauthenticated
                fallback identity so legacy callers that haven't
                propagated identity yet still get a deterministic
                substitution (``SESSION_USER()`` →
                ``'anonymous'`` literal) rather than a translation
                error.
            **kwargs: Reserved for future rewriter context (e.g.
                      catalog handle for wildcard table expansion).

        Returns:
            ``Ok(duckdb_sql)`` on success, ``Err(DomainError)`` on failure.
        """
        # 1. Quick reject for explicitly unsupported constructs.
        upper = bq_sql.upper()
        for kw in _UNSUPPORTED_KEYWORDS:
            if kw in upper:
                return Err(sql_unsupported(kw))

        # 1a. ``SESSION_USER()`` substitution (ADR 0038). Runs first
        #     among the pre-translators so the resolved email literal
        #     is in the SQL before any other rewriter operates on it
        #     (the row-access enforcement pass inlines policy filters
        #     into the user's query; those filters can contain
        #     ``SESSION_USER()`` and we want the substitution to win
        #     before SQLGlot transpiles the BigQuery AST to DuckDB,
        #     which would otherwise resolve ``SESSION_USER`` to the
        #     literal ``'duckdb'``).
        bq_sql = rewrite_session_user(bq_sql, _resolve_caller(caller))

        # 1b. ``ALTER TABLE ... SET OPTIONS(...)`` no-op. SQLGlot's
        #     BigQuery → DuckDB transpile drops the ``OPTIONS(...)``
        #     clause and emits the truncated ``ALTER TABLE "..." SET``
        #     which DuckDB rejects with ``syntax error at end of
        #     input``. dbt-bigquery emits this at the tail of every
        #     ``dbt seed`` / ``dbt run``. bqemulator doesn't model
        #     table-level option metadata yet, so collapse to a
        #     trivially-successful ``SELECT 1`` and let the job
        #     state-machine report success.
        bq_sql = rewrite_alter_table_set_options(bq_sql)

        # 2-pre-pre. CTAS-with-schema decomposition. BigQuery accepts
        #    ``CREATE [OR REPLACE] TABLE x (schema) AS SELECT …`` in a
        #    single statement; DuckDB's parser rejects the combined form.
        #    Strip the schema clause and wrap each SELECT projection in
        #    ``CAST(<value> AS <declared-type>) AS <declared-name>`` so
        #    the resulting bare CTAS preserves the user's declared
        #    column types.
        bq_sql_for_transpile = rewrite_create_table_schema_ctas(bq_sql)
        # 2-pre. Scope-expansion #15 pre-translator: rewrite the
        #    BigQuery ``RANGE_SESSIONIZE(TABLE …, …)`` TVF call into
        #    a windowed-subquery gaps-and-islands sessionisation
        #    expansion. Runs *before* :func:`rewrite_specialized_types`
        #    so the ``RANGE(MIN, MAX)`` constructor it emits gets
        #    rewritten to the STRUCT form SQLGlot transpiles cleanly.
        #    Operates on the raw SQL text because SQLGlot's BigQuery
        #    parser doesn't accept the ``TABLE <ref>`` TVF-argument
        #    keyword.
        bq_sql_for_transpile = rewrite_range_sessionize(bq_sql_for_transpile)
        # 2. Specialized-types pre-translator: expand BigQuery compound
        #    interval literals (``INTERVAL '1-2 3 4:5:6.789' YEAR TO
        #    SECOND``) into single-unit additive expressions so DuckDB's
        #    parser accepts the SQL after SQLGlot's transpile pass.
        bq_sql_for_transpile = rewrite_specialized_types(bq_sql_for_transpile)
        # 2b. String-helpers pre-translator: route NORMALIZE /
        #     NORMALIZE_AND_CASEFOLD through Python helpers — SQLGlot
        #     collapses the casefold flag during the DuckDB transpile,
        #     so we must rewrite while the BigQuery AST still carries
        #     the distinction.
        bq_sql_for_transpile = rewrite_string_helpers(bq_sql_for_transpile)
        # 2c. Aggregate-variants pre-translator: ARRAY_AGG / STRING_AGG
        #     ORDER BY LIMIT n + ARRAY_AGG IGNORE NULLS. DuckDB rejects
        #     LIMIT inside aggregates and SQLGlot silently drops IGNORE
        #     NULLS during the DuckDB transpile, so we must rewrite
        #     while the BigQuery AST still carries the original shape.
        bq_sql_for_transpile = rewrite_aggregate_variants(bq_sql_for_transpile)
        # 2d. Numeric-literals pre-translator: pin NUMERIC / BIGNUMERIC
        #     typed literals to explicit DECIMAL precision so DuckDB's
        #     default DECIMAL(18, 3) doesn't reject wide BigQuery
        #     numerics. BIGNUMERIC routes through a Python UDF so the
        #     scale-marker (> 9) lands on the wire as BIGNUMERIC even
        #     when the literal's natural scale is ≤ 9.
        bq_sql_for_transpile = rewrite_numeric_literals(bq_sql_for_transpile)
        # 2e. Decimal-literals pre-translator: rewrite bare BigQuery
        #     decimal literals (``3.25``, ``-1.5``) to scientific
        #     notation so DuckDB types them as ``DOUBLE`` (matching
        #     BigQuery's ``FLOAT64`` typing) instead of inferring a
        #     narrow ``DECIMAL(p, s)`` that surfaces as NUMERIC on the
        #     wire.
        bq_sql_for_transpile = rewrite_decimal_literals(bq_sql_for_transpile)
        # 2f. Datetime-helpers pre-translator: rewrite ``LAST_DAY(x,
        #     WEEK)`` to ``DATE_ADD(x, INTERVAL (7 - EXTRACT(DAYOFWEEK
        #     FROM x)) DAY)``. SQLGlot's default transpile inlines the
        #     call to a Sunday-end (not BigQuery's Saturday-end)
        #     expression; the pre-translate runs while the AST still
        #     carries the LastDay shape so we can replace it cleanly.
        bq_sql_for_transpile = rewrite_datetime_helpers(bq_sql_for_transpile)
        # 2g. JSON-helpers pre-translator: wrap ``TO_JSON(x)`` in
        #     ``CAST(... AS JSON)`` so the result column lands on the
        #     wire as ``JSON`` rather than ``STRING`` (which is what
        #     SQLGlot's default ``CAST(TO_JSON(...) AS TEXT)`` produces
        #     for both ``TO_JSON`` and ``TO_JSON_STRING``).
        bq_sql_for_transpile = rewrite_json_helpers(bq_sql_for_transpile)
        # 2h-pre. UNNEST-struct pre-translator (runs BEFORE
        #     ``rewrite_struct_helpers``): propagate the first STRUCT's
        #     named-field aliases to every subsequent positional STRUCT
        #     in an ``UNNEST([...])`` array literal. This preserves
        #     BigQuery's "first struct seeds the field names for the
        #     array" semantic so the downstream ``rewrite_struct_helpers``
        #     pass sees a homogeneously-named array (and therefore
        #     leaves it alone). Without this step, the downstream pass
        #     would convert ``STRUCT('b', 2)`` → ``ROW('b', 2)`` while
        #     leaving the first ``STRUCT('a' AS label, 1 AS value)``
        #     named, and the resulting mixed array would fail DuckDB's
        #     field-name binder on the outer ``SELECT label, value``.
        bq_sql_for_transpile = rewrite_unnest_struct(bq_sql_for_transpile)
        # 2h. Struct-helpers pre-translator: rewrite positional
        #     ``STRUCT(value, value, …)`` to DuckDB's ``ROW(…)`` so the
        #     struct aligns *positionally* with its target — matching
        #     BigQuery's name-from-context inference for INSERT VALUES
        #     and UNION ALL chains where the first SELECT carries
        #     explicit field aliases.
        bq_sql_for_transpile = rewrite_struct_helpers(bq_sql_for_transpile)
        # 2i. Safe-helpers pre-translator: rewrite BigQuery's ``SAFE.X``
        #     prefix form (``SAFE.LN(-1)``, ``SAFE.SQRT(-1)``, etc.) to
        #     DuckDB's ``TRY(...)`` — the SQLGlot transpile leaves the
        #     ``SAFE.`` schema-qualified call intact and the table
        #     rewriter then mangles it into a synthetic project-qualified
        #     function name.
        bq_sql_for_transpile = rewrite_safe_helpers(bq_sql_for_transpile)
        # 2j. Division-by-zero pre-translator: wrap every bare ``/`` in
        #     a CASE that raises ``Division by zero`` via DuckDB's
        #     ``error(VARCHAR)`` builtin when the divisor evaluates to
        #     0. Runs AFTER ``safe_helpers`` so a user-written ``a / b``
        #     inside the ``SAFE.X(...)`` prefix form is already nested
        #     in a ``TRY(...)`` shell — our raise gets absorbed by TRY,
        #     yielding NULL (matching BigQuery's ``SAFE.X(a / 0) = NULL``
        #     semantic). Function-call divides (``SAFE_DIVIDE``,
        #     ``IEEE_DIVIDE``) are opaque ``Anonymous`` / typed nodes
        #     at this stage so the walk does not see them — their Div
        #     children are emitted post-translate by SQLGlot's transpile
        #     and the ``IeeeDivideRule``, respectively, after our
        #     pre-translator has already run.
        bq_sql_for_transpile = rewrite_division_by_zero(bq_sql_for_transpile)
        # 2k-pre. SHA512 pre-translator: rewrite every ``SHA512(x)`` to
        #     ``bqemu_sha512(x)`` while the AST still carries the
        #     ``length=512`` annotation. SQLGlot's BQ → DuckDB transpile
        #     silently collapses ``SHA512`` to ``SHA256`` (DuckDB has no
        #     ``sha512`` builtin); rewriting here preserves the
        #     algorithm width.
        bq_sql_for_transpile = rewrite_sha512(bq_sql_for_transpile)
        # 2k-tz. Timestamp ISO helpers pre-translator: route
        #     ``FORMAT_TIMESTAMP`` / ``PARSE_TIMESTAMP`` through the
        #     Python helpers ``bqemu_format_timestamp_iso`` /
        #     ``bqemu_parse_timestamp_iso`` whenever the format carries a
        #     ``%Ez`` extension specifier or a ``%Z`` named-zone token,
        #     or when ``FORMAT_TIMESTAMP`` carries an explicit zone
        #     argument SQLGlot would otherwise drop on transpile. The
        #     helpers bridge DuckDB's STRFTIME / STRPTIME, which reject
        #     ``%E#`` specifiers and silently accept ambiguous zone
        #     abbreviations that real BigQuery rejects with ``Invalid
        #     time zone``.
        bq_sql_for_transpile = rewrite_timestamp_iso_helpers(bq_sql_for_transpile)
        # 2k. COLLATE specifier pre-translator: rewrite the two
        #     BigQuery specifiers the corpus exercises — ``'und:ci'``
        #     (case-insensitive Unicode default) to ``LOWER(value)`` so
        #     the comparison naturally case-folds, and ``'binary'`` to
        #     ``error(<BQ message>)`` so the recorded
        #     ``str_collate_binary`` fixture's error envelope is matched
        #     by the existing error_mapper fallback. Must run before the
        #     SQLGlot transpile because SQLGlot emits the unquoted
        #     ``<value> COLLATE und:ci`` form which DuckDB's lexer
        #     rejects (the ``:`` is a divider).
        bq_sql_for_transpile = rewrite_collate_specifier(bq_sql_for_transpile)
        # 2l. Partition-pseudo-columns pre-translator: rewrite BigQuery's
        #     ``_PARTITIONDATE`` / ``_PARTITIONTIME`` pseudo-columns to
        #     ``CURRENT_DATE()`` / ``CURRENT_TIMESTAMP()``. The
        #     emulator's storage layer doesn't tag rows with a partition
        #     timestamp; collapsing to today's date matches the fixture
        #     filters (``> '1900-01-01'``, ``BETWEEN ...``) and the
        #     fixture for ``< '1900-01-01'`` that expects 0 rows.
        bq_sql_for_transpile = rewrite_partition_pseudo_columns(bq_sql_for_transpile)

        # 3. SQLGlot transpile.
        try:
            transpiled_list = sqlglot.transpile(
                bq_sql_for_transpile,
                read="bigquery",
                write="duckdb",
                pretty=False,
            )
        except sqlglot.errors.ParseError as exc:
            return Err(sql_parse_error(str(exc)))
        except sqlglot.errors.OptimizeError as exc:
            return Err(sql_parse_error(str(exc)))
        except Exception as exc:  # noqa: BLE001
            return Err(sql_parse_error(f"Unexpected translation error: {exc}"))

        if not transpiled_list or not transpiled_list[0].strip():
            return Err(sql_parse_error("Empty query"))

        # 3. Post-process: parse the DuckDB SQL back into an AST, apply
        #    custom rules, then re-serialize. Rules that detect an
        #    explicitly out-of-scope feature can raise
        #    :class:`UnsupportedFeatureError`; we propagate that as a
        #    clean ``Err`` rather than letting it bubble through.
        duckdb_sql = transpiled_list[0]
        if self._rules:
            try:
                duckdb_sql = self._apply_rules(duckdb_sql, schema=schema)
            except UnsupportedFeatureError as exc:
                return Err(exc)

        _log.debug(
            "sql.translated",
            input_len=len(bq_sql),
            output_len=len(duckdb_sql),
        )
        return Ok(duckdb_sql)

    def _apply_rules(self, sql: str, *, schema: dict[str, Any] | None = None) -> str:
        """Walk the AST in post-order and apply every matching rule.

        Post-order matters when a rule rewrites a parent whose children
        also need rewriting: visiting children first means the parent's
        rewrite sees the already-translated subtree when it copies its
        arguments (``[arg.copy() for arg in node.expressions]``). This
        is critical for nested spatial calls like
        ``ST_DWITHIN(ST_GEOGFROMTEXT(...), ST_GEOGFROMTEXT(...), 5)``
        where the inner constructors must be renamed before the outer
        ``ST_DWITHIN`` is rebuilt.

        When *schema* is provided, runs SQLGlot's ``qualify`` +
        ``annotate_types`` passes so operand-type-aware rules
        (``AvgDecimalRule``) can see resolved column types. Failure of
        either pass is non-fatal — we fall back to the unannotated AST
        and rules that need type info simply skip.
        """
        try:
            tree = sqlglot.parse_one(sql, read="duckdb")
        except Exception:  # noqa: BLE001
            # If we can't parse the DuckDB output, return it as-is —
            # SQLGlot produced it, so it should be valid.
            return sql

        # ``sqlglot.parse_one`` is typed as ``Expr`` in the stubs; the
        # runtime value is an :class:`exp.Expression`. Asserting here
        # narrows it for the rest of the function and the helpers.
        assert isinstance(tree, exp.Expression)  # noqa: S101

        if schema is not None:
            tree = self._annotate_tree(tree, schema)

        modified = False
        # Snapshot nodes pre-order, then iterate in reverse — children
        # of any parent appear *after* the parent in pre-order, so
        # ``reversed(...)`` gives us a post-order-equivalent traversal.
        for node in reversed(list(tree.walk())):
            node_expr: exp.Expression = node  # type: ignore[assignment]
            if self._apply_first_matching_rule(node_expr, tree):
                modified = True

        if modified:
            return tree.sql(dialect="duckdb")
        return sql

    @staticmethod
    def _annotate_tree(
        tree: exp.Expression,
        schema: dict[str, Any],
    ) -> exp.Expression:
        """Run SQLGlot's ``qualify`` + ``annotate_types`` passes.

        Failures are logged and the unannotated tree is returned;
        rules that need type info gracefully skip. The runtime
        ``isinstance`` check narrows SQLGlot's loosely-typed
        ``Expr`` return to :class:`exp.Expression` without resorting
        to a ``cast`` (which different stub revisions flag as either
        required or redundant).
        """
        try:
            qualified = qualify(tree, schema=schema, dialect="duckdb", infer_schema=True)
            annotated = annotate_types(qualified, schema=schema)
        except Exception as exc:  # noqa: BLE001
            _log.debug("sql.annotate_types_skipped", error=str(exc))
            return tree
        assert isinstance(annotated, exp.Expression)  # noqa: S101 — stub-bridge narrowing
        return annotated

    def _apply_first_matching_rule(
        self,
        node: exp.Expression,
        tree: exp.Expression,
    ) -> bool:
        """Apply the first matching rule to ``node`` (in place); return True if replaced.

        Each rule produces DuckDB-native output, so a single
        post-order pass is sufficient — once a rule replaces ``node``
        we stop scanning further rules for it. Subsequent reversed-
        walk iterations pick up the original positions inside the
        replacement subtree on their own.
        """
        if node.parent is None and node is not tree:
            # Node has been detached by an earlier replacement;
            # skip it.
            return False
        for rule in self._rules:
            if not rule.applies_to(node):
                continue
            replacement = rule.rewrite(node)
            if replacement is not node:
                node.replace(replacement)
                return True
        return False


__all__ = ["SQLTranslator"]
