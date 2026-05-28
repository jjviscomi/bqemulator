"""Python-backed scalar UDFs registered on every DuckDB connection.

DuckDB ships a large but incomplete catalogue of scalar functions. Several
BigQuery builtins our SQLGlot translator targets have no direct DuckDB
equivalent — ``JSON_REMOVE``, ``JSON_SET``, ``JSON_STRIP_NULLS``,
``NORMALIZE``, ``NORMALIZE_AND_CASEFOLD``, ``FARM_FINGERPRINT`` are the
canonical examples. Rather than reject the queries at translate time, we
register narrow Python implementations under ``bqemu_*`` names at engine
startup; the SQLGlot post-translator rules in
:mod:`bqemulator.sql.rules.json_helpers` and friends rewrite each
BigQuery call to its matching helper.

The contract is intentionally minimal:

* Each helper is a *pure* function — no I/O, no global state.
* Inputs and outputs are JSON-friendly: strings for JSON values, primitive
  Python types for scalars.
* ``None`` propagates as ``NULL`` (BigQuery's null semantics).

Helpers are registered exactly once per :class:`DuckDBEngine`; concurrent
``register_builtin_udfs`` calls are protected by the engine's lifecycle —
``start`` runs to completion before any query executes.
"""

from __future__ import annotations

import base64
import datetime as _datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
import struct
from typing import TYPE_CHECKING, Any, Literal, cast
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:  # pragma: no cover
    import duckdb


_JSON_PATH_DELIM = "."
_NormalizeForm = Literal["NFC", "NFD", "NFKC", "NFKD"]
_VALID_NORMALIZE_FORMS: frozenset[str] = frozenset({"NFC", "NFD", "NFKC", "NFKD"})


def _coerce_normalize_form(form: str | None) -> _NormalizeForm:
    """Return *form* as one of the four ``unicodedata``-accepted forms.

    ``None`` and any unrecognised value fall back to ``"NFC"`` — the
    BigQuery documented default.
    """
    upper = (form or "NFC").upper()
    if upper not in _VALID_NORMALIZE_FORMS:
        return "NFC"
    return cast("_NormalizeForm", upper)


def _split_json_path(path: str) -> list[str]:
    """Parse a BigQuery ``$.a.b.c`` JSONPath into its keys.

    The supported subset matches the conformance corpus: dotted keys only,
    no array indices, no bracket-quoted keys. Returns an empty list when
    the path is exactly ``$``.
    """
    if not path.startswith("$"):
        msg = f"JSONPath must start with '$': {path!r}"
        raise ValueError(msg)
    tail = path[1:]
    if not tail:
        return []
    if not tail.startswith(_JSON_PATH_DELIM):
        msg = f"unsupported JSONPath syntax: {path!r}"
        raise ValueError(msg)
    return [segment for segment in tail.split(_JSON_PATH_DELIM) if segment]


def bqemu_json_remove(value: str | None, path: str | None) -> str | None:
    """Return *value* with the key at *path* removed.

    Mirrors BigQuery's ``JSON_REMOVE(json_value, path)`` for the dotted-
    path subset our conformance corpus exercises. Returns ``value``
    unchanged if the key does not exist; returns ``None`` if either input
    is ``NULL``.
    """
    if value is None or path is None:
        return None
    obj = json.loads(value)
    keys = _split_json_path(path)
    if not keys:
        return value
    cursor: Any = obj
    for key in keys[:-1]:
        if not isinstance(cursor, dict) or key not in cursor:
            return json.dumps(obj)
        cursor = cursor[key]
    if isinstance(cursor, dict):
        cursor.pop(keys[-1], None)
    return json.dumps(obj)


def bqemu_json_set(value: str | None, path: str | None, new_value_json: str | None) -> str | None:
    """Return *value* with *new_value_json* set at *path*.

    Mirrors BigQuery's two-argument ``JSON_SET(json_value, path, value)``.
    The path must be ``$`` (replace root) or ``$.a.b…`` (set a nested
    key, creating intermediate objects as needed). ``new_value_json`` is
    the JSON-serialised form of the value to set — the SQLGlot rule
    wraps the BigQuery operand in ``TO_JSON(...)`` before passing it
    through so DuckDB doesn't need a polymorphic-argument UDF.
    """
    if value is None or path is None or new_value_json is None:
        return None
    new_value = json.loads(new_value_json)
    obj = json.loads(value)
    keys = _split_json_path(path)
    if not keys:
        return json.dumps(new_value)
    cursor: Any = obj
    for key in keys[:-1]:
        if not isinstance(cursor, dict):
            return json.dumps(obj)
        cursor = cursor.setdefault(key, {})
    if isinstance(cursor, dict):
        cursor[keys[-1]] = new_value
    return json.dumps(obj)


def bqemu_json_strip_nulls(value: str | None) -> str | None:
    """Return *value* with every ``null`` member removed recursively.

    Mirrors BigQuery's ``JSON_STRIP_NULLS(json_value)``. Object members
    whose value is ``null`` are dropped; array elements are kept (the
    BigQuery contract).
    """
    if value is None:
        return None

    def _strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: _strip(v) for k, v in node.items() if v is not None}
        if isinstance(node, list):
            return [_strip(elem) for elem in node]
        return node

    return json.dumps(_strip(json.loads(value)))


_JSON_ARRAY_INSERT_TOKEN_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(-?\d+)\]")


def _parse_json_array_insert_path(path: str) -> list[tuple[str, str | int]]:
    """Tokenize a BigQuery ``$[N]`` / ``$.key`` / ``$.key[N]`` path.

    Returns a list of ``(kind, value)`` tuples where ``kind`` is
    ``"key"`` (object navigation) or ``"index"`` (array navigation).
    Raises :class:`ValueError` on a malformed path so the caller can
    surface ``NULL`` (matching BigQuery's "non-existent path does not
    modify the JSON" contract — we follow it by short-circuiting).
    """
    if not path.startswith("$"):
        msg = f"unsupported JSONPath syntax: {path!r}"
        raise ValueError(msg)
    tokens: list[tuple[str, str | int]] = []
    pos = 1
    while pos < len(path):
        match = _JSON_ARRAY_INSERT_TOKEN_RE.match(path, pos)
        if match is None:
            msg = f"unsupported JSONPath syntax at offset {pos}: {path!r}"
            raise ValueError(msg)
        key, idx = match.groups()
        if key is not None:
            tokens.append(("key", key))
        else:
            tokens.append(("index", int(idx)))
        pos = match.end()
    return tokens


def bqemu_json_array_insert(
    json_str: str | None,
    path: str | None,
    value_json: str | None,
) -> str | None:
    """BigQuery ``JSON_ARRAY_INSERT(json_doc, path, value)``: insert into a JSON array.

    BigQuery's ``JSON_ARRAY_INSERT`` adds ``value`` to the array
    referenced by ``path`` while preserving every existing element.
    The supported path subset matches the conformance corpus:

    * ``$[N]`` — top-level array at index ``N``;
    * ``$.key[N]`` — array under a top-level key;
    * ``$.key1.key2[N]`` — nested array under chained object keys.

    Following BigQuery's documented edge-case contract:

    * Out-of-bounds positive index → append (clamped to end of array);
    * Negative or zero index that doesn't resolve to a real position
      under the array → no-op (returns the input unchanged);
    * Path that doesn't end at a valid array → no-op;
    * Any of ``json_str`` / ``path`` / ``value_json`` is ``NULL`` →
      returns ``NULL``.

    ``value_json`` is the JSON-serialised form of the value to
    insert. The translator wraps the BigQuery operand in
    ``CAST(TO_JSON(...) AS VARCHAR)`` before passing it through so the
    helper doesn't need polymorphic dispatch.
    """
    if json_str is None or path is None or value_json is None:
        return None
    try:
        obj = json.loads(json_str)
        value = json.loads(value_json)
    except (ValueError, TypeError):
        return json_str
    try:
        tokens = _parse_json_array_insert_path(path)
    except ValueError:
        return json_str
    if not tokens or tokens[-1][0] != "index":
        return json_str
    target_index = cast("int", tokens[-1][1])
    if target_index < 0:
        return json_str
    cursor = _walk_json_path_to_array(obj, tokens[:-1])
    if cursor is None:
        return json_str
    insert_at = min(target_index, len(cursor))
    cursor.insert(insert_at, value)
    return json.dumps(obj)


def _walk_json_path_to_array(obj: Any, tokens: list[tuple[str, Any]]) -> list[Any] | None:
    """Walk ``obj`` through the parsed path ``tokens``; return the final array.

    Returns ``None`` if any step doesn't resolve cleanly:

    * ``key`` step where the cursor isn't a dict or the key is absent;
    * ``index`` step where the cursor isn't a list or the index is
      out of range;
    * final cursor isn't a list (the array we're going to insert into).
    """
    cursor: Any = obj
    for kind, value_step in tokens:
        if kind == "key":
            if not isinstance(cursor, dict) or value_step not in cursor:
                return None
            cursor = cursor[value_step]
        else:
            idx = cast("int", value_step)
            if not isinstance(cursor, list) or not 0 <= idx < len(cursor):
                return None
            cursor = cursor[idx]
    if not isinstance(cursor, list):
        return None
    return cursor


