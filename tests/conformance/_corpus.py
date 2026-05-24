"""Corpus discovery, placeholder substitution, and per-fixture REST/header bundles.

The conformance corpus lives at ``tests/conformance/sql_corpus/`` and
contains one directory per fixture. The shape is locked by ADR 0022
and ADR 0018:

    tests/conformance/sql_corpus/<phase>/<fixture_name>/
        query.sql         # canonical input SQL
        setup.sql         # optional fixture seed (idempotent DDL/DML)
        setup_rest.json   # optional ordered REST operations
        headers.json      # optional per-fixture HTTP headers
        parameters.json   # optional query parameters
        expected.json     # recorded baseline from real BigQuery

The runner and recorder both call :func:`discover_fixtures` to walk the
corpus and :func:`substitute_placeholders` to expand the supported
placeholders. ``${DATASET}`` is the legacy placeholder kept for back-
compat (resolves to ``project.dataset_id``); ``${PROJECT}`` and
``${DATASET_ID}`` are split forms used by ``setup_rest.json`` URL
paths and bodies. ``${PRINCIPAL}`` / ``${GROUP}`` are used by
``setup_rest.json`` grantee lists and ``headers.json`` caller
identities — they decouple the recorder's real ADC identity from the
deterministic emulator-side identity the runner uses. Any other
``${…}`` token raises so a typo (e.g. ``${dataset}`` lowercase) fails
loudly instead of silently leaking through.

``parameters.json`` carries a ``QueryParameters`` payload — both the
recorder and the runner submit the fixture's ``query.sql`` through
``QueryJobConfig.query_parameters`` so the wire-format
``queryParameters`` field on the REST body is exercised end-to-end.
See :mod:`tests.conformance._parameters` for the conversion from the
on-disk JSON shape to the BQ client's ``QueryParameter`` objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
import re

# Repository-relative path: ``tests/conformance/sql_corpus``. The
# runner and recorder both resolve fixture paths via this constant.
CORPUS_DIR = Path(__file__).parent / "sql_corpus"

# Fixture sub-directories per phase. Order is stable so parametrised
# tests have predictable IDs.
PHASE_SUBDIRS = (
    "rest_crud",
    "partitioning_clustering",
    "routines_scripting",
    "versioning",
    "row_access",
    "specialized_types",
    "standard_functions",
    # API request configuration variations — same SQL repeated with
    # different ``QueryJobConfig`` knobs flipped. Fixtures here are
    # paired with ``job_config.json`` and exercise the configuration
    # matrix catalogued in
    # ``docs/reference/api-configuration-coverage-matrix.md``.
    "api_configuration",
    # INFORMATION_SCHEMA virtual views — SCHEMATA / TABLES / COLUMNS /
    # TABLE_OPTIONS / VIEWS / PARTITIONS. Fixtures land here in pairs
    # of three per view; recording flow documented at
    # ``information_schema/_g4_recording_steps.md``.
    "information_schema",
)

PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

#: Default identity values used by the runner when the operator-side
#: env vars (``BQEMU_CONFORMANCE_PRINCIPAL`` / ``BQEMU_CONFORMANCE_GROUP`` /
#: ``BQEMU_CONFORMANCE_OTHER_PRINCIPAL``) are unset. These match no real
#: Google identity, so the recorder MUST override them — but for the
#: in-process emulator they are perfectly usable: the rewriter only
#: compares strings.
DEFAULT_RUNNER_PRINCIPAL = "user:alice@example.com"
DEFAULT_RUNNER_GROUP = "group:engineering@example.com"
DEFAULT_RUNNER_OTHER_PRINCIPAL = "serviceAccount:other@example.com"

#: Names of the placeholders the substituter accepts. ``DATASET`` is
#: the legacy ``project.dataset_id`` form; ``PROJECT`` + ``DATASET_ID``
#: are split forms for REST URL components. ``PRINCIPAL`` / ``GROUP`` /
#: ``OTHER_PRINCIPAL`` are caller-identity placeholders.
#: ``OTHER_PRINCIPAL`` carries an IAM-member that real BigQuery accepts
#: as a grantee but that is NOT the recording caller — used by the
#: "deny everyone except <caller>" fixtures so BigQuery's grantee
#: validation (which rejects ``user:nobody@example.com``) is satisfied.
#: ``GCS_BUCKET`` is the placeholder for load/extract fixtures that
#: reference a pre-staged ``gs://`` URI; the recorder reads it from the
#: ``BQEMU_CONFORMANCE_GCS_BUCKET`` env var.
_SUPPORTED_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "DATASET",
        "PROJECT",
        "DATASET_ID",
        "PRINCIPAL",
        "GROUP",
        "OTHER_PRINCIPAL",
        "GCS_BUCKET",
    },
)

#: Placeholder default — the runner uses this when an HTTP fixture
#: references ``${GCS_BUCKET}`` but the recorder hasn't supplied one.
#: For the in-process emulator runner it's harmless because the load
#: executor will fail to read the non-existent file, but the runner
#: still substitutes a syntactically valid token so the request path
#: stays parseable.
DEFAULT_RUNNER_GCS_BUCKET = "bqemu-conformance-no-bucket-set"


@dataclass(slots=True, frozen=True)
class Fixture:
    """One conformance fixture on disk."""

    phase: str
    name: str
    path: Path
    query_sql: str
    setup_sql: str | None
    expected_path: Path
    setup_rest: tuple[dict[str, object], ...] = ()
    headers: tuple[tuple[str, str], ...] = ()
    parameters: dict[str, object] | None = None
    job_config: dict[str, object] | None = None

    @property
    def needs_dataset(self) -> bool:
        """True when the fixture has a setup.sql or setup_rest.json.

        Literal-only fixtures (``SELECT 1`` and friends) do not require
        a temp dataset on either the emulator or real BigQuery; this
        flag lets the runner short-circuit dataset creation for them.
        Fixtures with REST-only setup (e.g. RAP creation against an
        existing table) also need a dataset because the REST call
        path templates with ``${DATASET_ID}``. Parameter-only fixtures
        do NOT need a dataset — their queries are typically literals
        with parameter substitutions (e.g. ``SELECT @n AS n``).
        """
        return self.setup_sql is not None or bool(self.setup_rest)

    @property
    def id(self) -> str:
        """The parametrize-friendly identifier, e.g. ``rest_crud/select_int64_literal``."""
        return f"{self.phase}/{self.name}"


class VariationTag(StrEnum):
    """One of seven locked-set tags describing what kind of variation a fixture exercises.

    See [ADR 0022](../../docs/adr/0022-conformance-corpus-design.md)
    §"Variation taxonomy" for the locked seven-tag set and the per-tag
    detection contract. The set is intentionally frozen — when in doubt,
    fall back to ``HAPPY_PATH`` and let depth-of-coverage do the work.
    The taxonomy lets the
    [conformance coverage matrix](../../docs/reference/conformance-coverage-matrix.md)
    surface "broad but shallow" surfaces — those with many fixtures
    that all sit in the happy path and reliably miss the typical
    BQ-vs-DuckDB divergence (NULL propagation, empty inputs,
    ±Inf / NaN, Unicode case-folding, error-shape parity, timezone
    arithmetic).
    """

    HAPPY_PATH = "happy_path"
    NULL_INPUT = "null_input"
    EMPTY_INPUT = "empty_input"
    BOUNDARY_VALUE = "boundary_value"
    UNICODE = "unicode"
    ERROR_PATH = "error_path"
    TIMEZONE = "timezone"


# Regex that detects NULL semantics in query text. Matches both
# predicates (``IS [NOT] NULL``) and bare ``NULL`` projections /
# arguments. Case-insensitive via :func:`re.IGNORECASE`.
_NULL_DETECT_RE = re.compile(r"\bIS\s+NULL\b|\bIS\s+NOT\s+NULL\b|\bNULL\b", re.IGNORECASE)

# Regex that detects empty-input markers in query text. Catches
# ``LIMIT 0`` clauses, empty array literals (``[]`` or ``[  ]``), and
# empty string literals (``''``). Empty single-quote pairs cannot be
# part of a longer BigQuery string literal (BQ uses backslash escapes
# rather than doubled quotes), so the ``''`` match is reliable.
_EMPTY_DETECT_RE = re.compile(r"\bLIMIT\s+0\b|\[\s*\]|''", re.IGNORECASE)

# Regex that detects "obviously extreme" literals in query text:
# - integer literals with 15 or more digits (INT64 max is 19 digits,
#   so this comfortably catches ``9223372036854775807`` and friends
#   without false-positiving on typical timestamps or row counts);
# - quoted ``'inf'`` / ``'infinity'`` / ``'nan'`` (with optional sign)
#   used as the string form of FLOAT64 ±Inf / NaN literals.
_BOUNDARY_LITERAL_RE = re.compile(
    r"\b\d{15,}\b|'[+-]?inf(?:inity)?'|'[+-]?nan'",
    re.IGNORECASE,
)

# Boundary-value name keywords matched as snake-case tokens (between
# underscores). Token-match avoids substring false positives like
# ``information`` -> ``inf`` or ``unterminated`` -> ``min``. The
# ``bound_*`` family of fixtures hits these via the ``max``/``min``/
# ``inf``/``nan`` suffix; pure ``IS_INF``/``IS_NAN`` queries hit via
# the ``_BOUNDARY_LITERAL_RE`` regex on the embedded ``'Infinity'`` /
# ``'NaN'`` cast literal.
_BOUNDARY_NAME_TOKENS = frozenset({"max", "min", "inf", "nan"})

# Boundary-value name substrings — long enough to be unambiguous, so a
# whole-name substring match is safe.
_BOUNDARY_NAME_SUBSTRINGS = ("boundary", "overflow")

# Regex that detects timezone-arithmetic markers in query text:
# - The ``AT TIME ZONE`` operator (case-insensitive, any whitespace).
# - A literal IANA-format zone name like ``'America/New_York'`` or
#   ``'Etc/UTC'`` (Area/Location with the area first-capital). The
#   pattern intentionally requires the leading single quote so a
#   docstring like ``description: America/New_York`` does not match.
# - A literal numeric offset ``'+HH:MM'`` / ``'-HH:MM'`` used as the
#   second argument to ``DATETIME(ts, ...)`` / ``TIMESTAMP(dt, ...)``
#   / ``FORMAT_TIMESTAMP(..., ts, ...)`` / ``%Ez``-style parsing.
# - ``TIMESTAMP_TRUNC(..., DAY, 'zone')`` / ``TIMESTAMP_TRUNC(..., ..., '+HH:MM')``
#   trailing arg form — detected by the named zone / offset literal.
# - ``Etc/UTC`` is a deliberate match even though it is operationally
#   identical to UTC — the variation-depth report counts fixtures that
#   *exercise* the named-zone surface, not fixtures whose semantics
#   differ from a no-zone baseline.
_TIMEZONE_DETECT_RE = re.compile(
    r"\bAT\s+TIME\s+ZONE\b"
    r"|'[A-Z][a-zA-Z]+/[A-Za-z_+-]+'"
    r"|'[+-]\d{2}:\d{2}'",
    re.IGNORECASE,
)


def _has_error_envelope(expected_path: Path) -> bool:
    """Return ``True`` if ``expected.json``'s top-level object has an ``error`` key.

    The error-shape contract (ADR 0022 §3 ``Error parity``) records
    every recorded BigQuery error as
    ``{"error": {"reason": "...", "http_status": ..., ...}}`` at the
    top level of ``expected.json``. The classifier scans only the
    top-level keys — full error-shape validation lives in
    :mod:`tests.conformance._comparison`. Returns ``False`` for any
    parse error or missing file so a malformed fixture can't crash
    the matrix generator's variation pass.
    """
    if not expected_path.is_file():
        return False
    try:
        payload = json.loads(expected_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and "error" in payload


def _name_tokens(name: str) -> set[str]:
    """Split a snake-case fixture name into its component tokens.

    The classifier uses this to do exact-token matching for boundary
    keywords (``max``, ``min``, ``inf``, ``nan``) so a fixture name
    like ``information_schema_visibility`` is not falsely tagged as
    boundary on the basis of the embedded ``inf`` substring.
    """
    return set(name.lower().split("_"))


def classify_variation(fixture: Fixture) -> frozenset[VariationTag]:
    """Return the variation tags exercised by ``fixture``.

    Tags are derived purely from on-disk fixture content — the fixture
    name, the ``query.sql`` text, and the top-level keys of
    ``expected.json``. No DuckDB, no parser, no network. See
    :class:`VariationTag` and
    [ADR 0022](../../docs/adr/0022-conformance-corpus-design.md)
    §"Variation taxonomy" for the locked six-tag set and the per-tag
    detection contract.

    A fixture can carry multiple tags simultaneously (e.g. a fixture
    testing ``UPPER`` on a Unicode-typed NULL gets
    ``{NULL_INPUT, UNICODE}``). ``HAPPY_PATH`` is mutually exclusive
    with every other tag: it fires only when *no* other tag matches,
    so every fixture is classified into at least one bucket.
    """
    name_lower = fixture.name.lower()
    name_tokens = _name_tokens(fixture.name)
    sql = fixture.query_sql

    tags: set[VariationTag] = set()

    if _has_error_envelope(fixture.expected_path):
        tags.add(VariationTag.ERROR_PATH)

    if "null" in name_lower or _NULL_DETECT_RE.search(sql):
        tags.add(VariationTag.NULL_INPUT)

    if "empty" in name_lower or _EMPTY_DETECT_RE.search(sql):
        tags.add(VariationTag.EMPTY_INPUT)

    if (
        name_tokens & _BOUNDARY_NAME_TOKENS
        or any(sub in name_lower for sub in _BOUNDARY_NAME_SUBSTRINGS)
        or _BOUNDARY_LITERAL_RE.search(sql)
    ):
        tags.add(VariationTag.BOUNDARY_VALUE)

    if "unicode" in name_lower or any(ord(c) > 127 for c in sql):
        tags.add(VariationTag.UNICODE)

    if name_lower.startswith("tz_") or _TIMEZONE_DETECT_RE.search(sql):
        tags.add(VariationTag.TIMEZONE)

    if not tags:
        return frozenset({VariationTag.HAPPY_PATH})
    return frozenset(tags)


def discover_fixtures(
    corpus_dir: Path | None = None, *, include_unrecorded: bool = False
) -> list[Fixture]:
    """Walk the corpus and return every fixture directory.

    A fixture directory is recognised by the presence of ``query.sql``.
    The corresponding ``setup.sql``, ``setup_rest.json``, ``headers.json``,
    and ``parameters.json`` are loaded if present. By default fixtures
    without an ``expected.json`` are excluded so the runner only attempts
    to compare against recorded baselines; pass ``include_unrecorded=True``
    to surface them (used by the recorder).
    """
    root = corpus_dir or CORPUS_DIR
    fixtures: list[Fixture] = []
    for phase in PHASE_SUBDIRS:
        phase_dir = root / phase
        if not phase_dir.is_dir():
            continue
        for entry in sorted(phase_dir.iterdir()):
            if not entry.is_dir():
                continue
            query_path = entry / "query.sql"
            if not query_path.is_file():
                continue
            expected_path = entry / "expected.json"
            if not include_unrecorded and not expected_path.is_file():
                continue
            setup_path = entry / "setup.sql"
            setup_sql = setup_path.read_text(encoding="utf-8") if setup_path.is_file() else None
            setup_rest = _load_setup_rest(entry / "setup_rest.json")
            headers = _load_headers(entry / "headers.json")
            parameters = _load_parameters(entry / "parameters.json")
            job_config = _load_job_config(entry / "job_config.json")
            fixtures.append(
                Fixture(
                    phase=phase,
                    name=entry.name,
                    path=entry,
                    query_sql=query_path.read_text(encoding="utf-8"),
                    setup_sql=setup_sql,
                    expected_path=expected_path,
                    setup_rest=setup_rest,
                    headers=headers,
                    parameters=parameters,
                    job_config=job_config,
                )
            )
    return fixtures


def _load_setup_rest(path: Path) -> tuple[dict[str, object], ...]:
    """Read ``setup_rest.json`` as a tuple of REST-operation dicts.

    The expected shape is a top-level list. Each entry must carry
    ``method``, ``path``, and (optionally) ``body``. Validation here is
    intentionally minimal — the recorder/runner layers re-validate
    when issuing the call. An unparseable file raises so a typo
    surfaces at discovery time, not deep inside a recording run.
    """
    if not path.is_file():
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path}: invalid JSON ({exc})"
        raise ValueError(msg) from exc
    if not isinstance(data, list):
        msg = f"{path}: setup_rest.json must be a top-level list"
        raise TypeError(msg)
    out: list[dict[str, object]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            msg = f"{path}: entry #{idx} must be an object"
            raise TypeError(msg)
        if "method" not in item or "path" not in item:
            msg = f"{path}: entry #{idx} requires 'method' and 'path'"
            raise ValueError(msg)
        out.append(item)
    return tuple(out)


def _load_headers(path: Path) -> tuple[tuple[str, str], ...]:
    """Read ``headers.json`` as an ordered tuple of (name, value) pairs."""
    if not path.is_file():
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path}: invalid JSON ({exc})"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{path}: headers.json must be a top-level object"
        raise TypeError(msg)
    out: list[tuple[str, str]] = []
    for name, value in data.items():
        if not isinstance(name, str) or not isinstance(value, str):
            msg = f"{path}: header entries must be string→string"
            raise TypeError(msg)
        out.append((name, value))
    return tuple(out)


_VALID_PARAMETER_MODES = frozenset({"named", "positional"})


def _load_parameters(path: Path) -> dict[str, object] | None:
    """Read ``parameters.json`` as a typed ``QueryParameters`` payload.

    Returns ``None`` when the file is absent so callers can short-
    circuit. The on-disk shape (see ``sql_corpus/README.md``) is a
    top-level object with two keys:

    * ``mode`` — ``"named"`` (``@n``) or ``"positional"`` (``?``).
    * ``parameters`` — a list of parameter entries. Each entry carries
      ``type`` (either a scalar type name like ``"INT64"`` or a
      compound dict like ``{"type": "ARRAY", "arrayType": {…}}``) and
      ``value`` (a JSON-native value or ``null``). Named entries also
      carry ``name``.

    Validation here is minimal — the conversion to BQ
    ``QueryParameter`` objects in :mod:`tests.conformance._parameters`
    re-validates the type / value shape when the runner / recorder
    builds the actual job config. An unparseable file raises so a typo
    fails loudly at discovery time, not deep inside a recording run.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path}: invalid JSON ({exc})"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{path}: parameters.json must be a top-level object"
        raise TypeError(msg)
    mode = data.get("mode")
    if mode not in _VALID_PARAMETER_MODES:
        msg = f"{path}: 'mode' must be one of {sorted(_VALID_PARAMETER_MODES)} (got {mode!r})"
        raise ValueError(msg)
    parameters = data.get("parameters")
    if not isinstance(parameters, list):
        msg = f"{path}: 'parameters' must be a list"
        raise TypeError(msg)
    for idx, entry in enumerate(parameters):
        if not isinstance(entry, dict):
            msg = f"{path}: entry #{idx} must be an object"
            raise TypeError(msg)
        if "type" not in entry:
            msg = f"{path}: entry #{idx} requires 'type'"
            raise ValueError(msg)
        if mode == "named" and not isinstance(entry.get("name"), str):
            msg = f"{path}: entry #{idx} requires a string 'name' in named mode"
            raise ValueError(msg)
    return data


