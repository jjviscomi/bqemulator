"""Command-line entry points.

``bqemulator`` is the console script installed by ``pyproject.toml``. All
subcommands dispatch through the :func:`main` group.

Subcommands:

* ``start``  — run the emulator server (REST + gRPC).
* ``import`` — mirror schemas from a real BigQuery project into the local
  catalog (requires the ``import`` extra).
* ``export`` — export emulator state as portable seed files.
* ``seed``   — load seed data into the emulator.
* ``backup``  — snapshot the persistent DuckDB catalog to a directory.
* ``restore`` — restore a backup directory into a ``data_dir``.
* ``version`` — print version and exit.

The implementations of import / export / seed / backup / restore live
under :mod:`bqemulator.commands` and are deferred-imported by the
relevant subcommand so ``bqemulator --version`` and ``bqemulator start``
cold-start without paying for them.
"""

from __future__ import annotations

from pathlib import Path

import click

from bqemulator import __version__
from bqemulator.config import LogFormat, LogLevel, PersistenceMode, Settings


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "--version", "-V", package_name="bqemulator")
def main() -> None:
    """Local emulator for Google BigQuery."""


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--rest-host",
    default=None,
    envvar="BQEMU_REST_HOST",
    help="REST bind host (default: 127.0.0.1).",
)
@click.option(
    "--rest-port",
    type=int,
    default=None,
    envvar="BQEMU_REST_PORT",
    help="REST bind port (default: 9050; 0 for random free port).",
)
@click.option(
    "--grpc-host",
    default=None,
    envvar="BQEMU_GRPC_HOST",
    help="gRPC bind host (default: 127.0.0.1).",
)
@click.option(
    "--grpc-port",
    type=int,
    default=None,
    envvar="BQEMU_GRPC_PORT",
    help="gRPC bind port (default: 9060; 0 for random free port).",
)
@click.option(
    "--ephemeral/--persistent",
    default=None,
    help="In-memory (ephemeral) or file-backed (persistent). Default: ephemeral.",
)
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    envvar="BQEMU_DATA_DIR",
    help="Directory for persistent DuckDB file and working state.",
)
@click.option(
    "--project",
    "default_project_id",
    default=None,
    envvar="BQEMU_DEFAULT_PROJECT_ID",
    help="Default project id when a request omits it.",
)
@click.option(
    "--log-level",
    type=click.Choice([level.value for level in LogLevel], case_sensitive=False),
    default=None,
    envvar="BQEMU_LOG_LEVEL",
)
@click.option(
    "--log-format",
    type=click.Choice([fmt.value for fmt in LogFormat], case_sensitive=False),
    default=None,
    envvar="BQEMU_LOG_FORMAT",
)
@click.option(
    "--enable-admin/--no-admin",
    "admin_enabled",
    default=None,
    envvar="BQEMU_ADMIN_ENABLED",
    help="Enable /admin HTTP endpoints.",
)
def start(
    rest_host: str | None,
    rest_port: int | None,
    grpc_host: str | None,
    grpc_port: int | None,
    ephemeral: bool | None,
    data_dir: Path | None,
    default_project_id: str | None,
    log_level: str | None,
    log_format: str | None,
    admin_enabled: bool | None,
) -> None:
    """Start the emulator (REST + gRPC)."""
    # This command is inherently complex because it is a transparent
    # pass-through from CLI flags to the ``Settings`` constructor. Every
    # ``if flag is not None`` branch is required to preserve env-var /
    # ``.bqemu.toml`` precedence (CLI > env > file > defaults). Extracting
    # to a loop would not meaningfully reduce the branch count.
    overrides: dict[str, object] = {}
    if rest_host is not None:
        overrides["rest_host"] = rest_host
    if rest_port is not None:
        overrides["rest_port"] = rest_port
    if grpc_host is not None:
        overrides["grpc_host"] = grpc_host
    if grpc_port is not None:
        overrides["grpc_port"] = grpc_port
    if ephemeral is True:
        overrides["persistence_mode"] = PersistenceMode.EPHEMERAL
    elif ephemeral is False:
        overrides["persistence_mode"] = PersistenceMode.PERSISTENT
    if data_dir is not None:
        overrides["data_dir"] = data_dir
        # If a data_dir was given without explicit --persistent, infer persistent.
        overrides.setdefault("persistence_mode", PersistenceMode.PERSISTENT)
    if default_project_id is not None:
        overrides["default_project_id"] = default_project_id
    if log_level is not None:
        overrides["log_level"] = LogLevel(log_level.lower())
    if log_format is not None:
        overrides["log_format"] = LogFormat(log_format.lower())
    if admin_enabled is not None:
        overrides["admin_enabled"] = admin_enabled

    # Env-var + .bqemu.toml resolution still happens; the overrides here
    # take highest priority (CLI flags). mypy cannot resolve **kwargs
    # against pydantic-settings' BaseSettings, hence the ignore.
    settings = Settings(**overrides)  # type: ignore[arg-type]

    # Deferred import so `bqemulator --version` / `--help` stays fast.
    from bqemulator.server import run_forever

    run_forever(settings)


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@main.command()
def version() -> None:
    """Print version and exit."""
    click.echo(f"bqemulator {__version__}")


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


