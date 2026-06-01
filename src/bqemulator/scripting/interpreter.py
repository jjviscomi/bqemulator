"""Tree-walking interpreter for BigQuery scripts.

The interpreter:

1. Parses the script into an AST via :mod:`bqemulator.scripting.parser`.
2. Walks the AST, dispatching each statement.
3. Evaluates expressions by wrapping them in ``SELECT <expr>`` and
   running them through the shared SQL translation pipeline, with
   script variables bound as positional parameters so no user string
   ever reaches DuckDB unescaped.
4. Uses :class:`ScriptRaise` to propagate domain errors through
   ``EXCEPTION WHEN`` handlers.

See [ADR 0011](../../docs/adr/0011-tree-walking-scripting-interpreter.md)
and [ADR 0015](../../docs/adr/0015-scripting-execution-model.md).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any, ClassVar
import uuid

import pyarrow as pa
import sqlglot
from sqlglot import exp

from bqemulator.catalog.ddl_sync import (
    sync_created_table,
    sync_created_view,
    sync_dropped_object,
)
from bqemulator.domain.errors import (
    DomainError,
    InvalidQueryError,
    QuotaExceededError,
    ResourceRef,
    resource_not_found,
)
from bqemulator.domain.result import Err, Ok
from bqemulator.observability.logging_ import get_logger
from bqemulator.row_access.identity import CallerIdentity
from bqemulator.scripting.ast import (
    BeginStmt,
    BreakStmt,
    CallStmt,
    ContinueStmt,
    CreateFunctionStmt,
    CreateProcedureStmt,
    DeclareStmt,
    ExecuteImmediateStmt,
    ForStmt,
    IfStmt,
    LoopStmt,
    RaiseStmt,
    ReturnStmt,
    SetStmt,
    SqlStmt,
    Statement,
    WhileStmt,
)
from bqemulator.scripting.exceptions import (
    BreakSignal,
    ContinueSignal,
    ReturnSignal,
    ScriptRaise,
)
from bqemulator.scripting.frames import FrameStack
from bqemulator.scripting.parser import parse_script
from bqemulator.sql.parameters import bind_parameters
from bqemulator.sql.rewriter.information_schema import (
    expand_information_schema,
)
from bqemulator.sql.rewriter.row_access_filter import rewrite_for_row_access
from bqemulator.sql.rewriter.unnest_offset import rewrite_unnest_offset
from bqemulator.sql.rewriter.wildcard_expander import expand_wildcard_tables
from bqemulator.sql.table_rewriter import rewrite_table_refs
from bqemulator.sql.translator import SQLTranslator
from bqemulator.udf.temp_registry import TempRoutineRegistry
from bqemulator.versioning.ddl import (
    VersioningDDL,
    VersioningDDLKind,
    VersioningDDLRouter,
    execute_versioning_ddl,
    is_versioning_ddl,
)

if TYPE_CHECKING:
    from bqemulator.api.dependencies import AppContext
    from bqemulator.catalog.models import RoutineMeta

_log = get_logger(__name__)

# Part counts for dotted routine references.
_REF_FULLY_QUALIFIED = 3
_REF_DATASET_QUALIFIED = 2


@dataclass(slots=True)
class ScriptResult:
    """Result of running a script.

    Attributes:
        final_table: The arrow table produced by the last executed
            SELECT in the script (or None if the last statement did not
            return a result set).
        statements_executed: Count of executed statements, used for the
            job's ``statistics.scriptStatistics.statementCount``.
    """

    final_table: pa.Table | None
    statements_executed: int


class ScriptInterpreter:
    """Executes a parsed script against an :class:`AppContext`."""

    def __init__(
        self,
        ctx: AppContext,
        project_id: str,
        *,
        caller: CallerIdentity | None = None,
    ) -> None:
        self._ctx = ctx
        self._project_id = project_id
        self._frames = FrameStack()
        self._frames.push(kind="root")
        self._translator = SQLTranslator()
        self._final_table: pa.Table | None = None
        self._statements_executed = 0
        self._max_statements = ctx.settings.scripting_max_statements
        self._max_loop_iters = ctx.settings.scripting_max_loop_iterations
        self._caller = caller or CallerIdentity(
            principal="user:anonymous@bqemulator.local",
            is_authenticated=False,
        )
        # ADR 0023 §1.D — local-scope TEMP-function registry. Only
        # created when the context exposes a udf_registry (the production
        # path always does; some narrow unit fixtures omit it).
        udf_registry = getattr(ctx, "udf_registry", None)
        self._temp_routines: TempRoutineRegistry | None = (
            TempRoutineRegistry(engine=ctx.engine, udf_registry=udf_registry)
            if udf_registry is not None
            else None
        )
        # BigQuery transactions ([`BEGIN TRANSACTION`](https://cloud.google.com/bigquery/docs/reference/standard-sql/transactions))
        # are implemented at the emulator level rather than through
        # DuckDB's native transactions because DuckDB's transaction
        # model is incompatible with BigQuery's semantic of preserving
        # DML through a caught ``EXCEPTION WHEN ERROR`` block. We
        # snapshot every table the first time DML targets it inside an
        # open transaction, then on ROLLBACK we restore from the
        # snapshot; on COMMIT or exception-caught we drop the snapshot
        # and the DML stays applied. ``None`` means "no transaction
        # open"; an empty dict means "open but nothing modified yet".
        self._active_txn: dict[str, str] | None = None

    async def run(self, source: str) -> ScriptResult:
        """Parse ``source`` and execute the resulting script."""
        script = parse_script(source)
        try:
            for stmt in script.statements:
                await self._exec_statement(stmt)
            # BigQuery semantic: an open transaction at end-of-script
            # with no unhandled error is implicitly committed. Drop the
            # snapshot backups; the applied DML stays in the real
            # tables. If an exception escapes the script, the
            # ``except`` arm below restores from snapshots instead.
            if self._active_txn is not None:
                await self._drop_txn_snapshots()
        except BaseException:
            # An unhandled error rolled out of the script — BigQuery's
            # implicit semantic is to roll back any open transaction.
            # Restore from snapshots so the script's effects do not
            # leak to subsequent jobs.
            if self._active_txn is not None:
                with contextlib.suppress(Exception):
                    await self._restore_txn_snapshots()
            raise
        finally:
            if self._temp_routines is not None:
                self._temp_routines.cleanup()
        return ScriptResult(
            final_table=self._final_table,
            statements_executed=self._statements_executed,
        )

    # -- Dispatch --------------------------------------------------------

    #: ``(stmt_subclass, method_name)`` pairs consulted by
    #: :meth:`_exec_statement`. Storing method *names* instead of bound
    #: functions sidesteps two pitfalls: (1) referencing unbound
    #: ``ScriptInterpreter._exec_*`` in the class body would trip the
    #: ``SLF001`` private-access lint at every call site, and (2) the
    #: per-handler argument-type variance (``DeclareStmt`` etc.) can't
    #: be expressed as a uniform ``Callable`` slot without per-entry
    #: casts. The dispatch is order-stable: the first ``isinstance``
    #: match wins. ``BreakStmt`` / ``ContinueStmt`` / ``SqlStmt`` route
    #: through tiny adapter methods below so every entry shares the
    #: ``(self, stmt) -> None`` shape.
    _STATEMENT_DISPATCH: ClassVar[tuple[tuple[type[Statement], str], ...]] = (
        (DeclareStmt, "_exec_declare"),
        (SetStmt, "_exec_set"),
        (IfStmt, "_exec_if"),
        (WhileStmt, "_exec_while"),
        (LoopStmt, "_exec_loop"),
        (ForStmt, "_exec_for"),
        (BreakStmt, "_exec_break_stmt"),
        (ContinueStmt, "_exec_continue_stmt"),
        (ReturnStmt, "_exec_return"),
        (BeginStmt, "_exec_begin"),
        (CallStmt, "_exec_call"),
        (ExecuteImmediateStmt, "_exec_execute_immediate"),
        (RaiseStmt, "_exec_raise"),
        (CreateFunctionStmt, "_exec_create_function"),
        (CreateProcedureStmt, "_exec_create_procedure"),
        (SqlStmt, "_exec_sql_stmt"),
    )

    async def _exec_statement(self, stmt: Statement) -> None:
        self._statements_executed += 1
        if self._statements_executed > self._max_statements:
            raise QuotaExceededError(
                f"Script exceeded maximum statement count ({self._max_statements})",
            )
        for stmt_type, method_name in self._STATEMENT_DISPATCH:
            if isinstance(stmt, stmt_type):
                await getattr(self, method_name)(stmt)
                return
        raise InvalidQueryError(f"Unknown statement type: {type(stmt).__name__}")

    async def _exec_break_stmt(self, _stmt: BreakStmt) -> None:
        """Dispatch adapter: ``BreakStmt`` raises the ``BreakSignal`` sentinel."""
        raise BreakSignal

    async def _exec_continue_stmt(self, _stmt: ContinueStmt) -> None:
        """Dispatch adapter: ``ContinueStmt`` raises the ``ContinueSignal`` sentinel."""
        raise ContinueSignal

    async def _exec_sql_stmt(self, stmt: SqlStmt) -> None:
        """Dispatch adapter: route ``SqlStmt`` to :meth:`_exec_sql` with its raw SQL."""
        await self._exec_sql(stmt.sql)

    # -- Individual constructs -------------------------------------------

    async def _exec_declare(self, stmt: DeclareStmt) -> None:
        value: Any = None
        if stmt.default_expr is not None:
            value = await self._eval_expr_scalar(stmt.default_expr)
        for name in stmt.names:
            self._frames.declare(name, stmt.type_name, value)

    async def _exec_set(self, stmt: SetStmt) -> None:
        if len(stmt.targets) == 1:
            value = await self._eval_expr_scalar(stmt.source_expr)
            self._frames.set(stmt.targets[0], value)
            return
        # Multi-target: source must be (SELECT ...).
        row = await self._eval_expr_row(stmt.source_expr)
        if len(row) != len(stmt.targets):
            raise InvalidQueryError(
                f"SET (targets) column count {len(stmt.targets)} != row columns {len(row)}",
            )
        for name, val in zip(stmt.targets, row, strict=True):
            self._frames.set(name, val)

    async def _exec_if(self, stmt: IfStmt) -> None:
        for cond_expr, body in stmt.branches:
            cond_val = await self._eval_expr_scalar(cond_expr)
            if _is_truthy(cond_val):
                await self._run_block(body, frame_kind="block")
                return
        if stmt.else_body is not None:
            await self._run_block(stmt.else_body, frame_kind="block")

    async def _exec_while(self, stmt: WhileStmt) -> None:
        iterations = 0
        while True:
            cond_val = await self._eval_expr_scalar(stmt.condition_expr)
            if not _is_truthy(cond_val):
                return
            iterations += 1
            if iterations > self._max_loop_iters:
                raise QuotaExceededError(
                    f"WHILE exceeded max iterations ({self._max_loop_iters})",
                )
            try:
                await self._run_block(stmt.body, frame_kind="loop")
            except BreakSignal:
                return
            except ContinueSignal:
                continue

    async def _exec_loop(self, stmt: LoopStmt) -> None:
        iterations = 0
        while True:
            iterations += 1
            if iterations > self._max_loop_iters:
                raise QuotaExceededError(
                    f"LOOP exceeded max iterations ({self._max_loop_iters})",
                )
            try:
                await self._run_block(stmt.body, frame_kind="loop")
            except BreakSignal:
                return
            except ContinueSignal:
                continue

    async def _exec_for(self, stmt: ForStmt) -> None:
        # FOR name IN (SELECT ...) — execute the SELECT once and iterate.
        source_sql = stmt.source_sql.strip()
        if source_sql.startswith("(") and source_sql.endswith(")"):
            source_sql = source_sql[1:-1]
        arrow_table = await self._run_query(source_sql)
        rows = arrow_table.to_pylist()
        iterations = 0
        for row in rows:
            iterations += 1
            if iterations > self._max_loop_iters:
                raise QuotaExceededError(
                    f"FOR exceeded max iterations ({self._max_loop_iters})",
                )
            self._frames.push(kind="loop")
            self._frames.declare(stmt.loop_var, "STRUCT", row)
            try:
                for s in stmt.body:
                    await self._exec_statement(s)
            except BreakSignal:
                self._frames.pop()
                return
            except ContinueSignal:
                self._frames.pop()
                continue
            self._frames.pop()

    async def _exec_return(self, stmt: ReturnStmt) -> None:
        value: Any = None
        if stmt.value_expr is not None:
            value = await self._eval_expr_scalar(stmt.value_expr)
        raise ReturnSignal(value)

    async def _exec_begin(self, stmt: BeginStmt) -> None:
        try:
            await self._run_block(stmt.body, frame_kind="block")
        except ScriptRaise as raised:
            if stmt.exception_handler is None:
                raise
            # BigQuery semantic: ``EXCEPTION WHEN ERROR THEN`` catches
            # the error but does NOT roll back DML applied inside the
            # outer ``BEGIN TRANSACTION``. Drop the snapshot backups so
            # the applied DML stays in the real tables (matches the
            # ``txn_in_exception_block`` conformance fixture).
            await self._drop_txn_snapshots()
            # Enter the handler in a fresh frame.
            # Expose the raised error as implicit @@error.message (read-only).
            self._frames.push(kind="block")
            self._frames.declare(
                "__error_message__",
                "STRING",
                raised.message,
            )
            try:
                for s in stmt.exception_handler:
                    await self._exec_statement(s)
            finally:
                self._frames.pop()
        except DomainError as exc:
            # Unwrapped DomainError: if we have a handler, wrap + match;
            # otherwise re-raise.
            if stmt.exception_handler is None:
                raise
            await self._drop_txn_snapshots()
            self._frames.push(kind="block")
            self._frames.declare("__error_message__", "STRING", exc.message)
            try:
                for s in stmt.exception_handler:
                    await self._exec_statement(s)
            finally:
                self._frames.pop()

    async def _exec_call(self, stmt: CallStmt) -> None:
        # BigQuery builtin procedure: BQ.REFRESH_MATERIALIZED_VIEW('<fqdn>')
        # routes through the versioning DDL manager (no catalog routine
        # registration on BQ; the call is special-cased by the service).
        if stmt.routine_ref.upper() == "BQ.REFRESH_MATERIALIZED_VIEW":
            await self._exec_call_refresh_mv(stmt)
            return
        routine = self._resolve_call_routine(stmt.routine_ref)
        if len(stmt.arg_exprs) != len(routine.arguments):
            raise InvalidQueryError(
                f"Procedure {routine.routine_id} expects {len(routine.arguments)} "
                f"arguments, got {len(stmt.arg_exprs)}",
            )
        arg_values, writeback_names = await self._evaluate_call_arguments(
            routine.arguments,
            stmt.arg_exprs,
        )
        callee_frame = await self._invoke_procedure(routine, arg_values)
        self._apply_callee_writebacks(writeback_names, routine.arguments, callee_frame)

    def _resolve_call_routine(self, routine_ref: str) -> RoutineMeta:
        """Resolve a routine reference and assert it's a PROCEDURE.

        Raises ``resource_not_found`` when the routine doesn't exist or
        isn't a procedure (functions can't be CALLed in BigQuery scripts).
        """
        project_id, dataset_id, routine_id = self._resolve_ref(routine_ref)
        routine = self._ctx.catalog.get_routine(project_id, dataset_id, routine_id)
        if routine is None or routine.routine_type != "PROCEDURE":
            raise resource_not_found(
                ResourceRef("routine", project_id, dataset_id, routine_id),
            )
        return routine

    async def _evaluate_call_arguments(
        self,
        params: list[Any],
        arg_exprs: list[str],
    ) -> tuple[list[Any], list[str | None]]:
        """Evaluate IN/INOUT args and capture OUT/INOUT writeback slots.

        OUT parameters are not evaluated — BigQuery initialises the
        callee's local with NULL. INOUT parameters are evaluated and
        also receive a writeback. IN parameters are evaluated with no
        writeback. The returned ``writeback_names`` list mirrors the
        argument order, with ``None`` for slots that don't write back.
        """
        arg_values: list[Any] = []
        writeback_names: list[str | None] = []
        for param, expr in zip(params, arg_exprs, strict=True):
            value, writeback = await self._evaluate_one_argument(param, expr)
            arg_values.append(value)
            writeback_names.append(writeback)
        return arg_values, writeback_names

    async def _evaluate_one_argument(
        self,
        param: Any,
        expr: str,
    ) -> tuple[Any, str | None]:
        """Evaluate a single argument expression honouring its mode."""
        mode = param.mode.upper() if param.mode else "IN"
        caller_var = expr.strip()
        writeback = caller_var if caller_var.isidentifier() else None
        if mode == "OUT":
            return None, writeback
        value = await self._eval_expr_scalar(expr)
        if mode == "INOUT":
            return value, writeback
        return value, None

    def _apply_callee_writebacks(
        self,
        writeback_names: list[str | None],
        params: list[Any],
        callee_frame: dict[str, Any] | None,
    ) -> None:
        """Copy OUT/INOUT callee locals back to the caller frame.

        ``callee_frame`` is None when the procedure body raised before
        the writeback frame was published — there's nothing to
        propagate in that case.
        """
        if callee_frame is None:
            return
        for name, param in zip(writeback_names, params, strict=True):
            if name is None:
                continue
            if param.name in callee_frame:
                self._frames.set(name, callee_frame[param.name])

    async def _exec_call_refresh_mv(self, stmt: CallStmt) -> None:
        """Dispatch ``CALL BQ.REFRESH_MATERIALIZED_VIEW('<fqdn>')``.

        BigQuery exposes MV refresh as a builtin procedure call rather
        than DDL; the emulator routes the call through the same
        versioning manager as the ``REFRESH MATERIALIZED VIEW`` form.
        """
        if len(stmt.arg_exprs) != 1:
            raise InvalidQueryError(
                "BQ.REFRESH_MATERIALIZED_VIEW expects 1 argument (the fully-"
                "qualified materialized view name as a STRING)",
            )
        target = await self._eval_expr_scalar(stmt.arg_exprs[0])
        if not isinstance(target, str):
            raise InvalidQueryError(
                "BQ.REFRESH_MATERIALIZED_VIEW: argument must evaluate to a STRING",
            )
        parts = [p.strip("` ") for p in target.split(".") if p.strip("` ")]
        if len(parts) == _REF_FULLY_QUALIFIED:
            proj, ds, tbl = parts[0], parts[1], parts[2]
        elif len(parts) == _REF_DATASET_QUALIFIED:
            proj, ds, tbl = self._project_id, parts[0], parts[1]
        else:
            raise InvalidQueryError(
                f"BQ.REFRESH_MATERIALIZED_VIEW: invalid materialized view name {target!r}",
            )
        parsed = VersioningDDL(
            kind=VersioningDDLKind.REFRESH_MATERIALIZED_VIEW,
            target_project=proj,
            target_dataset=ds,
            target_table=tbl,
        )
        await execute_versioning_ddl(parsed, self._ctx)

    async def _exec_execute_immediate(self, stmt: ExecuteImmediateStmt) -> None:
        sql_value = await self._eval_expr_scalar(stmt.sql_expr)
        if not isinstance(sql_value, str):
            raise InvalidQueryError("EXECUTE IMMEDIATE requires a STRING expression")
        using_values = [await self._eval_expr_scalar(e) for e in stmt.using_exprs]

        # Substitute positional ? placeholders with bound USING values.
        if stmt.into_names:
            arrow_table = await self._run_query_with_params(sql_value, using_values)
            rows = arrow_table.to_pylist()
            if not rows:
                raise InvalidQueryError("EXECUTE IMMEDIATE INTO returned no rows")
            if len(rows) > 1:
                raise InvalidQueryError("EXECUTE IMMEDIATE INTO returned multiple rows")
            first = rows[0]
            values = list(first.values())
            if len(values) != len(stmt.into_names):
                raise InvalidQueryError(
                    f"EXECUTE IMMEDIATE INTO target count {len(stmt.into_names)} "
                    f"!= result columns {len(values)}",
                )
            for name, v in zip(stmt.into_names, values, strict=True):
                self._frames.set(name, v)
        else:
            await self._run_statement_with_params(sql_value, using_values)

    async def _exec_raise(self, stmt: RaiseStmt) -> None:
        message: str | None = None
        if stmt.message_expr is not None:
            val = await self._eval_expr_scalar(stmt.message_expr)
            message = str(val) if val is not None else None
        err = InvalidQueryError(message or "User-raised error")
        raise ScriptRaise(err, message_override=message)

    async def _exec_create_function(self, stmt: CreateFunctionStmt) -> None:
        # ADR 0023 §1.D — single-part identifier routes to the script-local
        # TEMP-function registry rather than the persistent routines catalog.
        # The TEMP function is materialised under a registry-unique synthetic
        # dataset and is dropped when the script finishes.
        parts = [p for p in stmt.name.split(".") if p]
        if len(parts) == 1 and self._temp_routines is not None:
            await self._exec_create_temp_function(stmt, parts[0])
            return

        project_id, dataset_id, routine_id = self._resolve_ref(stmt.name)
        now = self._ctx.clock.now()
        from bqemulator.catalog.etag import generate_etag
        from bqemulator.catalog.models import RoutineArgument, RoutineMeta

        args = tuple(RoutineArgument(name=a[0], data_type=a[1]) for a in stmt.arguments)
        routine = RoutineMeta(
            project_id=project_id,
            dataset_id=dataset_id,
            routine_id=routine_id,
            routine_type=stmt.routine_type,
            language=stmt.language,
            definition_body=stmt.body,
            arguments=args,
            return_type=stmt.return_type,
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag(project_id, dataset_id, routine_id, str(now)),
        )
        await _upsert_routine(self._ctx, routine, or_replace=stmt.or_replace)

    async def _exec_create_temp_function(
        self,
        stmt: CreateFunctionStmt,
        bare_name: str,
    ) -> None:
        """Register a TEMP function in the script-local registry (ADR 0023 §1.D)."""
        if self._temp_routines is None:  # pragma: no cover — guarded by caller
            raise InvalidQueryError(
                "TEMP FUNCTION requires a configured UDF registry on the AppContext",
            )
        now = self._ctx.clock.now()
        from bqemulator.catalog.etag import generate_etag
        from bqemulator.catalog.models import RoutineArgument, RoutineMeta

        synthetic_dataset = self._temp_routines.synthetic_dataset
        args = tuple(RoutineArgument(name=a[0], data_type=a[1]) for a in stmt.arguments)
        # ADR 0023 §1.D — a SQL TEMP routine body may reference other
        # TEMP routines by their bare names. The SQL UDF translator
        # cannot see the registry, so pre-rewrite bare TEMP calls to
        # their materialised qualified names here. JS UDFs have an
        # opaque body, so the rewrite would not apply — skip it.
        body = stmt.body
        if stmt.language == "SQL":
            body = self._temp_routines.rewrite_calls(body)
        routine = RoutineMeta(
            project_id=self._project_id,
            dataset_id=synthetic_dataset,
            routine_id=bare_name,
            routine_type=stmt.routine_type,
            language=stmt.language,
            definition_body=body,
            arguments=args,
            return_type=stmt.return_type,
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag(self._project_id, synthetic_dataset, bare_name, str(now)),
        )
        async with self._ctx.engine.write_lock():
            self._temp_routines.register(bare_name, routine)

    async def _exec_create_procedure(self, stmt: CreateProcedureStmt) -> None:
        project_id, dataset_id, routine_id = self._resolve_ref(stmt.name)
        now = self._ctx.clock.now()
        from bqemulator.catalog.etag import generate_etag
        from bqemulator.catalog.models import RoutineArgument, RoutineMeta

        args = tuple(RoutineArgument(name=a[0], data_type=a[1], mode=a[2]) for a in stmt.arguments)
        routine = RoutineMeta(
            project_id=project_id,
            dataset_id=dataset_id,
            routine_id=routine_id,
            routine_type="PROCEDURE",
            language="SQL",
            definition_body=stmt.body,
            arguments=args,
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag(project_id, dataset_id, routine_id, str(now)),
        )
        await _upsert_routine(self._ctx, routine, or_replace=stmt.or_replace)

    async def _exec_sql(self, sql: str) -> None:
        sql = sql.strip()
        if not sql:
            return
        # BigQuery transaction control — intercept BEGIN / COMMIT /
        # ROLLBACK [TRANSACTION] and route through the emulator-side
        # snapshot machinery rather than DuckDB's native transactions.
        # See :meth:`__init__` for the rationale and ``self._active_txn``.
        txn_op = _classify_txn_statement(sql)
        if txn_op is not None:
            await self._handle_txn_statement(txn_op)
            return
        # If we're inside a user-level transaction, snapshot each DML
        # target the first time it's modified. On ROLLBACK the snapshots
        # restore the pre-transaction state; on COMMIT or
        # exception-caught the snapshots are dropped and the DML stays
        # applied (matching BigQuery's documented EXCEPTION semantic).
        if self._active_txn is not None:
            await self._snapshot_dml_targets(sql)
        # ADR 0023 §1.F — route versioning DDL (CREATE SNAPSHOT TABLE,
        # CREATE TABLE … CLONE, CREATE MATERIALIZED VIEW, DROP/REFRESH
        # equivalents) through the matching manager. DuckDB does not
        # support this syntax, and the managers carry the catalog +
        # storage side effects BigQuery wires into each statement.
        if is_versioning_ddl(sql):
            parsed = VersioningDDLRouter(self._project_id).parse(sql)
            if parsed is not None:
                await execute_versioning_ddl(parsed, self._ctx)
                # DDL contributes no rows — leave ``_final_table``
                # untouched so the next SELECT (if any) wins.
                return
        # Other SQL (SELECT, CREATE VIEW, CREATE TABLE [AS …], DML)
        # flows through the standard pipeline.
        table = await self._run_query(sql)
        if _is_row_producing(sql):
            self._final_table = table
        # ADR 0023 §1.F — register plain ``CREATE [OR REPLACE] TABLE``
        # outputs in the catalog so a follow-on snapshot / clone / MV
        # finds the source via ``catalog.get_table``.
        sync_created_table(sql, self._project_id, self._ctx)
        # ADR 0018 (revised 2026-05-19) — register plain ``CREATE
        # [OR REPLACE] VIEW`` outputs so the row-access rewriter's
        # ``_expand_view`` branch can recurse through the view body
        # and apply caller-bound policies on the base tables it
        # references.
        sync_created_view(sql, self._project_id, self._ctx)
        # Reconcile the catalog after a successful DROP TABLE/VIEW/SCHEMA
        # so the dropped relation or dataset disappears from tables.get,
        # tables.list, and INFORMATION_SCHEMA (matching BigQuery). DROP
        # MATERIALIZED VIEW / DROP SNAPSHOT TABLE route through the
        # versioning DDL fast path above and are not handled here.
        sync_dropped_object(sql, self._project_id, self._ctx)

    async def _handle_txn_statement(self, op: str) -> None:
        """Apply a BEGIN / COMMIT / ROLLBACK [TRANSACTION] statement.

        Transactions are tracked at the emulator level rather than
        forwarded to DuckDB; see ``self._active_txn`` for the
        rationale. Nested ``BEGIN`` is silently absorbed (matches BQ's
        permissive nested-transaction handling); an unmatched
        ``COMMIT`` / ``ROLLBACK`` is a no-op.
        """
        if op == "BEGIN":
            if self._active_txn is None:
                self._active_txn = {}
            return
        if op == "COMMIT":
            await self._drop_txn_snapshots()
            return
        if op == "ROLLBACK":
            await self._restore_txn_snapshots()
            return

    async def _snapshot_dml_targets(self, sql: str) -> None:
        """Snapshot every DML target referenced by ``sql`` once per transaction."""
        if self._active_txn is None:
            return
        for quoted_target in _dml_targets(sql, self._project_id):
            if quoted_target in self._active_txn:
                continue
            backup = f"_bqemu_txn_{uuid.uuid4().hex[:16]}"
            try:
                self._ctx.engine.execute(
                    f'CREATE TABLE "{backup}" AS SELECT * FROM {quoted_target}'
                )
            except Exception as exc:  # noqa: BLE001 — best-effort snapshot
                # If the target doesn't exist yet (CREATE TABLE AS …
                # would still be in flight), there is nothing to back
                # up. Subsequent ROLLBACK simply leaves the target as
                # the script left it.
                _log.debug(
                    "scripting.txn.snapshot_failed",
                    target=quoted_target,
                    error=str(exc),
                )
                continue
            self._active_txn[quoted_target] = backup

    async def _drop_txn_snapshots(self) -> None:
        """Drop every snapshot backup and clear the active-txn state."""
        if self._active_txn is None:
            return
        for backup in self._active_txn.values():
            with contextlib.suppress(Exception):
                self._ctx.engine.execute(f'DROP TABLE IF EXISTS "{backup}"')
        self._active_txn = None

    async def _restore_txn_snapshots(self) -> None:
        """Restore each modified target from its snapshot, then drop backups."""
        if self._active_txn is None:
            return
        for target, backup in self._active_txn.items():
            with contextlib.suppress(Exception):
                self._ctx.engine.execute(f"DELETE FROM {target}")
                self._ctx.engine.execute(f'INSERT INTO {target} SELECT * FROM "{backup}"')
                self._ctx.engine.execute(f'DROP TABLE IF EXISTS "{backup}"')
        self._active_txn = None

    # -- Helpers ---------------------------------------------------------

    async def _run_block(
        self,
        stmts: tuple[Statement, ...],
        *,
        frame_kind: str,
    ) -> None:
        self._frames.push(kind=frame_kind)
        try:
            for s in stmts:
                await self._exec_statement(s)
        finally:
            self._frames.pop()

    async def _eval_expr_scalar(self, expr: str) -> Any:
        """Evaluate ``expr`` and return the first scalar value."""
        arrow_table = await self._run_query(f"SELECT {expr}")
        if arrow_table.num_rows == 0 or arrow_table.num_columns == 0:
            return None
        col = arrow_table.column(0)
        return col[0].as_py() if col[0].is_valid else None

    async def _eval_expr_row(self, expr: str) -> list[Any]:
        """Evaluate ``expr`` and return the first row as a list."""
        text = expr.strip()
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1]
        arrow_table = await self._run_query(text)
        if arrow_table.num_rows == 0:
            raise InvalidQueryError("SET (targets) = (SELECT ...) returned no rows")
        row = arrow_table.slice(0, 1).to_pylist()[0]
        return list(row.values())

    def _rewrite_temp_calls(self, bq_sql: str) -> str:
        """Rewrite bare TEMP-function calls to their qualified flat name.

        ADR 0023 §1.D: ``CREATE TEMP FUNCTION foo(...)`` followed by
        ``SELECT foo(...)`` resolves the bare ``foo`` to the registered
        TEMP function. The pre-rewrite happens before any other SQL
        pipeline stage so the translator sees a regular qualified call.
        Without a temp registry (e.g. test contexts that omit
        ``udf_registry``) the input flows through unchanged.
        """
        if self._temp_routines is None:
            return bq_sql
        return self._temp_routines.rewrite_calls(bq_sql)

    async def _run_query(self, bq_sql: str) -> pa.Table:
        """Translate + execute a BigQuery SELECT, returning an Arrow table."""
        bq_sql = self._rewrite_temp_calls(bq_sql)
        rewritten, param_values = _rewrite_vars_to_params(bq_sql, self._frames)
        rewritten = rewrite_for_row_access(
            rewritten,
            project_id=self._project_id,
            caller=self._caller,
            catalog=self._ctx.catalog,
        )
        rewritten = expand_information_schema(
            rewritten,
            self._project_id,
            self._ctx.catalog,
        )
        rewritten = rewrite_unnest_offset(rewritten)
        expanded = expand_wildcard_tables(rewritten, self._project_id, self._ctx.catalog)
        match self._translator.translate(expanded, caller=self._caller):
            case Ok(duckdb_sql):
                pass
            case Err(error):
                raise error
        duckdb_sql = rewrite_table_refs(duckdb_sql, self._project_id)
        duckdb_sql, final_params = bind_parameters(duckdb_sql, None)
        # Merge the script variable positional params with any placeholders
        # bind_parameters emitted (none in practice for script SQL).
        combined = param_values + (final_params or [])
        try:
            return self._ctx.engine.fetch_arrow(duckdb_sql, combined or None)
        except DomainError:
            raise
        except Exception as exc:
            # Route DuckDB-side runtime errors through the central
            # error mapper so JS UDF / div-by-zero / overflow / catalog
            # failures land on the wire in BigQuery's documented shape
            # (see ADR 0022 §3). Falling back to the verbose "Script SQL
            # execution failed: ..." wrapper would mask the BQ-shaped
            # translation the conformance corpus pins.
            from bqemulator.jobs.error_mapper import translate_runtime_error

            mapped = translate_runtime_error(exc, duckdb_sql=duckdb_sql)
            if isinstance(mapped, DomainError):
                raise mapped from exc
            raise InvalidQueryError(f"Script SQL execution failed: {exc}") from exc

    async def _run_statement_with_params(self, bq_sql: str, using_values: list[Any]) -> None:
        """Execute a statement that may contain ? placeholders from USING."""
        # Translate first, then merge any @var substitutions AND the using_values.
        bq_sql = self._rewrite_temp_calls(bq_sql)
        rewritten, script_params = _rewrite_vars_to_params(bq_sql, self._frames)
        rewritten = rewrite_for_row_access(
            rewritten,
            project_id=self._project_id,
            caller=self._caller,
            catalog=self._ctx.catalog,
        )
        rewritten = expand_information_schema(
            rewritten,
            self._project_id,
            self._ctx.catalog,
        )
        rewritten = rewrite_unnest_offset(rewritten)
        expanded = expand_wildcard_tables(rewritten, self._project_id, self._ctx.catalog)
        match self._translator.translate(expanded, caller=self._caller):
            case Ok(duckdb_sql):
                pass
            case Err(error):
                raise error
        duckdb_sql = rewrite_table_refs(duckdb_sql, self._project_id)
        # The using_values land on the original ?'s; our @-substitutions emit
        # additional ?'s at the front. Concatenation preserves order because
        # _rewrite_vars_to_params visits left-to-right.
        combined = script_params + using_values
        try:
            result = self._ctx.engine.execute(duckdb_sql, combined or None)
            if hasattr(result, "to_arrow_table"):
                table = result.to_arrow_table()
                if table.num_rows or table.num_columns:
                    self._final_table = table
        except DomainError:
            raise
        except Exception as exc:
            raise InvalidQueryError(f"EXECUTE IMMEDIATE failed: {exc}") from exc

    async def _run_query_with_params(
        self,
        bq_sql: str,
        using_values: list[Any],
    ) -> pa.Table:
        """Execute a SELECT with positional USING parameters and return the result."""
        bq_sql = self._rewrite_temp_calls(bq_sql)
        rewritten, script_params = _rewrite_vars_to_params(bq_sql, self._frames)
        rewritten = rewrite_for_row_access(
            rewritten,
            project_id=self._project_id,
            caller=self._caller,
            catalog=self._ctx.catalog,
        )
        rewritten = expand_information_schema(
            rewritten,
            self._project_id,
            self._ctx.catalog,
        )
        rewritten = rewrite_unnest_offset(rewritten)
        expanded = expand_wildcard_tables(rewritten, self._project_id, self._ctx.catalog)
        match self._translator.translate(expanded, caller=self._caller):
            case Ok(duckdb_sql):
                pass
            case Err(error):
                raise error
        duckdb_sql = rewrite_table_refs(duckdb_sql, self._project_id)
        combined = script_params + using_values
        return self._ctx.engine.fetch_arrow(duckdb_sql, combined or None)

    async def _invoke_procedure(
        self, routine: RoutineMeta, args: list[Any]
    ) -> dict[str, Any] | None:
        """Execute a procedure body in a fresh frame with the given args.

        Returns a snapshot of the callee's outermost frame at the moment
        the procedure exits — the caller uses this to propagate
        OUT / INOUT parameter writes back to its own frame.
        """
        if len(args) != len(routine.arguments):
            raise InvalidQueryError(
                f"Procedure {routine.routine_id} expects {len(routine.arguments)} "
                f"arguments, got {len(args)}",
            )
        # Fresh frame — callees do not see caller locals.
        nested = ScriptInterpreter(
            self._ctx,
            routine.project_id,
            caller=self._caller,
        )
        for param, value in zip(routine.arguments, args, strict=True):
            nested._frames.declare(param.name, "ANY", value)
        with contextlib.suppress(ReturnSignal):
            await nested.run(routine.definition_body)
        # Merge the callee's final table into ours.
        if nested._final_table is not None:
            self._final_table = nested._final_table
        return nested._frames.snapshot_current()

    def _resolve_ref(self, ref: str) -> tuple[str, str, str]:
        """Split a dotted routine reference into (project, dataset, routine).

        Real BigQuery treats a single-part identifier as a TEMP-function
        reference when it resolves in the script's local scope. Per
        ADR 0023 §1.D, the resolver attempts the local-scope lookup
        first; if no match, the existing 2/3-part qualified-name check
        applies.
        """
        parts = [p for p in ref.split(".") if p]
        if len(parts) == 1 and self._temp_routines is not None:
            local = self._temp_routines.resolve(parts[0])
            if local is not None:
                return local.project_id, local.dataset_id, local.routine_id
        if len(parts) == _REF_FULLY_QUALIFIED:
            return parts[0], parts[1], parts[2]
        if len(parts) == _REF_DATASET_QUALIFIED:
            return self._project_id, parts[0], parts[1]
        raise InvalidQueryError(f"Routine reference must have 2 or 3 parts: {ref}")


def _rewrite_vars_to_params(
    bq_sql: str,
    frames: FrameStack,
) -> tuple[str, list[Any]]:
    """Rewrite bare script-variable references to named placeholders.

    BigQuery scripting exposes declared variables as plain identifiers
    inside expressions. We parse the BigQuery SQL, walk every ``Column``
    node matching a declared variable, and substitute a numbered
    placeholder (``@1``, ``@2``, …). SQLGlot rewrites those to DuckDB's
    ``$N`` syntax, which — crucially — allows the same parameter to be
    referenced multiple times from the transpiled SQL (SQLGlot often
    expands a single scalar function into multiple argument references,
    so a positional ``?`` marker would get duplicated and fail to bind).

    For STRUCT-typed variables (including the implicit ``FOR row IN
    (...)`` row variable), ``row.field`` references resolve to the
    field value directly.
    """
    visible = frames.all_visible()
    if not visible:
        return bq_sql, []

    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001
        return bq_sql, []

    params: list[Any] = []
    replaced_any = False
    for col in tree.find_all(exp.Column):
        if _rewrite_one_column(col, visible, params):
            replaced_any = True
    if not replaced_any:
        return bq_sql, []
    return tree.sql(dialect="bigquery"), params


def _rewrite_one_column(
    col: exp.Column,
    visible: dict[str, Any],
    params: list[Any],
) -> bool:
    """Replace ``col`` with a parameter placeholder when it resolves to a script var.

    Returns ``True`` when a substitution happened (and ``params`` was
    extended). ``False`` means the column referred to a real SQL
    column (not a script variable) and was left alone.
    """
    if col.table:
        return _rewrite_struct_field_column(col, visible, params)
    name = col.name
    if name not in visible:
        return False
    var = visible[name]
    placeholder = _build_scalar_placeholder(var.value, var.type_name, params)
    if _is_top_level_projection(col):
        placeholder = exp.Alias(this=placeholder, alias=exp.to_identifier(name))
    col.replace(placeholder)
    return True


def _rewrite_struct_field_column(
    col: exp.Column,
    visible: dict[str, Any],
    params: list[Any],
) -> bool:
    """Handle ``table.field`` references where ``table`` is a STRUCT variable.

    STRUCT-valued variables — including the implicit ``FOR row IN
    (...)`` row variable — resolve ``var.field`` directly to the
    inner field value. Non-STRUCT or unknown lookups fall through to
    the caller (no substitution).
    """
    table = col.table
    if table not in visible:
        return False
    container = visible[table].value
    if not isinstance(container, dict) or col.name not in container:
        return False
    params.append(container[col.name])
    col.replace(exp.Placeholder(this=str(len(params))))
    return True


def _build_scalar_placeholder(
    value: Any,
    type_name: str | None,
    params: list[Any],
) -> exp.Expression:
    """Append ``value`` to ``params`` and return its Placeholder expression.

    Preserves the declared variable type when DEFAULT NULL leaves
    ``value`` as Python ``None``. Without a CAST, the DuckDB driver
    binds NULL as the default INT64 type and the schema renderer
    surfaces the column as ``INTEGER`` even though the script said
    ``DECLARE x STRING DEFAULT NULL``. Wrapping the placeholder in
    a ``CAST(... AS <declared_type>)`` makes the type travel through
    to the wire schema — matching BigQuery's "declared type is
    authoritative" contract. STRUCT-valued variables short-circuit
    out via :func:`_rewrite_struct_field_column` so the CAST here
    only fires for scalar variables.
    """
    params.append(value)
    placeholder: exp.Expression = exp.Placeholder(this=str(len(params)))
    if value is None and type_name and type_name.upper() != "ANY":
        # Defensive: fall back to bare placeholder if the declared
        # type string is not parseable by ``exp.DataType.build``.
        with contextlib.suppress(Exception):
            placeholder = exp.Cast(this=placeholder, to=exp.DataType.build(type_name))
    return placeholder


def _is_top_level_projection(col: exp.Column) -> bool:
    """True when ``col`` is a top-level SELECT projection expression.

    ADR 0023 §1.E — BigQuery infers a projection's column name from
    the source identifier when the SELECT has no explicit AS
    (``SELECT label`` → column name ``label``). Replacing the column
    with a bound parameter erases that signal, so DuckDB falls back
    to ``$1``. Used by :func:`_rewrite_one_column` to wrap the
    placeholder in an alias and preserve the projected name.
    """
    return isinstance(col.parent, exp.Select) and col.arg_key == "expressions"


#: Matches ``BEGIN`` / ``BEGIN TRANSACTION`` / ``START TRANSACTION``,
#: ``COMMIT`` / ``COMMIT TRANSACTION`` / ``COMMIT WORK`` /
#: ``END TRANSACTION``, ``ROLLBACK`` / ``ROLLBACK TRANSACTION`` /
#: ``ROLLBACK WORK`` — with optional trailing semicolons and any
#: amount of internal whitespace. Used to dispatch BigQuery
#: transaction-control statements to the emulator-side snapshot
#: machinery instead of forwarding them to DuckDB.
_TXN_BEGIN_RE = re.compile(
    r"^\s*(?:BEGIN(?:\s+TRANSACTION)?|START\s+TRANSACTION)\s*;?\s*$", re.IGNORECASE
)
_TXN_COMMIT_RE = re.compile(
    r"^\s*(?:COMMIT(?:\s+(?:TRANSACTION|WORK))?|END\s+TRANSACTION)\s*;?\s*$", re.IGNORECASE
)
_TXN_ROLLBACK_RE = re.compile(r"^\s*ROLLBACK(?:\s+(?:TRANSACTION|WORK))?\s*;?\s*$", re.IGNORECASE)


def _classify_txn_statement(bq_sql: str) -> str | None:
    """Return ``"BEGIN"`` / ``"COMMIT"`` / ``"ROLLBACK"`` or ``None``.

    Used by :meth:`ScriptInterpreter._exec_sql` to short-circuit
    transaction-control SQL to the emulator-side snapshot machinery
    rather than forwarding the statement to DuckDB. ``BEGIN`` alone is
    classified as ``"BEGIN"`` only when the scripting parser routed it
    here — the parser disambiguates ``BEGIN TRANSACTION`` from
    ``BEGIN ... END`` blocks before reaching ``_exec_sql``.
    """
    if _TXN_BEGIN_RE.match(bq_sql):
        return "BEGIN"
    if _TXN_COMMIT_RE.match(bq_sql):
        return "COMMIT"
    if _TXN_ROLLBACK_RE.match(bq_sql):
        return "ROLLBACK"
    return None


def _dml_targets(bq_sql: str, project_id: str) -> list[str]:
    """Return the quoted DuckDB target table(s) modified by ``bq_sql``.

    The result is the same ``"<project>__<dataset>"."<table>"`` form
    that :mod:`bqemulator.sql.table_rewriter` produces; the transaction
    snapshot machinery uses it directly in ``CREATE TABLE … AS SELECT
    * FROM <target>`` to back up the table state.

    Parsing-only failures (DDL with non-standard syntax, vendor
    extensions outside the BigQuery dialect) return an empty list so
    the caller proceeds without a snapshot. The user-level ROLLBACK
    behaviour for those statements is identical to BigQuery's
    "DDL inside a transaction is auto-committed at statement boundary"
    semantic.
    """
    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — best-effort identification
        return []
    if not isinstance(tree, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        return []
    target = tree.this
    if isinstance(target, exp.Schema):
        target = target.this
    if not isinstance(target, exp.Table):
        return []
    db = target.db
    if not db:
        return []
    catalog = target.catalog or project_id
    schema = f"{catalog}__{db}"
    return [f'"{schema}"."{target.name}"']


def _is_row_producing(bq_sql: str) -> bool:
    """Return True if ``bq_sql`` is a row-producing statement.

    BigQuery's "last statement with output wins" rule (ADR 0023 §1.F)
    distinguishes SELECT / WITH / set-op statements (which contribute
    rows to the script's final result) from DDL / DML (which execute
    but emit no rows). Unparseable statements default to ``False`` so
    a malformed DDL never accidentally populates the final result.
    """
    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001
        return False
    return isinstance(tree, exp.Query)


def _is_truthy(value: Any) -> bool:
    """BigQuery-style truthiness for an IF / WHILE condition."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return len(value) > 0
    return True