def bqemu_normalize(value: str | None, form: str | None) -> str | None:
    """Return *value* under Unicode normalization *form*.

    *form* is one of ``"NFC"``, ``"NFD"``, ``"NFKC"``, ``"NFKD"`` — the
    same four BigQuery accepts. Falls back to ``"NFC"`` when ``form`` is
    ``None`` (matches BigQuery's default).
    """
    if value is None:
        return None
    return unicodedata.normalize(_coerce_normalize_form(form), value)


def bqemu_normalize_casefold(value: str | None, form: str | None) -> str | None:
    """Return *value* normalized then case-folded.

    Mirrors BigQuery's ``NORMALIZE_AND_CASEFOLD(value, form)``.
    Case-folding lowers and expands locale-insensitive case mappings (so
    German ``Straße`` becomes ``strasse``) — Python's ``str.casefold``
    matches BigQuery's behaviour for the corpus we exercise.
    """
    if value is None:
        return None
    return unicodedata.normalize(_coerce_normalize_form(form), value).casefold()


def bqemu_to_bignumeric(value: str | None) -> Decimal | None:
    """Return *value* parsed as a ``Decimal`` typed for BIGNUMERIC on the wire.

    Mirrors BigQuery's ``BIGNUMERIC 'literal'`` typed-literal +
    ``PARSE_BIGNUMERIC(string)`` semantics. The helper exists so the
    SQLGlot pre-translator can emit ``bqemu_to_bignumeric('…')`` —
    DuckDB then dispatches through a fixed ``DECIMAL(38, 10)`` return
    signature and our schema renderer surfaces the column as
    ``BIGNUMERIC`` (any DECIMAL with ``scale > 9`` is BIGNUMERIC per
    ADR 0023 §1.B closure).

    The Python implementation parses with :class:`Decimal` so the
    integer-side capacity (up to 28 digits given the DECIMAL(38, 10)
    return signature) is decoupled from DuckDB's literal-side default
    of DECIMAL(18, 3). Values that exceed DECIMAL(38, …)'s 38-digit
    cap remain unrepresentable — those cascade to ADR 0023 §1.I
    (see ``bound_bignumeric_max``).
    """
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        msg = f"Invalid BIGNUMERIC literal: {value!r}"
        raise ValueError(msg) from exc


# FarmHash Fingerprint64 constants (from farmhashna.cc).
_FH_K0 = 0xC3A5C85C97CB3127
_FH_K1 = 0xB492B66FBE98F273
_FH_K2 = 0x9AE16A3B2F90404F
_U64_MASK = 0xFFFFFFFFFFFFFFFF
_U32_MASK = 0xFFFFFFFF
_I64_SIGN_BIT = 1 << 63


def _fh_fetch64(data: bytes, offset: int) -> int:
    return cast("int", struct.unpack_from("<Q", data, offset)[0])


def _fh_fetch32(data: bytes, offset: int) -> int:
    return cast("int", struct.unpack_from("<I", data, offset)[0])


def _fh_rotate(val: int, shift: int) -> int:
    val &= _U64_MASK
    if shift == 0:
        return val
    return ((val >> shift) | (val << (64 - shift))) & _U64_MASK


def _fh_shift_mix(val: int) -> int:
    return (val ^ (val >> 47)) & _U64_MASK


def _fh_hash_len_16_mul(u: int, v: int, mul: int) -> int:
    a = ((u ^ v) * mul) & _U64_MASK
    a ^= a >> 47
    a &= _U64_MASK
    b = ((v ^ a) * mul) & _U64_MASK
    b ^= b >> 47
    b &= _U64_MASK
    return (b * mul) & _U64_MASK


def _fh_hash_len_16(u: int, v: int) -> int:
    # K_MUL is 0x9DDFEA08EB382D69 in FarmHash's HashLen16(u, v).
    return _fh_hash_len_16_mul(u, v, 0x9DDFEA08EB382D69)


def _fh_hash_len_0_to_16(data: bytes) -> int:
    length = len(data)
    if length >= 8:  # noqa: PLR2004
        mul = (_FH_K2 + length * 2) & _U64_MASK
        a = (_fh_fetch64(data, 0) + _FH_K2) & _U64_MASK
        b = _fh_fetch64(data, length - 8)
        c = (_fh_rotate(b, 37) * mul + a) & _U64_MASK
        d = ((_fh_rotate(a, 25) + b) * mul) & _U64_MASK
        return _fh_hash_len_16_mul(c, d, mul)
    if length >= 4:  # noqa: PLR2004
        mul = (_FH_K2 + length * 2) & _U64_MASK
        a = _fh_fetch32(data, 0)
        return _fh_hash_len_16_mul(
            (length + (a << 3)) & _U64_MASK,
            _fh_fetch32(data, length - 4),
            mul,
        )
    if length > 0:
        a = data[0]
        b = data[length >> 1]
        c = data[length - 1]
        y = (a + (b << 8)) & _U64_MASK
        z = (length + (c << 2)) & _U64_MASK
        return (_fh_shift_mix(((y * _FH_K2) ^ (z * _FH_K0)) & _U64_MASK) * _FH_K2) & _U64_MASK
    return _FH_K2


def _fh_hash_len_17_to_32(data: bytes) -> int:
    length = len(data)
    mul = (_FH_K2 + length * 2) & _U64_MASK
    a = (_fh_fetch64(data, 0) * _FH_K1) & _U64_MASK
    b = _fh_fetch64(data, 8)
    c = (_fh_fetch64(data, length - 8) * mul) & _U64_MASK
    d = (_fh_fetch64(data, length - 16) * _FH_K2) & _U64_MASK
    return _fh_hash_len_16_mul(
        (_fh_rotate((a + b) & _U64_MASK, 43) + _fh_rotate(c, 30) + d) & _U64_MASK,
        (a + _fh_rotate((b + _FH_K2) & _U64_MASK, 18) + c) & _U64_MASK,
        mul,
    )


def _fh_hash_len_33_to_64(data: bytes) -> int:
    length = len(data)
    mul = (_FH_K2 + length * 2) & _U64_MASK
    a = (_fh_fetch64(data, 0) * _FH_K2) & _U64_MASK
    b = _fh_fetch64(data, 8)
    c = (_fh_fetch64(data, length - 8) * mul) & _U64_MASK
    d = (_fh_fetch64(data, length - 16) * _FH_K2) & _U64_MASK
    y = (_fh_rotate((a + b) & _U64_MASK, 43) + _fh_rotate(c, 30) + d) & _U64_MASK
    z = _fh_hash_len_16_mul(
        y,
        (a + _fh_rotate((b + _FH_K2) & _U64_MASK, 18) + c) & _U64_MASK,
        mul,
    )
    e = (_fh_fetch64(data, 16) * mul) & _U64_MASK
    f = _fh_fetch64(data, 24)
    g = ((y + _fh_fetch64(data, length - 32)) * mul) & _U64_MASK
    h = ((z + _fh_fetch64(data, length - 24)) * mul) & _U64_MASK
    return _fh_hash_len_16_mul(
        (_fh_rotate((e + f) & _U64_MASK, 43) + _fh_rotate(g, 30) + h) & _U64_MASK,
        (e + _fh_rotate((f + a) & _U64_MASK, 18) + g) & _U64_MASK,
        mul,
    )


def _fh_weak_hash_len_32_with_seeds(
    data: bytes,
    offset: int,
    a: int,
    b: int,
) -> tuple[int, int]:
    w = _fh_fetch64(data, offset)
    x = _fh_fetch64(data, offset + 8)
    y = _fh_fetch64(data, offset + 16)
    z = _fh_fetch64(data, offset + 24)
    a = (a + w) & _U64_MASK
    b = _fh_rotate((b + a + z) & _U64_MASK, 21)
    c = a
    a = (a + x) & _U64_MASK
    a = (a + y) & _U64_MASK
    b = (b + _fh_rotate(a, 44)) & _U64_MASK
    return (a + z) & _U64_MASK, (b + c) & _U64_MASK


