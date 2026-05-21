# Adding a UDF runtime

v1 ships three runtimes: SQL, JavaScript (V8), and table-valued. Adding
a new runtime (e.g. WebAssembly, or a sandboxed Python subset) follows
this pattern:

1. Implement a class in `src/bqemulator/udf/<lang>_udf.py` with the
   interface:

 ```python
   class UDFRuntime(Protocol):
       language: str
       def register(self, routine: RoutineMeta) -> None: ...
       def invoke(self, name: str, args: list[Any]) -> Any: ...
       def close(self) -> None: ...
   ```

2. Register it with `bqemulator.udf.runtime.UDFDispatcher` at startup.
3. Open an [RFC](../../rfcs/README.md) for the new language — it
   affects the public SQL surface.
4. Ship:
 - Unit tests covering type conversion.
 - Integration tests running real UDFs.
 - E2E test against all four client languages invoking the UDF.
 - Docs page in `docs/guides/`.
 - ADR capturing the runtime choice.

Reference implementation: `js_udf.py` (mini-racer).