def _load_job_config(path: Path) -> dict[str, object] | None:
    """Read ``job_config.json`` as a typed ``QueryJobConfig`` payload.

    Returns ``None`` when the file is absent so callers can short-
    circuit (the recorder/runner submit the SQL with a default
    ``QueryJobConfig``). The on-disk shape is documented in
    :mod:`tests.conformance._job_config` and
    [`api-configuration-coverage-matrix`](../../docs/reference/api-configuration-coverage-matrix.md).

    Validation here is intentionally light — the conversion to a
    BigQuery ``QueryJobConfig`` in
    :func:`tests.conformance._job_config.build_job_config` re-
    validates each value's shape when the runner / recorder builds
    the actual job config. An unparseable file raises so a typo
    fails loudly at discovery time, not deep inside a recording run.

    The function only enforces that the file parses as a JSON object
    (the BQ-specific key validation lives in the converter so it
    stays in sync with the converter's supported-keys set).
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path}: invalid JSON ({exc})"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{path}: job_config.json must be a top-level object"
        raise TypeError(msg)
    return data


@dataclass(slots=True, frozen=True)
class PlaceholderContext:
    """Values substituted for the supported ``${…}`` placeholders.

    The recorder and the runner build this context per-fixture:

    * ``dataset`` — fully-qualified ``project.dataset_id`` form used by
      the legacy ``${DATASET}`` placeholder (kept for back-compat).
    * ``project`` / ``dataset_id`` — the split forms used by REST URL
      templates.
    * ``principal`` — IAM-member string the recorder/runner should
      grant in policies and use as the caller. The recorder uses its
      real ADC identity here (so BigQuery's RAP enforcement actually
      sees the grant); the runner uses a deterministic placeholder.
    * ``group`` — optional IAM ``group:`` member used by the group-
      grantee fixtures.
    """

    dataset: str
    principal: str = DEFAULT_RUNNER_PRINCIPAL
    group: str = DEFAULT_RUNNER_GROUP
    other_principal: str = DEFAULT_RUNNER_OTHER_PRINCIPAL
    gcs_bucket: str = DEFAULT_RUNNER_GCS_BUCKET

    @property
    def project(self) -> str:
        """Return the ``project`` half of ``project.dataset_id``."""
        if "." not in self.dataset:
            return self.dataset
        return self.dataset.split(".", 1)[0]

    @property
    def dataset_id(self) -> str:
        """Return the ``dataset_id`` half of ``project.dataset_id``."""
        if "." not in self.dataset:
            return ""
        return self.dataset.split(".", 1)[1]


def substitute_placeholders(text: str, ctx: PlaceholderContext) -> str:
    """Expand every supported ``${…}`` placeholder in ``text``.

    See :data:`_SUPPORTED_PLACEHOLDERS` for the accepted token names.
    Any other token raises :class:`ValueError` so a typo (e.g.
    ``${dataset}`` lowercase) is surfaced at runtime rather than
    silently leaking through.
    """
    mapping = {
        "DATASET": ctx.dataset,
        "PROJECT": ctx.project,
        "DATASET_ID": ctx.dataset_id,
        "PRINCIPAL": ctx.principal,
        "GROUP": ctx.group,
        "OTHER_PRINCIPAL": ctx.other_principal,
        "GCS_BUCKET": ctx.gcs_bucket,
    }

    def _replace(match: re.Match[str]) -> str:
        placeholder_name = match.group(1)
        if placeholder_name not in _SUPPORTED_PLACEHOLDERS:
            msg = (
                f"Unknown placeholder: ${{{placeholder_name}}} "
                f"(supported: {sorted(_SUPPORTED_PLACEHOLDERS)})"
            )
            raise ValueError(msg)
        return mapping[placeholder_name]

    return PLACEHOLDER_PATTERN.sub(_replace, text)


def substitute_dataset(sql: str, dataset: str) -> str:
    """Legacy two-arg shim around :func:`substitute_placeholders`.

    Kept so existing call-sites (and any third-party tools that
    consume the recorder's helpers) keep working unchanged. New code
    should construct a :class:`PlaceholderContext` and call
    :func:`substitute_placeholders` directly so the richer placeholder
    set is available.
    """
    return substitute_placeholders(sql, PlaceholderContext(dataset=dataset))


def substitute_in_json(value: object, ctx: PlaceholderContext) -> object:
    """Recursively substitute placeholders in a JSON-shaped value.

    Strings are passed through :func:`substitute_placeholders`; lists
    and dicts are walked element-wise. Numbers, booleans, and ``None``
    are returned unchanged. Used by the recorder/runner to expand
    ``setup_rest.json`` bodies before issuing the call.
    """
    if isinstance(value, str):
        return substitute_placeholders(value, ctx)
    if isinstance(value, list):
        return [substitute_in_json(item, ctx) for item in value]
    if isinstance(value, dict):
        return {key: substitute_in_json(val, ctx) for key, val in value.items()}
    return value


def split_statements(script: str) -> list[str]:
    """Split a multi-statement SQL script on ``;`` boundaries.

    Strips empty trailing statements and handles ``;`` inside
    single-line ``--`` comments (which BigQuery's parser also
    tolerates). Block comments (``/* … */``) are NOT supported in the
    corpus; the fixture authoring guide rejects them.
    """
    out: list[str] = []
    buf: list[str] = []
    for raw_line in script.splitlines():
        stripped = raw_line.split("--", 1)[0].rstrip()
        if not stripped:
            buf.append(raw_line)
            continue
        if stripped.endswith(";"):
            buf.append(raw_line[: raw_line.index(";")])
            statement = "\n".join(buf).strip()
            if statement:
                out.append(statement)
            buf = []
        else:
            buf.append(raw_line)
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return out


__all__ = [
    "CORPUS_DIR",
    "DEFAULT_RUNNER_GCS_BUCKET",
    "DEFAULT_RUNNER_GROUP",
    "DEFAULT_RUNNER_OTHER_PRINCIPAL",
    "DEFAULT_RUNNER_PRINCIPAL",
    "PHASE_SUBDIRS",
    "Fixture",
    "PlaceholderContext",
    "VariationTag",
    "classify_variation",
    "discover_fixtures",
    "split_statements",
    "substitute_dataset",
    "substitute_in_json",
    "substitute_placeholders",
]

# ``_load_parameters`` is intentionally NOT exported — the runner and
# recorder consume parameters via ``Fixture.parameters`` after discovery
# and never need to call the loader directly. The unit-test module
# imports it explicitly via the module attribute for contract pinning.