def _fh_hash_len_long(data: bytes) -> int:
    length = len(data)
    seed = 81
    x = (seed * _FH_K2 + _fh_fetch64(data, 0)) & _U64_MASK
    y = (seed * _FH_K1 + 113) & _U64_MASK
    z = (_fh_shift_mix((y * _FH_K2 + 113) & _U64_MASK) * _FH_K2) & _U64_MASK
    v = (0, 0)
    w = (0, 0)
    x = (x * _FH_K2 + _fh_fetch64(data, 0)) & _U64_MASK
    offset = 0
    remaining = length
    while remaining > 64:  # noqa: PLR2004
        x = (
            _fh_rotate((x + y + v[0] + _fh_fetch64(data, offset + 8)) & _U64_MASK, 37) * _FH_K1
        ) & _U64_MASK
        y = (
            _fh_rotate((y + v[1] + _fh_fetch64(data, offset + 48)) & _U64_MASK, 42) * _FH_K1
        ) & _U64_MASK
        x ^= w[1]
        x &= _U64_MASK
        y = (y + v[0] + _fh_fetch64(data, offset + 40)) & _U64_MASK
        z = _fh_rotate((z + w[0]) & _U64_MASK, 33) * _FH_K1
        v = _fh_weak_hash_len_32_with_seeds(
            data,
            offset,
            (v[1] * _FH_K1) & _U64_MASK,
            (x + w[0]) & _U64_MASK,
        )
        w = _fh_weak_hash_len_32_with_seeds(
            data,
            offset + 32,
            (z + w[1]) & _U64_MASK,
            (y + _fh_fetch64(data, offset + 16)) & _U64_MASK,
        )
        x, z = z & _U64_MASK, x & _U64_MASK
        offset += 64
        remaining -= 64
    mul = (_FH_K1 + ((z & 0xFF) << 1)) & _U64_MASK
    # Make ``offset`` point at the last 64 bytes — overlap is fine.
    offset = length - 64
    remaining = 64
    w = ((w[0] + ((length - 1) & 63)) & _U64_MASK, w[1])
    v = ((v[0] + z) & _U64_MASK, v[1])
    w = ((w[0] + v[0]) & _U64_MASK, w[1])
    v = ((v[0] + w[0]) & _U64_MASK, v[1])
    x = (
        _fh_rotate((x + y + v[0] + _fh_fetch64(data, offset + 8)) & _U64_MASK, 37) * mul
    ) & _U64_MASK
    y = (_fh_rotate((y + v[1] + _fh_fetch64(data, offset + 48)) & _U64_MASK, 42) * mul) & _U64_MASK
    x ^= (w[1] * 9) & _U64_MASK
    x &= _U64_MASK
    y = (y + ((v[0] * 9) & _U64_MASK) + _fh_fetch64(data, offset + 40)) & _U64_MASK
    z = (_fh_rotate((z + w[0]) & _U64_MASK, 33) * mul) & _U64_MASK
    v = _fh_weak_hash_len_32_with_seeds(
        data,
        offset,
        (v[1] * mul) & _U64_MASK,
        (x + w[0]) & _U64_MASK,
    )
    w = _fh_weak_hash_len_32_with_seeds(
        data,
        offset + 32,
        (z + w[1]) & _U64_MASK,
        (y + _fh_fetch64(data, offset + 16)) & _U64_MASK,
    )
    x, z = z & _U64_MASK, x & _U64_MASK
    return _fh_hash_len_16_mul(
        _fh_hash_len_16_mul(v[0], w[0], mul) + ((_fh_shift_mix(y) * _FH_K0) & _U64_MASK) + z,
        (_fh_hash_len_16_mul(v[1], w[1], mul) + x) & _U64_MASK,
        mul,
    )


def _farm_fingerprint64(data: bytes) -> int:
    """Return FarmHash ``Fingerprint64`` as an unsigned 64-bit int."""
    length = len(data)
    if length <= 16:  # noqa: PLR2004
        return _fh_hash_len_0_to_16(data)
    if length <= 32:  # noqa: PLR2004
        return _fh_hash_len_17_to_32(data)
    if length <= 64:  # noqa: PLR2004
        return _fh_hash_len_33_to_64(data)
    return _fh_hash_len_long(data)


def bqemu_farm_fingerprint(value: str | None) -> int | None:
    """Return BigQuery's ``FARM_FINGERPRINT`` (FarmHash ``Fingerprint64``).

    Pure-Python port of farmhashna's ``Hash64`` (the algorithm BigQuery
    uses for ``FARM_FINGERPRINT``). Bit-exact with real BigQuery for
    every input we exercise in the conformance corpus. The return is
    a *signed* 64-bit integer — values with the high bit set surface
    as negative, matching BigQuery's wire-format.
    """
    if value is None:
        return None
    data = value.encode("utf-8")
    unsigned = _farm_fingerprint64(data)
    if unsigned & _I64_SIGN_BIT:
        return unsigned - (1 << 64)
    return unsigned


def bqemu_upper_unicode(value: str | None) -> str | None:
    """Return *value*'s Unicode-correct upper-case form.

    DuckDB's ``UPPER`` does not apply the German eszett rule (``ß`` →
    ``SS``) and other multi-character upper-case mappings. Python's
    ``str.upper`` follows the Unicode case-mapping table, which is what
    BigQuery does.
    """
    if value is None:
        return None
    return value.upper()


def bqemu_instr_occurrence(
    haystack: str | None,
    needle: str | None,
    start: int | None,
    occurrence: int | None,
) -> int | None:
    """BigQuery 4-argument ``INSTR(haystack, needle, start, occurrence)``.

    Returns the 1-based position of the *occurrence*-th match of
    ``needle`` in ``haystack`` starting from position ``start``.
    ``start`` is 1-based and may be negative (count from the end of
    ``haystack``). Returns 0 when no such occurrence exists.
    BigQuery semantics: ``occurrence`` must be > 0; ``start`` must be
    non-zero. NULL inputs propagate as NULL.
    """
    if None in (haystack, needle, start, occurrence):
        return None
    # mypy: the None check above narrows all four to their non-None type.
    assert haystack is not None  # noqa: S101
    assert needle is not None  # noqa: S101
    assert start is not None  # noqa: S101
    assert occurrence is not None  # noqa: S101
    if needle == "":
        return 0
    # BigQuery raises on either invalid input; we surface NULL to keep the
    # query running rather than failing the whole pipeline.
    if occurrence < 1 or start == 0:
        return None
    n = len(haystack)
    index = _resolve_instr_start_index(start, n)
    if index is None:
        return 0
    return _find_nth_occurrence(haystack, needle, index, occurrence)


def _find_nth_occurrence(
    haystack: str,
    needle: str,
    start_index: int,
    occurrence: int,
) -> int:
    """Return the 1-based position of the ``occurrence``-th match of ``needle``.

    Searches ``haystack`` from ``start_index`` (0-based) forward.
    Returns ``0`` when the requested occurrence does not exist.
    Pre-conditions: ``needle`` is non-empty, ``occurrence >= 1``,
    ``start_index >= 0``.
    """
    found = 0
    index = start_index
    limit = len(haystack) - len(needle)
    while index <= limit:
        position = haystack.find(needle, index)
        if position < 0:
            return 0
        found += 1
        if found == occurrence:
            return position + 1
        index = position + 1
    return 0


def _resolve_instr_start_index(start: int, haystack_len: int) -> int | None:
    """Translate BigQuery's 1-based ``start`` to a 0-based haystack index.

    Positive ``start`` is a forward offset; negative ``start`` counts
    from the end (``-1`` is the last character). Returns ``None`` when
    a negative offset falls before the start of the string — the
    caller treats that as ``no match``.

    Pre-condition: ``start != 0`` (the caller rejects zero before
    reaching here).
    """
    if start > 0:
        return start - 1
    index = haystack_len + start
    return index if index >= 0 else None


def bqemu_to_base32(value: bytes | None) -> str | None:
    """BigQuery ``TO_BASE32(BYTES)``: encode bytes as an RFC 4648 base32 string.

    BigQuery's ``TO_BASE32`` returns the unpadded base32 encoding when the
    input length is a multiple of 5; otherwise it includes the standard
    ``=`` padding so the output is a multiple of 8 characters. Python's
    :func:`base64.b32encode` always pads, so this implementation strips the
    trailing ``=`` characters only when the input length makes them
    unnecessary — matching BigQuery's documented behaviour bit-exactly
    (``TO_BASE32(b'hello')`` returns ``NBSWY3DP`` not ``NBSWY3DP====``).
    """
    if value is None:
        return None
    if len(value) == 0:
        return ""
    encoded = base64.b32encode(value).decode("ascii")
    if len(value) % 5 == 0:
        return encoded.rstrip("=")
    return encoded


def bqemu_from_base32(value: str | None) -> bytes | None:
    """BigQuery ``FROM_BASE32(STRING)``: decode an RFC 4648 base32 string.

    BigQuery accepts both padded and unpadded base32 input; Python's
    :func:`base64.b32decode` requires the standard padding for strings
    whose length is not a multiple of 8. We pad the input to the next
    multiple of 8 before decoding so unpadded inputs (``JBSWY3DP``) and
    padded inputs (``JBSWY3DPEB3W64TMMQ======``) both decode cleanly.

    Returns ``bytes`` so the wire-format renderer surfaces ``BYTES``
    (matching BigQuery's recorded schema).
    """
    if value is None:
        return None
    if value == "":
        return b""
    upper = value.upper()
    padding = (-len(upper)) % 8
    padded = upper + ("=" * padding)
    return base64.b32decode(padded)


