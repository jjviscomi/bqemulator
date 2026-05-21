"""Tests for the click CLI.

We exercise the CLI parser without actually starting a server — the
`start` command is covered by integration tests (which spin up the
server via the pytest fixture).
"""

from __future__ import annotations

from unittest import mock

from click.testing import CliRunner
import pytest

from bqemulator import __version__
from bqemulator.cli import main

pytestmark = pytest.mark.unit


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestVersion:
    def test_version_subcommand_prints_version(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_version_flag(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output


class TestHelp:
    def test_help_lists_commands(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "start" in result.output
        assert "version" in result.output


class TestStart:
    def test_start_invokes_server_with_settings(self, runner: CliRunner) -> None:
        with mock.patch("bqemulator.server.run_forever") as mock_run:
            result = runner.invoke(
                main,
                ["start", "--rest-port", "12345", "--grpc-port", "0", "--ephemeral"],
            )
            assert result.exit_code == 0, result.output
            mock_run.assert_called_once()
            settings = mock_run.call_args.args[0]
            assert settings.rest_port == 12345
            assert settings.grpc_port == 0

    def test_start_with_data_dir_implies_persistent(
        self,
        runner: CliRunner,
        tmp_path: object,
    ) -> None:
        with mock.patch("bqemulator.server.run_forever") as mock_run:
            result = runner.invoke(
                main,
                ["start", "--data-dir", str(tmp_path)],
            )
            assert result.exit_code == 0, result.output
            settings = mock_run.call_args.args[0]
            from bqemulator.config import PersistenceMode

            assert settings.persistence_mode is PersistenceMode.PERSISTENT


class TestImport:
    def test_import_invokes_run_import_with_settings(
        self,
        runner: CliRunner,
        tmp_path: object,
    ) -> None:
        from bqemulator.commands.import_project import ImportSummary

        summary = ImportSummary()
        summary.datasets = 2
        summary.tables = 5
        summary.routines = 1
        with mock.patch(
            "bqemulator.commands.import_project.run_import",
            return_value=summary,
        ) as mock_run:
            result = runner.invoke(
                main,
                [
                    "import",
                    "--from-project",
                    "real",
                    "--dataset",
                    "ds1",
                    "--dataset",
                    "ds2",
                    "--target-project",
                    "local",
                    "--data-dir",
                    str(tmp_path),  # type: ignore[arg-type]
                ],
            )
            assert result.exit_code == 0, result.output
            mock_run.assert_called_once()
            kwargs = mock_run.call_args.kwargs
            assert kwargs["source_project"] == "real"
            assert kwargs["dataset_filters"] == ["ds1", "ds2"]
            assert kwargs["target_project"] == "local"
            assert "Imported 2 datasets, 5 tables, 1 routines" in result.output

    def test_import_errors_when_extra_missing(self, runner: CliRunner) -> None:
        """Simulate the 'import' extra not being installed.

        Phase 10 ships the ``bqemulator.commands.import_project`` module
        unconditionally, but the runtime still requires
        ``google.cloud.bigquery`` (only present when the extra was
        installed). We simulate that by patching ``run_import`` to fail
        with the same :class:`ImportError` the deferred import path
        would raise; the CLI must catch it and surface the install
        instruction.
        """
        with mock.patch(
            "bqemulator.commands.import_project.run_import",
            side_effect=ImportError("No module named 'google'"),
        ):
            result = runner.invoke(
                main,
                ["import", "--from-project", "real", "--data-dir", "/tmp/x"],
            )
        assert result.exit_code != 0
        assert "bqemulator[import]" in result.output


class TestExport:
    def test_export_invokes_run_export(
        self,
        runner: CliRunner,
        tmp_path: object,
    ) -> None:
        from bqemulator.commands.export import ExportSummary

        summary = ExportSummary()
        summary.datasets = 1
        summary.tables = 3
        summary.routines = 0
        summary.rows_written = 100
        with mock.patch(
            "bqemulator.commands.export.run_export",
            return_value=summary,
        ) as mock_run:
            result = runner.invoke(
                main,
                [
                    "export",
                    "--data-dir",
                    str(tmp_path),  # type: ignore[arg-type]
                    "--output-dir",
                    str(tmp_path) + "/out",  # type: ignore[operator]
                ],
            )
            assert result.exit_code == 0, result.output
            mock_run.assert_called_once()
            assert "Exported 1 datasets, 3 tables" in result.output

    def test_export_surfaces_clean_error_on_missing_db(
        self,
        runner: CliRunner,
        tmp_path: object,
    ) -> None:
        with mock.patch(
            "bqemulator.commands.export.run_export",
            side_effect=FileNotFoundError("no db"),
        ):
            result = runner.invoke(
                main,
                [
                    "export",
                    "--data-dir",
                    str(tmp_path),  # type: ignore[arg-type]
                    "--output-dir",
                    str(tmp_path) + "/out",  # type: ignore[operator]
                ],
            )
        assert result.exit_code != 0
        assert "no db" in result.output


class TestSeed:
    def test_seed_invokes_run_seed(self, runner: CliRunner, tmp_path: object) -> None:
        from bqemulator.commands.seed import SeedSummary

        summary = SeedSummary()
        summary.datasets = 1
        summary.tables = 2
        summary.routines = 0
        summary.rows_loaded = 50
        # Create input dir so click's existing-path validation passes.
        input_dir = tmp_path / "in"  # type: ignore[operator]
        input_dir.mkdir()
        with mock.patch(
            "bqemulator.commands.seed.run_seed",
            return_value=summary,
        ) as mock_run:
            result = runner.invoke(
                main,
                [
                    "seed",
                    "--data-dir",
                    str(tmp_path),  # type: ignore[arg-type]
                    "--input-dir",
                    str(input_dir),
                ],
            )
            assert result.exit_code == 0, result.output
            mock_run.assert_called_once()
            assert "Seeded 1 datasets" in result.output


class TestBackup:
    def test_backup_invokes_run_backup(
        self,
        runner: CliRunner,
        tmp_path: object,
    ) -> None:
        from bqemulator.commands.backup import BackupSummary

        with mock.patch(
            "bqemulator.commands.backup.run_backup",
            return_value=BackupSummary(output_dir=tmp_path / "out"),  # type: ignore[operator]
        ) as mock_run:
            result = runner.invoke(
                main,
                [
                    "backup",
                    "--data-dir",
                    str(tmp_path),  # type: ignore[arg-type]
                    "--to",
                    str(tmp_path) + "/out",  # type: ignore[operator]
                ],
            )
            assert result.exit_code == 0, result.output
            mock_run.assert_called_once()
            assert "Backed up" in result.output


class TestRestore:
    def test_restore_invokes_run_restore(
        self,
        runner: CliRunner,
        tmp_path: object,
    ) -> None:
        from bqemulator.commands.restore import RestoreSummary

        input_dir = tmp_path / "in"  # type: ignore[operator]
        input_dir.mkdir()
        with mock.patch(
            "bqemulator.commands.restore.run_restore",
            return_value=RestoreSummary(data_dir=tmp_path),  # type: ignore[arg-type]
        ) as mock_run:
            result = runner.invoke(
                main,
                [
                    "restore",
                    "--data-dir",
                    str(tmp_path),  # type: ignore[arg-type]
                    "--from",
                    str(input_dir),
                    "--force",
                ],
            )
            assert result.exit_code == 0, result.output
            mock_run.assert_called_once()
            kwargs = mock_run.call_args.kwargs
            assert kwargs["force"] is True
            assert "Restored" in result.output