async def _upsert_routine(
    ctx: AppContext,
    routine: RoutineMeta,
    *,
    or_replace: bool,
) -> None:
    """Insert or replace a routine via the catalog + UDF registry."""
    existing = ctx.catalog.get_routine(
        routine.project_id,
        routine.dataset_id,
        routine.routine_id,
    )
    async with ctx.engine.write_lock():
        if existing is not None and not or_replace:
            from bqemulator.domain.errors import resource_already_exists

            raise resource_already_exists(
                ResourceRef(
                    "routine",
                    routine.project_id,
                    routine.dataset_id,
                    routine.routine_id,
                ),
            )
        if existing is None:
            ctx.catalog.create_routine(routine)
        else:
            ctx.catalog.update_routine(routine)
        registry = getattr(ctx, "udf_registry", None)
        if registry is not None:
            registry.materialize(routine, ctx.engine)


async def run_script(ctx: AppContext, project_id: str, source: str) -> ScriptResult:
    """Top-level entry point for running a script.

    Any :class:`ScriptRaise` that escapes the script (no matching
    ``EXCEPTION WHEN ERROR THEN`` handler) is unwrapped to the
    underlying :class:`DomainError`. The REST and gRPC adapters then
    render it as a conventional BigQuery error, matching the behaviour
    real BigQuery has for scripts that ``RAISE`` without being caught.
    """
    interpreter = ScriptInterpreter(ctx, project_id)
    try:
        return await interpreter.run(source)
    except ScriptRaise as raised:
        raise raised.error from raised


__all__ = ["ScriptInterpreter", "ScriptResult", "run_script"]