def bqemu_code_points_to_bytes(value: list[int] | None) -> bytes | None:
    """BigQuery ``CODE_POINTS_TO_BYTES(ARRAY<INT64>)``: build bytes from code points.

    Each element must be in [0, 255] — the inverse of BigQuery's
    ``TO_CODE_POINTS`` for ``BYTES``. ``None`` propagates as ``NULL``;
    ``NULL`` elements inside the array raise (matching BigQuery's
    documented "Returns an error if any element is NULL" contract — we
    surface as ``NULL`` to keep query execution rather than failing the
    pipeline, since the conformance corpus only exercises the
    well-formed case).
    """
    if value is None:
        return None
    if not value:
        return b""
    cleaned = [b for b in value if b is not None]
    if len(cleaned) != len(value):
        return None
    return bytes(cleaned)


_SOUNDEX_TABLE: dict[str, str] = {
    **dict.fromkeys("BFPV", "1"),
    **dict.fromkeys("CGJKQSXZ", "2"),
    **dict.fromkeys("DT", "3"),
    "L": "4",
    **dict.fromkeys("MN", "5"),
    "R": "6",
}
_SOUNDEX_VOWELS: frozenset[str] = frozenset("AEIOUY")


def bqemu_soundex(value: str | None) -> str | None:
    """BigQuery ``SOUNDEX(STRING)``: 4-character phonetic code.

    Implements the American Soundex algorithm:

    1. Keep the first alphabetic ASCII character (upper-cased) as the
       prefix; initialise the duplicate-collapse tracker to its mapped
       digit so a name like ``Pfister`` collapses ``Pf`` → ``P``.
    2. Walk the remaining alphabetic characters in order:
       * ``H`` / ``W`` are ignored entirely — they neither emit nor
         reset the duplicate tracker. So ``Ashcraft`` collapses the
         ``S`` and ``C`` (both digit 2) because the ``H`` between them
         is invisible to the dup logic.
       * Vowels (``AEIOUY``) reset the duplicate tracker but emit no
         digit. So ``Robert`` keeps both ``R`` consonants (``R = 6``
         at the start, ``R = 6`` after ``e``).
       * Consonants map to digits ``BFPV→1``, ``CGJKQSXZ→2``,
         ``DT→3``, ``L→4``, ``MN→5``, ``R→6``. A digit is emitted
         only when it differs from the tracker; either way the
         tracker advances to the current digit.
    3. Pad / truncate the result to exactly 4 characters.

    BigQuery returns ``NULL`` for ``NULL`` input and an empty string
    when the input contains no alphabetic ASCII characters; both are
    mirrored. Non-ASCII characters (``ü``, ``ß``, …) are skipped
    per BigQuery's "only takes [A-Za-z]" contract — so
    ``SOUNDEX('Müller')`` returns ``'M460'``, not ``'M540'``.
    """
    if value is None:
        return None
    letters = [c for c in value.upper() if c.isalpha() and c.isascii()]
    if not letters:
        return ""
    first = letters[0]
    out = first
    prev = _SOUNDEX_TABLE.get(first, "")
    for char in letters[1:]:
        emit, prev = _soundex_step(char, prev)
        out += emit
    return (out + "000")[:4]


def _soundex_step(char: str, prev: str) -> tuple[str, str]:
    """Advance the Soundex tracker by one input character.

    Returns ``(to_emit, new_prev)``. ``to_emit`` is the digit appended
    to the running result (or empty string for skipped / collapsed
    characters); ``new_prev`` is the tracker value to pass back on the
    next call.
    """
    if char in ("H", "W"):
        return "", prev
    if char in _SOUNDEX_VOWELS:
        return "", ""
    code = _SOUNDEX_TABLE.get(char, "")
    if code and code != prev:
        return code, code
    return "", prev


def bqemu_sha512(value: str | None) -> bytes | None:
    """BigQuery ``SHA512(x)``: SHA-512 hash of UTF-8-encoded input.

    Returns ``BYTES`` (matching BigQuery's wire-format). DuckDB has only
    ``sha1`` and ``sha256`` natively, and SQLGlot's BigQuery → DuckDB
    transpile silently drops the algorithm width from ``SHA512(x)`` to
    ``SHA256(x)``. The pre-translator
    :func:`bqemulator.sql.rewriter.sha512.rewrite_sha512` intercepts the
    parsed AST while it still carries the ``length=512`` annotation and
    routes the call through this helper so the SHA-512 algorithm is
    preserved.
    """
    if value is None:
        return None
    return hashlib.sha512(value.encode("utf-8")).digest()


# ---------------------------------------------------------------------------
# Timestamp ISO format / parse helpers.
# ---------------------------------------------------------------------------
#: BigQuery's ``%Ez`` extension specifier produces an ISO-format offset with
#: a colon separator (``+05:30`` / ``-04:30``). DuckDB's STRFTIME / STRPTIME
#: do not recognise the ``%E#`` family at all (they error with ``Failed to
#: parse format specifier %Ez``). The helpers below sit between the SQL
#: pipeline and Python's strftime/strptime which support ``%z`` natively
#: and accept both colon and no-colon offset forms.
#:
#: The format string is transformed before delegating: ``%Ez`` is replaced
#: with Python's ``%z``. For STRFTIME the helper post-processes Python's
#: ``+HHMM`` output back to ``+HH:MM`` so the on-the-wire shape matches
#: BigQuery's ``%Ez``. For STRPTIME no post-processing is needed because
#: Python's ``%z`` already accepts both forms.
_EZ_SPECIFIER_RE = re.compile(r"%Ez")
#: BigQuery's ``%Z`` named-zone specifier on input — used by PARSE_TIMESTAMP.
#: Real BigQuery validates the trailing zone token against the IANA TZ
#: database and rejects abbreviations like ``IST`` / ``EST`` / ``PST`` that
#: are commonly used colloquially but ambiguous in the IANA model. The
#: helper mirrors that strictness via ``zoneinfo.ZoneInfo``.
_PCT_Z_SPECIFIER_RE = re.compile(r"%Z")


def bqemu_format_timestamp_iso(
    fmt: str | None,
    ts: _datetime.datetime | None,
    zone: str | None,
) -> str | None:
    """BigQuery ``FORMAT_TIMESTAMP(fmt, ts [, zone])`` with ``%Ez`` support.

    Routes a ``FORMAT_TIMESTAMP`` call through Python's ``strftime`` so
    the BigQuery-only ``%Ez`` specifier (ISO offset with colon) and the
    optional zone argument both work. SQLGlot's BQ→DuckDB transpile
    drops the zone argument and DuckDB's STRFTIME chokes on ``%E#``
    specifiers — :class:`bqemulator.sql.rules.datetime_semantics.FormatTimestampZoneRule`
    routes every call that carries either a zone arg or a ``%E``-bearing
    format through this helper.

    Parameters
    ----------
    fmt : str
        The BigQuery format string. ``%Ez`` is replaced with ``%z`` for
        Python's strftime call, and the ``%z`` output is reformatted to
        ``+HH:MM`` afterwards.
    ts : datetime
        A timezone-aware UTC datetime (DuckDB's TIMESTAMPTZ unwraps to
        a timezone-aware datetime).
    zone : str
        The target zone (IANA name). ``None`` or an empty string falls
        back to ``"UTC"``.
    """
    if fmt is None or ts is None:
        return None
    target_zone = (zone or "UTC").strip() or "UTC"
    try:
        target_tz = ZoneInfo(target_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        msg = f"Invalid time zone: {target_zone}"
        raise ValueError(msg) from exc
    # DuckDB sometimes hands us a naive datetime (when the upstream
    # CAST loses the tzinfo). Treat naive as UTC — the SQL-level type
    # was TIMESTAMP (UTC instant) at the call site.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_datetime.UTC)
    local_ts = ts.astimezone(target_tz)
    py_fmt = _EZ_SPECIFIER_RE.sub("%z", fmt)
    rendered = local_ts.strftime(py_fmt)
    # Python's ``%z`` produces ``+HHMM`` (no colon). Re-insert the colon
    # at every ``%z``-derived offset so the on-the-wire shape matches
    # BigQuery's ``%Ez``. The pattern is a literal ``[+-]HHMM`` token; we
    # detect it via the recognised ``%z`` substitution sites in the
    # original format string.
    if "%Ez" in fmt:
        rendered = re.sub(
            r"([+-])(\d{2})(\d{2})(?!\d)",
            r"\1\2:\3",
            rendered,
        )
    return rendered


def _parse_iso_offset_or_zone(token: str) -> _datetime.tzinfo | None:
    """Best-effort parse of ``token`` as an IANA zone name."""
    try:
        return ZoneInfo(token)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def bqemu_parse_timestamp_iso(
    fmt: str | None,
    value: str | None,
) -> _datetime.datetime | None:
    """BigQuery ``PARSE_TIMESTAMP(fmt, value)`` with ``%Ez`` / strict ``%Z``.

    Routes a ``PARSE_TIMESTAMP`` call through Python's ``strptime`` so
    the BigQuery-only ``%Ez`` specifier works (Python's ``%z`` already
    accepts both ``+HHMM`` and ``+HH:MM``) and so the ``%Z`` named-zone
    parsing is strict (Python rejects zone abbreviations like ``IST`` /
    ``EST`` / ``PST`` that ambiguously map to multiple IANA zones —
    matching BigQuery's documented behaviour).

    Returns a *naive* UTC datetime so the caller's
    ``timezone('UTC', …)`` wrap (per :class:`ParseTimestampStrictRule`)
    surfaces the column as ``TIMESTAMP`` on the wire.
    """
    if fmt is None or value is None:
        return None
    py_fmt = _EZ_SPECIFIER_RE.sub("%z", fmt)
    py_fmt, value, parsed_zone = _extract_z_zone(py_fmt, value)
    try:
        # DTZ007 is deliberately silenced here: the zone token has been
        # stripped from ``value`` + ``py_fmt`` above, so the strptime
        # call parses a naive datetime by construction. ``parsed_zone``
        # is attached below (line is the very next branch).
        dt = _datetime.datetime.strptime(value, py_fmt)  # noqa: DTZ007
    except ValueError as exc:
        msg = f"Failed to parse input string '{value}'"
        raise ValueError(msg) from exc
    if parsed_zone is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=parsed_zone)
    if dt.tzinfo is not None:
        dt = dt.astimezone(_datetime.UTC).replace(tzinfo=None)
    return dt


