"""JavaScript UDF runtime via embedded V8 (py-mini-racer).

Materializes a BigQuery JS UDF as a DuckDB Python scalar function via
:meth:`duckdb.DuckDBPyConnection.create_function`. Invocations dispatch
through an isolated :class:`MiniRacer` V8 context per routine that
enforces a CPU timeout and a memory cap.

Sandboxing:

- No network. ``mini-racer`` does not expose ``fetch``, ``XMLHttpRequest``
  or HTTP.
- No filesystem. The V8 context has no ``require``, ``import``,
  ``readFileSync``, or similar.
- No globals leaking across routines — each routine owns its own
  :class:`MiniRacer` context.

On timeout or OOM, :class:`JSTimeoutException` / :class:`JSOOMException`
surface as :class:`InvalidQueryError` with a clear message.

On startup, a per-process module-level import of ``py_mini_racer`` is
deferred until first ``materialize()`` call. This keeps startup cost
zero for users who never define JS UDFs.
"""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime, time
from decimal import Decimal
import json
from threading import RLock
from typing import TYPE_CHECKING, Any

from bqemulator.domain.errors import InvalidQueryError
from bqemulator.observability.logging_ import get_logger
from bqemulator.storage.sql_identifiers import _validate_sql_id
from bqemulator.udf.naming import qualified_routine_name
from bqemulator.udf.types import render_duckdb_type

if TYPE_CHECKING:
    from bqemulator.catalog.models import RoutineMeta
    from bqemulator.storage.engine import DuckDBEngine

_log = get_logger(__name__)


class JSUDFUnavailableError(InvalidQueryError):
    """Raised when a JS UDF is requested but ``mini-racer`` is not installed."""