@main.command(name="import")
@click.option("--from-project", "from_project", required=True, help="Source project id.")
@click.option(
    "--dataset",
    "datasets",
    multiple=True,
    help="Specific dataset(s) to import. Repeat for multiple.",
)
@click.option(
    "--target-project",
    "target_project",
    default=None,
    help="Project id to use in the local catalog. Defaults to --from-project.",
)
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    envvar="BQEMU_DATA_DIR",
)
def import_cmd(
    from_project: str,
    datasets: tuple[str, ...],
    target_project: str | None,
    data_dir: Path,
) -> None:
    """Mirror schemas from a real BigQuery project into the local catalog."""
    # Deferred import: the implementation depends on the optional
    # ``import`` extra (``google-cloud-bigquery``). If the extra is
    # missing — or if Phase 10 modules are not yet installed — surface a
    # clean instruction instead of an opaque ImportError. We catch
    # ImportError both around the module import AND around the call
    # itself, because ``run_import`` defers the
    # ``from google.cloud import bigquery`` line until invocation to
    # keep the CLI's cold-start fast.
    try:
        from bqemulator.commands.import_project import run_import
    except ImportError as exc:
        raise click.ClickException(
            "bqemulator[import] extra is required. Install with: pip install 'bqemulator[import]'",
        ) from exc
    try:
        summary = run_import(
            source_project=from_project,
            dataset_filters=list(datasets) or None,
            data_dir=data_dir,
            target_project=target_project,
        )
    except ImportError as exc:
        raise click.ClickException(
            "bqemulator[import] extra is required. Install with: pip install 'bqemulator[import]'",
        ) from exc
    click.echo(
        f"Imported {summary.datasets} datasets, {summary.tables} tables, "
        f"{summary.routines} routines from {from_project} into {data_dir}.",
    )


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@main.command(name="export")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    envvar="BQEMU_DATA_DIR",
    help="Persistent data_dir holding bqemulator.duckdb.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Destination directory for the export. Must be empty or absent.",
)
def export_cmd(data_dir: Path, output_dir: Path) -> None:
    """Export the local catalog and row data as portable seed files."""
    from bqemulator.commands.export import run_export

    try:
        summary = run_export(data_dir=data_dir, output_dir=output_dir)
    except (FileNotFoundError, FileExistsError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Exported {summary.datasets} datasets, {summary.tables} tables, "
        f"{summary.routines} routines ({summary.rows_written} rows) to {output_dir}.",
    )


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


@main.command(name="seed")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    envvar="BQEMU_DATA_DIR",
)
@click.option(
    "--input-dir",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    required=True,
    help="Directory previously produced by 'bqemulator export'.",
)
def seed_cmd(data_dir: Path, input_dir: Path) -> None:
    """Load an export directory back into a local persistent catalog."""
    from bqemulator.commands.seed import run_seed

    try:
        summary = run_seed(data_dir=data_dir, input_dir=input_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Seeded {summary.datasets} datasets, {summary.tables} tables, "
        f"{summary.routines} routines ({summary.rows_loaded} rows) into {data_dir}.",
    )


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


@main.command(name="backup")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    required=True,
    envvar="BQEMU_DATA_DIR",
)
@click.option(
    "--to",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Destination directory for the backup. Must be empty or absent.",
)
def backup_cmd(data_dir: Path, output_dir: Path) -> None:
    """Capture the persistent DuckDB database to a portable directory."""
    from bqemulator.commands.backup import run_backup

    try:
        run_backup(data_dir=data_dir, output_dir=output_dir)
    except (FileNotFoundError, FileExistsError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Backed up {data_dir} → {output_dir}.")


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


@main.command(name="restore")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    envvar="BQEMU_DATA_DIR",
)
@click.option(
    "--from",
    "input_dir",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    required=True,
    help="Backup directory previously produced by 'bqemulator backup'.",
)
@click.option(
    "--force/--no-force",
    default=False,
    help="Overwrite any existing bqemulator.duckdb under --data-dir.",
)
def restore_cmd(data_dir: Path, input_dir: Path, force: bool) -> None:
    """Restore a backup directory into a persistent data_dir."""
    from bqemulator.commands.restore import run_restore

    try:
        run_restore(data_dir=data_dir, input_dir=input_dir, force=force)
    except (FileNotFoundError, FileExistsError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Restored {input_dir} → {data_dir}.")