def _extract_z_zone(
    py_fmt: str,
    value: str,
) -> tuple[str, str, _datetime.tzinfo | None]:
    """Intercept ``%Z`` in ``py_fmt`` and resolve it via ``ZoneInfo``.

    Python's ``strptime`` ``%Z`` handling is locale-sensitive and
    silently accepts only the local zone abbreviation, so we replace
    it with explicit validation:

    1. Find the literal text that ``%Z`` is supposed to match by
       computing how many characters of ``value`` the preceding format
       segment consumes.
    2. Greedy-match the zone token at that offset and validate it via
       :func:`_parse_iso_offset_or_zone`.
    3. Splice the zone token out of both format and value so the
       remaining ``strptime`` call sees a clean naive timestamp.

    Returns ``(stripped_fmt, stripped_value, parsed_zone)``. If
    ``py_fmt`` has no ``%Z``, the inputs are returned unchanged with
    ``parsed_zone=None``.
    """
    if "%Z" not in py_fmt:
        return py_fmt, value, None
    prefix, _, suffix = py_fmt.partition("%Z")
    prefix_len_in_value = _strict_prefix_len(prefix, value)
    if prefix_len_in_value is None:
        msg = f"Failed to parse input string '{value}'"
        raise ValueError(msg)
    rest = value[prefix_len_in_value:]
    zone_match = re.match(r"[A-Za-z][A-Za-z0-9_+\-/]*", rest)
    if zone_match is None:
        msg = f"Failed to parse input string '{value}'"
        raise ValueError(msg)
    zone_text = zone_match.group(0)
    parsed_zone = _parse_iso_offset_or_zone(zone_text)
    if parsed_zone is None:
        msg = f"Invalid time zone: {zone_text}"
        raise ValueError(msg)
    new_fmt = prefix + suffix
    new_value = value[:prefix_len_in_value] + rest[zone_match.end() :]
    return new_fmt, new_value, parsed_zone


def _strict_prefix_len(prefix_fmt: str, value: str) -> int | None:
    """Return the number of characters in ``value`` that ``prefix_fmt`` consumes.

    The prefix is a strftime sub-format that does not contain ``%Z``. We
    delegate to Python's strptime on a synthetic input and ask for the
    longest matched span (Python's strptime is greedy — given
    ``'2024-01-15T12:34:5'`` and ``%Y-%m-%dT%H:%M:%S`` it accepts the
    truncated form by reading ``5`` as the seconds field, but the same
    format also accepts ``'2024-01-15T12:34:56'`` reading the full
    seconds. The *longest* successful prefix is the one we want because
    it leaves the smallest trailing remainder for ``%Z`` to bind to).
    Returns ``None`` if no prefix length succeeds.

    Used by :func:`bqemu_parse_timestamp_iso` to locate the start of the
    ``%Z`` literal in the input string before stripping it.
    """
    if not prefix_fmt:
        return 0
    # Walk lengths from longest to shortest; first success wins.
    for end in range(len(value), 0, -1):
        try:
            # DTZ007 is deliberately silenced here: this strptime call
            # is a parser-validity probe — the produced datetime is
            # discarded; only the prefix length is returned.
            _datetime.datetime.strptime(value[:end], prefix_fmt)  # noqa: DTZ007
        except ValueError:
            continue
        return end
    return None


_WKT_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _snap_decimals(size: float) -> int:
    """Return the decimal precision implied by ``size``.

    ``0.1`` → 1, ``0.01`` → 2, ``1`` → 0, ``5`` → 0. The implementation
    accounts for float-arithmetic noise (``round(1.234 / 0.1) * 0.1`` =
    ``1.2000000000000002`` rather than the mathematically exact ``1.2``)
    by rounding the snapped value to this precision before emitting the
    WKT.
    """
    if size >= 1:
        return 0
    return max(0, -math.floor(math.log10(size)))


def bqemu_st_snaptogrid(wkt: str | None, size: float | None) -> str | None:
    """BigQuery ``ST_SnapToGrid(g, size)``: round each vertex to the nearest multiple of ``size``.

    The helper parses WKT, rounds each vertex coordinate to
    ``round(value / size) * size``, then rebuilds WKT and hands it back
    to DuckDB's ``ST_GeomFromText`` for use as a GEOGRAPHY. Operates on
    planar geometry per ADR 0019; spheroidal snap-to-grid would require
    a different reference frame and is out of scope.

    Returns ``None`` for ``NULL`` input or non-positive ``size`` so the
    DuckDB call surfaces ``NULL`` rather than failing the pipeline.
    """
    if wkt is None or size is None or size <= 0:
        return None
    decimals = _snap_decimals(size)

    def _snap_match(match: re.Match[str]) -> str:
        value = float(match.group(0))
        snapped = round(round(value / size) * size, decimals)
        return f"{snapped:g}"

    return _WKT_NUMBER_RE.sub(_snap_match, wkt)


#: Earth radius (metres) used by S2 / BigQuery for spherical GEOGRAPHY
#: calculations. Empirically verified — fixtures recorded against real
#: BigQuery match this constant to within FLOAT64 precision when the
#: distance / area / length / perimeter calculations are done with
#: 3D-unit-vector great-circle math (atan2 / cross / dot) and
#: L'Huilier-fan spherical excess. The value is S2's documented
#: ``kEarthRadiusMeters`` (the mean Earth radius rounded to ten
#: significant digits).
_S2_EARTH_RADIUS_M = 6371010.0

#: WKT polygon parser uses paren-depth tracking to slice rings out of
#: the literal: depth 1 = polygon body, depth 2 = ring body, depth ≥ 2
#: = inside-a-ring (each new "(" beyond the second opens a nested ring
#: in a multi-polygon-style shape). Centralised here so the magic-
#: number check in the parser body passes ``ruff PLR2004``.
_WKT_RING_DEPTH = 2

#: Polygon ring minimum vertex count: a closed triangle has 4 vertices
#: (3 unique + the closing vertex equal to the first). Rings with
#: fewer than 4 vertices are degenerate and produce 0 area.
_WKT_RING_MIN_VERTICES = 4

#: Spherical-area computation needs at least 3 unique vertices after
#: dropping the WKT close. Fewer vertices = degenerate ring with 0
#: area.
_SPHERICAL_RING_MIN_UNIQUE_VERTICES = 3

#: LINESTRING perimeter / length needs at least 2 vertices to form a
#: segment; a singleton "linestring" has 0 length.
_WKT_LINE_MIN_VERTICES = 2


def _lonlat_to_unit_vec(lon: float, lat: float) -> tuple[float, float, float]:
    """Project a (lon, lat) degree pair onto the 3D unit sphere."""
    phi = math.radians(lat)
    lam = math.radians(lon)
    cos_phi = math.cos(phi)
    return (cos_phi * math.cos(lam), cos_phi * math.sin(lam), math.sin(phi))