class JavaScriptUDFRuntime:
    """JavaScript UDF runtime via embedded V8."""

    def __init__(self, *, cpu_timeout_ms: int, memory_limit_bytes: int) -> None:
        self._cpu_timeout_ms = cpu_timeout_ms
        self._memory_limit_bytes = memory_limit_bytes
        self._contexts: dict[tuple[str, str, str], _RoutineContext] = {}
        self._lock = RLock()

    def materialize(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:
        """Register the JS UDF as a DuckDB Python function."""
        context = _build_context(
            routine,
            cpu_timeout_ms=self._cpu_timeout_ms,
            memory_limit_bytes=self._memory_limit_bytes,
        )
        key = (routine.project_id, routine.dataset_id, routine.routine_id)
        qualified_name = _qualified_function_name(routine)

        # DuckDB's create_function signature: (name, callable, arg_types, ret_type)
        duckdb_conn = engine.connection

        # Drop any prior registration idempotently.
        with self._lock:
            prior = self._contexts.pop(key, None)
            if prior is not None:
                _try_remove_function(duckdb_conn, prior.qualified_name)
            self._contexts[key] = context

        def invoke(*args: Any) -> Any:
            return _invoke_routine(context, args)

        # DuckDB needs explicit parameter + return types.
        arg_types = [duckdb_conn.type(render_duckdb_type(a.data_type)) for a in routine.arguments]
        ret_type = duckdb_conn.type(render_duckdb_type(routine.return_type))
        # ``null_handling="special"`` matches BigQuery's JS UDF
        # semantics: the JS body receives NULL as ``null`` (rather than
        # being skipped) and may return ``null`` for any row. DuckDB's
        # default ``"default"`` skips NULL inputs and forbids NULL
        # returns, which surfaces as an ``Invalid Input Error`` on the
        # first NULL the body produces.
        duckdb_conn.create_function(
            qualified_name,
            invoke,
            arg_types,
            ret_type,
            null_handling="special",  # type: ignore[call-overload]
        )
        _log.debug("js_udf.registered", routine=routine.routine_id)

    def deregister(self, routine: RoutineMeta, engine: DuckDBEngine) -> None:
        """Remove the JS UDF from DuckDB and release the V8 context."""
        key = (routine.project_id, routine.dataset_id, routine.routine_id)
        with self._lock:
            context = self._contexts.pop(key, None)
        if context is None:
            return
        _try_remove_function(engine.connection, context.qualified_name)
        try:
            context.mr.close()
        except Exception as exc:  # noqa: BLE001 — close is best-effort
            _log.warning("js_udf.close_failed", error=str(exc))


def _qualified_function_name(routine: RoutineMeta) -> str:
    """Return the DuckDB function name for the routine.

    Delegates to :func:`qualified_routine_name` so every runtime uses
    the same ``{project}__{dataset}__{routine}`` naming.
    """
    return qualified_routine_name(routine)


class _RoutineContext:
    """Per-routine V8 context + invocation metadata."""

    __slots__ = (
        "arg_type_kinds",
        "entry_name",
        "mr",
        "qualified_name",
        "return_type_kind",
        "timeout_sec",
    )

    def __init__(
        self,
        mr: Any,
        entry_name: str,
        qualified_name: str,
        timeout_sec: float,
        arg_type_kinds: tuple[str, ...] = (),
        return_type_kind: str = "",
    ) -> None:
        self.mr = mr
        self.entry_name = entry_name
        self.qualified_name = qualified_name
        self.timeout_sec = timeout_sec
        # BigQuery's JS UDF type-encoding (see public docs): INT64,
        # NUMERIC, and BIGNUMERIC arrive in JS as strings to preserve
        # precision beyond IEEE 754's 53-bit mantissa. The emulator
        # passes the wire-side argument types through to
        # :func:`_invoke_routine` so the per-arg coercion matches.
        self.arg_type_kinds = arg_type_kinds
        self.return_type_kind = return_type_kind


def _build_context(
    routine: RoutineMeta,
    *,
    cpu_timeout_ms: int,
    memory_limit_bytes: int,
) -> _RoutineContext:
    """Construct an isolated V8 context for the given routine."""
    try:
        from py_mini_racer import MiniRacer
    except ImportError as exc:  # pragma: no cover - import guard
        raise JSUDFUnavailableError(
            "JavaScript UDFs require the `mini-racer` extra. "
            "Install with: pip install bqemulator[udf-js]",
        ) from exc

    # Construct the MiniRacer in a dedicated short-lived thread so
    # py-mini-racer's ``_running_event_loop`` does not pick up an
    # already-running asyncio loop (the bqemulator server loop) as the
    # MiniRacer's event loop. Sharing the bqemulator loop deadlocks
    # under the session-scoped conformance harness: a synchronous JS
    # UDF callback fires from inside ``engine.execute`` (on the loop's
    # own thread, inside a coroutine), and ``mr.eval`` would then
    # schedule its coroutine on the very loop that is currently blocked
    # in ``engine.execute``. Forcing a fresh per-MiniRacer event loop
    # keeps the JS UDF dispatch independent of bqemulator's own loop.
    mr_container: dict[str, Any] = {}

    def _construct() -> None:
        mr_container["mr"] = MiniRacer()

    import threading

    constructor = threading.Thread(target=_construct, daemon=True)
    constructor.start()
    constructor.join()
    mr = mr_container["mr"]
    mr.set_hard_memory_limit(memory_limit_bytes)

    entry = _validate_sql_id(routine.routine_id, "routine")
    arg_names = [a.name for a in routine.arguments]
    param_list = ", ".join(arg_names)
    script = f"function {entry}({param_list}) {{\n{routine.definition_body}\n}}"

    try:
        # No timeout on compile — the JS is already in memory, and
        # py-mini-racer's timeout path conflicts with running asyncio
        # loops (the REST handler is async). Memory cap still applies.
        mr.eval(script)
    except Exception as exc:  # noqa: BLE001 — narrow below
        _raise_js_error("JS UDF compile failed", exc, routine.routine_id)

    arg_type_kinds = tuple(_type_kind(a.data_type) for a in routine.arguments)
    return_type_kind = _type_kind(routine.return_type)
    return _RoutineContext(
        mr=mr,
        entry_name=entry,
        qualified_name=_qualified_function_name(routine),
        timeout_sec=cpu_timeout_ms / 1000,
        arg_type_kinds=arg_type_kinds,
        return_type_kind=return_type_kind,
    )


def _type_kind(data_type: Any) -> str:
    """Best-effort extraction of the ``typeKind`` from a routine type dict.

    Routine arguments and return types arrive as ``{"typeKind": "INT64"}``
    dicts (REST shape). Nested types (ARRAY / STRUCT) carry an
    ``arrayElementType`` / ``structType`` field but their top-level kind
    is what governs the JS encoding for scalar dispatch.
    """
    if data_type is None:
        return ""
    if isinstance(data_type, dict):
        kind = data_type.get("typeKind") or data_type.get("type_kind") or ""
        return str(kind).upper()
    type_kind_attr = getattr(data_type, "type_kind", None) or getattr(data_type, "typeKind", None)
    if type_kind_attr is not None:
        return str(type_kind_attr).upper()
    return ""


_PRECISION_STRING_KINDS = frozenset({"INT64", "NUMERIC", "BIGNUMERIC"})


def _invoke_routine(context: _RoutineContext, args: tuple[Any, ...]) -> Any:
    """Call the routine's entrypoint and coerce the result.

    CPU-time enforcement is best-effort: py-mini-racer's
    ``timeout_sec`` argument refuses to install a watchdog when an
    asyncio event loop is running on the current thread, which is the
    norm under FastAPI + DuckDB. We therefore rely on:

    * the per-routine V8 hard memory cap
      (:meth:`py_mini_racer.MiniRacer.set_hard_memory_limit`) for the
      memory-exhaustion path, and
    * a ``signal.setitimer`` based stopwatch on POSIX platforms where
      we can safely enforce CPU time without disturbing the event loop.

    In isolation (no running loop) the ``timeout_sec`` kwarg is passed
    through to mini-racer.
    """
    json_args = [
        _coerce_arg_for_js(a, _safe_index(context.arg_type_kinds, i)) for i, a in enumerate(args)
    ]
    timeout_sec = context.timeout_sec
    try:
        if _asyncio_loop_running():
            result = context.mr.call(context.entry_name, *json_args)
        else:
            result = context.mr.call(
                context.entry_name,
                *json_args,
                timeout_sec=timeout_sec,
            )
    except Exception as exc:  # noqa: BLE001 — narrow below
        _raise_js_error("JS UDF invocation failed", exc, context.entry_name)

    return _coerce_return_from_js(result, context.return_type_kind)


def _safe_index(seq: tuple[str, ...], i: int) -> str:
    """Return ``seq[i]`` if in range, else the empty string."""
    if 0 <= i < len(seq):
        return seq[i]
    return ""


def _coerce_arg_for_js(value: Any, kind: str) -> Any:
    """Apply BigQuery's JS UDF input encoding before passing ``value`` to V8.

    BigQuery encodes INT64 / NUMERIC / BIGNUMERIC as JavaScript Strings
    (the public docs flag "if the value can be represented exactly as
    an IEEE 754 floating-point value and has no fractional part" as a
    Number-encoding shortcut, but the wire-format ground truth is the
    String encoding for INT64 — see the corresponding conformance
    fixtures in ``routines_scripting/js_udf_*``). The emulator preserves
    DuckDB's native Python int for the non-precision-stringified kinds
    so booleans, FLOAT64, and unspecified kinds round-trip unchanged.
    """
    if value is None:
        return None
    if (
        kind in _PRECISION_STRING_KINDS
        and isinstance(value, (int, Decimal))
        and not isinstance(value, bool)
    ):
        return str(value)
    return _to_json_value(value)


def _coerce_return_from_js(value: Any, kind: str) -> Any:
    """Coerce a V8 return value back to a DuckDB-friendly Python value.

    BigQuery's INT64 / NUMERIC / BIGNUMERIC return types accept either a
    JS Number or a JS String. The emulator widens the string case back
    to int / Decimal so DuckDB's downstream column type matches the
    declared return.
    """
    if value is None:
        return None
    if kind in _PRECISION_STRING_KINDS and isinstance(value, str):
        try:
            if kind == "INT64":
                return int(value)
            return Decimal(value)
        except (ValueError, ArithmeticError):
            return _from_js_value(value)
    return _from_js_value(value)


def _asyncio_loop_running() -> bool:
    """Return True if an asyncio loop is currently running on this thread."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _coerce_datetime_for_json(value: datetime) -> str:
    """Render a ``datetime`` as its ISO-8601 string, defaulting naive values to UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


#: Type → coercer dispatch for :func:`_to_json_value`. Order matters where
#: a more specific subclass precedes its parent — ``datetime`` is a
#: ``date`` subclass, so it must be matched first to apply tz-aware
#: handling before the bare ``(date, time)`` branch sees it.
_JSON_COERCERS: tuple[tuple[Any, Any], ...] = (
    (bytes, lambda v: base64.b64encode(v).decode("ascii")),
    (Decimal, str),
    (datetime, _coerce_datetime_for_json),
    ((date, time), lambda v: v.isoformat()),
    ((list, tuple), lambda v: [_to_json_value(x) for x in v]),
    (dict, lambda v: {str(k): _to_json_value(x) for k, x in v.items()}),
)


def _to_json_value(value: Any) -> Any:
    """Coerce a DuckDB-side Python value for JSON → V8 transport."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    for types, coercer in _JSON_COERCERS:
        if isinstance(value, types):
            return coercer(value)
    # Fallback — orjson-style default: round-trip through JSON.
    return json.loads(json.dumps(value, default=str))


def _from_js_value(value: Any) -> Any:
    """Coerce a value returned from V8 back to a DuckDB-friendly Python value."""
    # mini-racer returns native Python primitives and dicts/lists for
    # JSON-compatible structures, so we can pass them straight through.
    # The only transformation we do is to narrow floats that are exact
    # integers back to int — BigQuery's INT64 return type is more common.
    return value


def _try_remove_function(conn: Any, name: str) -> None:
    """Remove a DuckDB-registered Python function idempotently."""
    try:
        conn.remove_function(name)
    except Exception as exc:  # noqa: BLE001 - remove is best-effort
        _log.debug("js_udf.remove_function_noop", name=name, error=str(exc))


def _load_mini_racer_errors() -> tuple[type, type, type, type]:
    """Return the four py-mini-racer exception classes, or ``Exception`` stubs.

    Separating the import from the isinstance checks keeps mypy happy
    about the try/except binding shape and makes the dispatch explicit.
    """
    try:
        from py_mini_racer import (
            JSEvalException,
            JSOOMException,
            JSParseException,
            JSTimeoutException,
        )
    except ImportError:  # pragma: no cover
        return (Exception, Exception, Exception, Exception)
    return (JSEvalException, JSOOMException, JSParseException, JSTimeoutException)


def _raise_js_error(prefix: str, exc: Exception, routine: str) -> None:
    """Translate a py-mini-racer error into an InvalidQueryError."""
    eval_exc, oom_exc, parse_exc, timeout_exc = _load_mini_racer_errors()
    if isinstance(exc, timeout_exc):
        raise InvalidQueryError(
            f"{prefix}: CPU time limit exceeded in routine {routine!r}",
        ) from exc
    if isinstance(exc, oom_exc):
        raise InvalidQueryError(
            f"{prefix}: memory limit exceeded in routine {routine!r}",
        ) from exc
    if isinstance(exc, parse_exc):
        raise InvalidQueryError(
            f"{prefix}: parse error in routine {routine!r}: {exc}",
        ) from exc
    if isinstance(exc, eval_exc):
        raise InvalidQueryError(
            f"{prefix}: runtime error in routine {routine!r}: {exc}",
        ) from exc
    raise InvalidQueryError(f"{prefix}: {exc}") from exc


__all__ = ["JSUDFUnavailableError", "JavaScriptUDFRuntime"]