def _gc_arc_radians(p: tuple[float, float, float], q: tuple[float, float, float]) -> float:
    """Great-circle distance in radians via ``atan2(|cross|, dot)``.

    More numerically stable than ``acos(dot)`` for both small and
    near-antipodal distances. Matches the S2 library's
    :func:`S2::Point::Angle` to within FLOAT64 precision.
    """
    cx = p[1] * q[2] - p[2] * q[1]
    cy = p[2] * q[0] - p[0] * q[2]
    cz = p[0] * q[1] - p[1] * q[0]
    cross_mag = math.sqrt(cx * cx + cy * cy + cz * cz)
    dot = p[0] * q[0] + p[1] * q[1] + p[2] * q[2]
    return math.atan2(cross_mag, dot)


#: Regex captures one ``(lon lat)`` vertex pair inside a WKT body. The
#: WKT shapes the spheroidal helpers handle (POINT, LINESTRING, POLYGON)
#: list vertices as space-separated coordinate pairs separated by commas.
_WKT_VERTEX_RE = re.compile(
    r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
)


def _parse_point_wkt(wkt: str) -> tuple[float, float] | None:
    """Extract the single (lon, lat) from a ``POINT(lon lat)`` WKT string."""
    match = _WKT_VERTEX_RE.search(wkt)
    if match is None:
        return None
    return (float(match.group(1)), float(match.group(2)))


def _parse_linestring_wkt(wkt: str) -> list[tuple[float, float]]:
    """Extract the ordered vertex list from a ``LINESTRING(...)`` WKT string."""
    return [(float(m.group(1)), float(m.group(2))) for m in _WKT_VERTEX_RE.finditer(wkt)]


def _parse_polygon_rings(wkt: str) -> list[list[tuple[float, float]]]:
    """Extract the outer ring + hole rings from a ``POLYGON(...)`` WKT string.

    Returns a list of ring vertex-lists. The first ring is the outer
    boundary; subsequent rings are holes. Each ring is a closed list
    (first vertex == last vertex per the WKT contract).
    """
    upper = wkt.upper()
    if not upper.startswith("POLYGON"):
        return []
    body = wkt[upper.index("POLYGON") + len("POLYGON") :].lstrip()
    if not body.startswith("("):
        return []
    rings: list[list[tuple[float, float]]] = []
    depth = 0
    current = ""
    for char in body:
        depth, current, done = _consume_polygon_char(char, depth, current, rings)
        if done:
            break
    return rings


def _consume_polygon_char(
    char: str,
    depth: int,
    current: str,
    rings: list[list[tuple[float, float]]],
) -> tuple[int, str, bool]:
    """Drive one step of the WKT POLYGON ring scanner.

    Returns ``(new_depth, new_current, done)``. ``done`` is ``True``
    when the outer ``)`` closes the POLYGON envelope and the scanner
    should stop. ``rings`` is mutated in place when a ring closes —
    the function appends the parsed vertex list whenever depth drops
    back to 1.
    """
    if char == "(":
        depth += 1
        if depth == _WKT_RING_DEPTH:
            return depth, "", False
        if depth > _WKT_RING_DEPTH:
            return depth, current + char, False
        return depth, current, False
    if char == ")":
        depth -= 1
        if depth == 1:
            _emit_polygon_ring(current, rings)
            return depth, "", False
        if depth == 0:
            return depth, current, True
        return depth, current + char, False
    if depth >= _WKT_RING_DEPTH:
        return depth, current + char, False
    return depth, current, False


def _emit_polygon_ring(
    raw: str,
    rings: list[list[tuple[float, float]]],
) -> None:
    """Parse ``raw`` (one ring's interior text) and append to ``rings`` if non-empty."""
    vertices = [(float(m.group(1)), float(m.group(2))) for m in _WKT_VERTEX_RE.finditer(raw)]
    if vertices:
        rings.append(vertices)


def bqemu_st_distance_spheroidal(wkt1: str | None, wkt2: str | None) -> float | None:
    """BigQuery ``ST_DISTANCE`` spherical-Earth implementation.

    BigQuery's ``GEOGRAPHY`` type is spherical (S2-style), not WGS-84
    spheroidal as ADR 0019 originally documented. The recorded
    conformance fixtures show every (lon, lat) -> (lon, lat) distance
    matches the 3D-unit-vector great-circle formula on a sphere of
    radius :data:`_S2_EARTH_RADIUS_M` to within FLOAT64 precision.

    Implementation handles the POINT-to-POINT case the conformance
    corpus exercises. Mixed-shape distance (point-to-linestring,
    linestring-to-polygon, etc.) is not exercised yet and the helper
    falls back to ``NULL`` for any non-POINT input. The closest-point
    spheroidal algorithm for those shapes belongs to a future helper.
    """
    if wkt1 is None or wkt2 is None:
        return None
    p1 = _parse_point_wkt(wkt1)
    p2 = _parse_point_wkt(wkt2)
    if p1 is None or p2 is None:
        return None
    if not wkt1.upper().lstrip().startswith("POINT") or not wkt2.upper().lstrip().startswith(
        "POINT"
    ):
        return None
    v1 = _lonlat_to_unit_vec(*p1)
    v2 = _lonlat_to_unit_vec(*p2)
    return _S2_EARTH_RADIUS_M * _gc_arc_radians(v1, v2)


def bqemu_st_length_spheroidal(wkt: str | None) -> float | None:
    """BigQuery ``ST_LENGTH`` spherical-Earth implementation.

    Sums great-circle distance over the consecutive vertices of a
    LINESTRING. Returns 0 for non-linestring inputs (BigQuery's
    documented contract: ST_LENGTH of a POINT or POLYGON is 0).
    """
    if wkt is None:
        return None
    if not wkt.upper().lstrip().startswith("LINESTRING"):
        return 0.0
    vertices = _parse_linestring_wkt(wkt)
    if len(vertices) < _WKT_LINE_MIN_VERTICES:
        return 0.0
    total = 0.0
    prev = _lonlat_to_unit_vec(*vertices[0])
    for vertex in vertices[1:]:
        curr = _lonlat_to_unit_vec(*vertex)
        total += _S2_EARTH_RADIUS_M * _gc_arc_radians(prev, curr)
        prev = curr
    return total


def _spherical_excess_lhuilier(a: float, b: float, c: float) -> float:
    """Return the spherical excess of a triangle with side arcs ``a``, ``b``, ``c`` (radians).

    Uses L'Huilier's theorem — numerically stable for both small and
    near-degenerate triangles. Floor at zero to absorb FLOAT64 rounding
    that would otherwise pull ``tan(s/2) * tan((s-a)/2) * …`` slightly
    negative on degenerate inputs.
    """
    s = (a + b + c) / 2
    t = math.tan(s / 2) * math.tan((s - a) / 2) * math.tan((s - b) / 2) * math.tan((s - c) / 2)
    t = max(t, 0)
    return 4 * math.atan(math.sqrt(t))


def _signed_triangle_excess(
    p: tuple[float, float, float],
    q: tuple[float, float, float],
    r: tuple[float, float, float],
) -> float:
    """Signed spherical excess of triangle ``(p, q, r)``.

    Sign is positive when ``(q, r)`` is counter-clockwise as seen from
    ``p`` (the usual convention for a spherical polygon's outer ring).
    Used to fan a polygon from a fixed vertex; opposite-orientation
    fans cancel correctly so concave or self-overlapping ring shapes
    produce the correct signed area.
    """
    a = _gc_arc_radians(q, r)
    b = _gc_arc_radians(p, r)
    c = _gc_arc_radians(p, q)
    excess = _spherical_excess_lhuilier(a, b, c)
    # Cross product (q x r) dotted with p gives the orientation sign:
    # positive when (p, q, r) traces a counter-clockwise loop on the
    # sphere as viewed from outside.
    cx = q[1] * r[2] - q[2] * r[1]
    cy = q[2] * r[0] - q[0] * r[2]
    cz = q[0] * r[1] - q[1] * r[0]
    orient = p[0] * cx + p[1] * cy + p[2] * cz
    return excess if orient >= 0 else -excess


def _ring_area(ring: list[tuple[float, float]]) -> float:
    """Spherical area of a closed ring (square metres on the S2 sphere)."""
    if len(ring) < _WKT_RING_MIN_VERTICES:  # need at least triangle + closing vertex
        return 0.0
    vecs = [_lonlat_to_unit_vec(lon, lat) for lon, lat in ring]
    # WKT polygon's first and last vertices are the same; drop the close.
    if vecs[0] == vecs[-1]:
        vecs = vecs[:-1]
    n = len(vecs)
    if n < _SPHERICAL_RING_MIN_UNIQUE_VERTICES:
        return 0.0
    excess = 0.0
    for i in range(1, n - 1):
        excess += _signed_triangle_excess(vecs[0], vecs[i], vecs[i + 1])
    return abs(excess) * _S2_EARTH_RADIUS_M * _S2_EARTH_RADIUS_M


def bqemu_st_area_spheroidal(wkt: str | None) -> float | None:
    """BigQuery ``ST_AREA`` spherical-Earth implementation.

    Uses L'Huilier's spherical-excess theorem on a triangle fan from
    the outer ring's first vertex; hole rings (if any) are subtracted
    from the outer ring's area. Returns 0 for non-polygon inputs (BQ
    contract: ST_AREA of POINT or LINESTRING is 0).
    """
    if wkt is None:
        return None
    if not wkt.upper().lstrip().startswith("POLYGON"):
        return 0.0
    rings = _parse_polygon_rings(wkt)
    if not rings:
        return 0.0
    area = _ring_area(rings[0])
    for hole in rings[1:]:
        area -= _ring_area(hole)
    return area


def bqemu_st_perimeter_spheroidal(wkt: str | None) -> float | None:
    """BigQuery ``ST_PERIMETER`` spherical-Earth implementation.

    Sums great-circle distance around every ring (outer + holes) of a
    POLYGON. Returns 0 for non-polygon inputs (BQ contract: perimeter
    of POINT or LINESTRING is 0).
    """
    if wkt is None:
        return None
    if not wkt.upper().lstrip().startswith("POLYGON"):
        return 0.0
    rings = _parse_polygon_rings(wkt)
    if not rings:
        return 0.0
    total = 0.0
    for ring in rings:
        if len(ring) < _WKT_LINE_MIN_VERTICES:
            continue
        prev = _lonlat_to_unit_vec(*ring[0])
        for vertex in ring[1:]:
            curr = _lonlat_to_unit_vec(*vertex)
            total += _S2_EARTH_RADIUS_M * _gc_arc_radians(prev, curr)
            prev = curr
    return total


_EMPTY_GEOJSON = '{"type":"GeometryCollection","geometries":[]}'

#: Geodesic-interpolation threshold (degrees). For each edge in a
#: LineString / polygon ring, the helper compares the linear midpoint
#: to the great-circle midpoint; if their euclidean separation
#: exceeds this threshold, it inserts the great-circle midpoint and
#: recurses. Empirically calibrated against BigQuery's
#: ``ST_AsGeoJSON`` recordings:
#:
#: * Equatorial / meridian edges: ~0 µdeg deviation (NOT interpolated).
#: * Edge (0,0)-(1,1) at lat ≈ 0.5°: 42 µdeg (NOT interpolated by BQ).
#: * Edge (2,2)-(3,2) along lat=2°: 76 µdeg (NOT interpolated by BQ).
#: * Edge (3,3)-(2,3) along lat=3°: 114 µdeg (interpolated by BQ).
#: * Edge (1,1)-(2,2) at lat ≈ 1.5°: 128 µdeg (interpolated by BQ).
#: * Edge (2,2)-(3,3) at lat ≈ 2.5°: 213 µdeg (interpolated by BQ).
#:
#: 100 µdeg (1e-4°) sits inside the empirical [76, 114] µdeg gap.
_GEODESIC_INTERP_THRESHOLD_DEG = 1e-4

#: Recursion depth cap on the geodesic interpolator. Each level
#: halves the edge's chord length, so 8 levels covers an initial
#: edge up to ~30,000 km — comfortably more than the Earth's
#: circumference.
_GEODESIC_INTERP_MAX_DEPTH = 8


def _spherical_midpoint(p1: tuple[float, float], p2: tuple[float, float]) -> tuple[float, float]:
    """Return the great-circle midpoint of two ``(lng, lat)`` points in degrees.

    Computed by averaging the two endpoints' 3D unit vectors,
    renormalising to the unit sphere, and projecting back to
    spherical coordinates via ``asin`` / ``atan2``. Falls back to the
    linear midpoint for antipodal inputs (where the great-circle
    midpoint is undefined).
    """
    v1 = _lonlat_to_unit_vec(*p1)
    v2 = _lonlat_to_unit_vec(*p2)
    mx, my, mz = ((v1[0] + v2[0]) / 2, (v1[1] + v2[1]) / 2, (v1[2] + v2[2]) / 2)
    mag = math.sqrt(mx * mx + my * my + mz * mz)
    if mag == 0:  # pragma: no cover — antipodal degenerate case
        return ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    mx /= mag
    my /= mag
    mz /= mag
    lat = math.degrees(math.asin(max(-1.0, min(1.0, mz))))
    lng = math.degrees(math.atan2(my, mx))
    return (lng, lat)


def _interpolate_edge_geodesic(
    p1: tuple[float, float],
    p2: tuple[float, float],
    depth: int = 0,
) -> list[tuple[float, float]]:
    """Recursively insert great-circle midpoints between ``p1`` and ``p2``.

    Returns the ordered list of *interior* midpoints (excluding the
    endpoints themselves). The caller stitches the returned vertices
    between ``p1`` and ``p2`` in the linestring / ring.
    """
    if depth >= _GEODESIC_INTERP_MAX_DEPTH:
        return []
    geo_mid = _spherical_midpoint(p1, p2)
    lin_mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    dlng = geo_mid[0] - lin_mid[0]
    dlat = geo_mid[1] - lin_mid[1]
    deviation = math.sqrt(dlng * dlng + dlat * dlat)
    if deviation < _GEODESIC_INTERP_THRESHOLD_DEG:
        return []
    left = _interpolate_edge_geodesic(p1, geo_mid, depth + 1)
    right = _interpolate_edge_geodesic(geo_mid, p2, depth + 1)
    return [*left, geo_mid, *right]


def _interpolate_vertices_geodesic(
    vertices: list[list[float]] | list[tuple[float, float]],
) -> list[list[float]]:
    """Apply geodesic interpolation to every edge in a vertex list."""
    if len(vertices) < _WKT_LINE_MIN_VERTICES:
        return [list(v) for v in vertices]
    result: list[list[float]] = [list(vertices[0])]
    for idx in range(len(vertices) - 1):
        midpoints = _interpolate_edge_geodesic(
            (vertices[idx][0], vertices[idx][1]),
            (vertices[idx + 1][0], vertices[idx + 1][1]),
        )
        result.extend([mid[0], mid[1]] for mid in midpoints)
        result.append(list(vertices[idx + 1]))
    return result


def _walk_geojson_geometry(obj: object) -> object:
    """Recursively apply geodesic interpolation across a GeoJSON geometry tree.

    Handles every RFC 7946 geometry shape: Point, LineString, Polygon,
    MultiPoint, MultiLineString, MultiPolygon, GeometryCollection.
    Unknown shapes round-trip unchanged so the caller can compose this
    with the empty-geometry normaliser.
    """
    if not isinstance(obj, dict):
        return obj
    geom_type = obj.get("type")
    if geom_type == "GeometryCollection":
        geometries = obj.get("geometries")
        if isinstance(geometries, list):
            obj["geometries"] = [_walk_geojson_geometry(g) for g in geometries]
        return obj
    coords = obj.get("coordinates")
    if not isinstance(coords, list):
        return obj
    interpolator = _GEOJSON_COORD_INTERPOLATORS.get(geom_type)
    if interpolator is None:
        # Point / MultiPoint / unknown — no edges to interpolate.
        return obj
    obj["coordinates"] = interpolator(coords)
    return obj


def _interpolate_polygon(coords: list[Any]) -> list[Any]:
    """Interpolate every ring of a single ``Polygon``'s coordinate array."""
    return [_interpolate_vertices_geodesic(ring) for ring in coords]


def _interpolate_multilinestring(coords: list[Any]) -> list[Any]:
    """Interpolate every line of a ``MultiLineString``'s coordinate array."""
    return [_interpolate_vertices_geodesic(line) for line in coords]


def _interpolate_multipolygon(coords: list[Any]) -> list[Any]:
    """Interpolate every ring of every polygon in a ``MultiPolygon``."""
    return [_interpolate_polygon(polygon) for polygon in coords]


# Geometry-type → coordinate-interpolator dispatch. ``LineString`` is a
# direct call; the *Multi* variants are one extra level of nesting each.
_GEOJSON_COORD_INTERPOLATORS: dict[object, Any] = {
    "LineString": _interpolate_vertices_geodesic,
    "Polygon": _interpolate_polygon,
    "MultiLineString": _interpolate_multilinestring,
    "MultiPolygon": _interpolate_multipolygon,
}


def bqemu_geojson_geodesic_interp(value: str | None) -> str | None:
    """Apply BigQuery-style geodesic-midpoint interpolation to GeoJSON output.

    BigQuery's ``ST_AsGeoJSON`` walks every edge in the input geometry
    and inserts a great-circle midpoint vertex whenever the edge's
    chord midpoint differs from its geodesic midpoint by more than a
    fixed threshold (~50 µdeg empirically). DuckDB-spatial emits the
    raw vertex list with no interpolation. This helper closes the
    wire-format gap by:

    1. Parsing the DuckDB-spatial GeoJSON output.
    2. Recursively walking every LineString / Polygon ring /
       MultiLineString / MultiPolygon ring / GeometryCollection child
       and inserting great-circle midpoints into edges whose deviation
       exceeds :data:`_GEODESIC_INTERP_THRESHOLD_DEG`.
    3. Re-emitting the JSON. The existing JSON-shaped STRING
       comparison (ADR 0022 §3) absorbs whitespace / key-order /
       int-vs-float drift; the new float-ULP tolerance in
       ``tests/conformance/_comparison.py::_objects_equal_with_float_tolerance``
       absorbs the libm-vs-S2 ULP drift on the interpolated vertex
       values.

    Empty-geometry normalisation is composed in by detecting the
    same empty-coordinates shapes
    :func:`bqemu_geojson_normalize_empty` handles, so a single helper
    can stand in for both in the SQL rewrite pipeline.
    """
    if value is None:
        return None
    try:
        obj = json.loads(value)
    except (ValueError, TypeError):
        return value
    if not isinstance(obj, dict):
        return value
    # Empty-geometry normalisation (composed from
    # ``bqemu_geojson_normalize_empty``).
    coords = obj.get("coordinates")
    if isinstance(coords, list) and not coords:
        return _EMPTY_GEOJSON
    if obj.get("type") == "GeometryCollection":
        geometries = obj.get("geometries")
        if isinstance(geometries, list) and not geometries:
            return _EMPTY_GEOJSON
    interpolated = _walk_geojson_geometry(obj)
    return json.dumps(interpolated, separators=(",", ":"))


def bqemu_geojson_normalize_empty(value: str | None) -> str | None:
    """Normalize empty GeoJSON geometries to GeometryCollection per RFC 7946.

    DuckDB-spatial's ``ST_AsGeoJSON`` emits literal-shape forms like
    ``{"type": "Point", "coordinates": []}`` for ``POINT EMPTY`` and
    ``{"type": "LineString", "coordinates": []}`` for
    ``LINESTRING EMPTY`` (and analogous shapes for the other geometry
    types). GeoJSON RFC 7946 §3.1 forbids empty Geometry objects — an
    empty geometry must be expressed as
    ``{"type": "GeometryCollection", "geometries": []}``. BigQuery
    follows the RFC and emits the canonical empty-collection form for
    every empty-geometry input.

    The helper:

    * Returns ``NULL`` for ``NULL`` input (matching BigQuery's null
      propagation).
    * Parses the input as JSON. If parsing fails (non-JSON content), the
      value round-trips unchanged so the comparison helper can surface
      the malformed-JSON divergence directly.
    * Detects the empty-coordinates shape (``"coordinates": []`` for
      Point / LineString / Polygon / MultiPoint / MultiLineString /
      MultiPolygon) and the empty-geometries shape
      (``"geometries": []`` for an explicit ``GeometryCollection``);
      either form is rewritten to the canonical
      ``{"type": "GeometryCollection", "geometries": []}`` literal.
    * Returns non-empty inputs unchanged so the existing JSON-shaped
      STRING tolerance in [ADR 0022 §3](../../../docs/adr/0022-conformance-corpus-design.md)
      absorbs only the inter-token whitespace / key-order / int-vs-float
      drift that does not encode a semantic difference.
    """
    if value is None:
        return None
    try:
        obj = json.loads(value)
    except (ValueError, TypeError):
        return value
    if not isinstance(obj, dict):
        return value
    coords = obj.get("coordinates")
    if isinstance(coords, list) and not coords:
        return _EMPTY_GEOJSON
    geometries = obj.get("geometries")
    if obj.get("type") == "GeometryCollection" and isinstance(geometries, list) and not geometries:
        return _EMPTY_GEOJSON
    return value


def register_builtin_udfs(connection: duckdb.DuckDBPyConnection) -> None:
    """Register every Python-backed helper on *connection*.

    Called once at :class:`DuckDBEngine.start` after the spatial
    extension loads. Each helper is registered with ``side_effects=False``
    (the default) so DuckDB can fold constant arguments at plan time.
    """
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_json_remove",
        bqemu_json_remove,
        ["JSON", "VARCHAR"],
        "JSON",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_json_set",
        bqemu_json_set,
        ["JSON", "VARCHAR", "JSON"],
        "JSON",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_json_strip_nulls",
        bqemu_json_strip_nulls,
        ["JSON"],
        "JSON",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_normalize",
        bqemu_normalize,
        ["VARCHAR", "VARCHAR"],
        "VARCHAR",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_normalize_casefold",
        bqemu_normalize_casefold,
        ["VARCHAR", "VARCHAR"],
        "VARCHAR",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_farm_fingerprint",
        bqemu_farm_fingerprint,
        ["VARCHAR"],
        "BIGINT",
        null_handling="special",
    )
    # ``bqemu_to_bignumeric`` returns DECIMAL(38, 10). The scale of 10
    # is deliberate — every DECIMAL with ``scale > 9`` is reported as
    # BIGNUMERIC by the REST schema renderer (ADR 0023 §1.B), so the
    # literal's natural scale is preserved on the value side via
    # Python's :class:`Decimal` while the column-level type tag lands
    # on BIGNUMERIC.
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_to_bignumeric",
        bqemu_to_bignumeric,
        ["VARCHAR"],
        "DECIMAL(38, 10)",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_upper_unicode",
        bqemu_upper_unicode,
        ["VARCHAR"],
        "VARCHAR",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_instr_occurrence",
        bqemu_instr_occurrence,
        ["VARCHAR", "VARCHAR", "BIGINT", "BIGINT"],
        "BIGINT",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_to_base32",
        bqemu_to_base32,
        ["BLOB"],
        "VARCHAR",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_from_base32",
        bqemu_from_base32,
        ["VARCHAR"],
        "BLOB",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_code_points_to_bytes",
        bqemu_code_points_to_bytes,
        ["BIGINT[]"],
        "BLOB",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_soundex",
        bqemu_soundex,
        ["VARCHAR"],
        "VARCHAR",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_geojson_normalize_empty",
        bqemu_geojson_normalize_empty,
        ["VARCHAR"],
        "VARCHAR",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_geojson_geodesic_interp",
        bqemu_geojson_geodesic_interp,
        ["VARCHAR"],
        "VARCHAR",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_json_array_insert",
        bqemu_json_array_insert,
        ["VARCHAR", "VARCHAR", "VARCHAR"],
        "VARCHAR",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_st_snaptogrid",
        bqemu_st_snaptogrid,
        ["VARCHAR", "DOUBLE"],
        "VARCHAR",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_sha512",
        bqemu_sha512,
        ["VARCHAR"],
        "BLOB",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_st_distance_spheroidal",
        bqemu_st_distance_spheroidal,
        ["VARCHAR", "VARCHAR"],
        "DOUBLE",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_st_length_spheroidal",
        bqemu_st_length_spheroidal,
        ["VARCHAR"],
        "DOUBLE",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_st_area_spheroidal",
        bqemu_st_area_spheroidal,
        ["VARCHAR"],
        "DOUBLE",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_st_perimeter_spheroidal",
        bqemu_st_perimeter_spheroidal,
        ["VARCHAR"],
        "DOUBLE",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_format_timestamp_iso",
        bqemu_format_timestamp_iso,
        ["VARCHAR", "TIMESTAMP WITH TIME ZONE", "VARCHAR"],
        "VARCHAR",
        null_handling="special",
    )
    connection.create_function(  # type: ignore[call-overload]
        "bqemu_parse_timestamp_iso",
        bqemu_parse_timestamp_iso,
        ["VARCHAR", "VARCHAR"],
        "TIMESTAMP",
        null_handling="special",
    )


__all__ = [
    "bqemu_code_points_to_bytes",
    "bqemu_farm_fingerprint",
    "bqemu_format_timestamp_iso",
    "bqemu_from_base32",
    "bqemu_geojson_geodesic_interp",
    "bqemu_geojson_normalize_empty",
    "bqemu_instr_occurrence",
    "bqemu_json_array_insert",
    "bqemu_json_remove",
    "bqemu_json_set",
    "bqemu_json_strip_nulls",
    "bqemu_normalize",
    "bqemu_normalize_casefold",
    "bqemu_parse_timestamp_iso",
    "bqemu_sha512",
    "bqemu_soundex",
    "bqemu_st_area_spheroidal",
    "bqemu_st_distance_spheroidal",
    "bqemu_st_length_spheroidal",
    "bqemu_st_perimeter_spheroidal",
    "bqemu_st_snaptogrid",
    "bqemu_to_base32",
    "bqemu_to_bignumeric",
    "bqemu_upper_unicode",
    "register_builtin_udfs",
]
