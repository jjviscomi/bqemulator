"""Tests for the Settings configuration model."""

from __future__ import annotations

from pathlib import Path

import pytest

from bqemulator.config import LogFormat, LogLevel, PersistenceMode, Settings

pytestmark = pytest.mark.unit


class TestDefaults:
    def test_sensible_defaults(self) -> None:
        s = Settings()
        assert s.rest_host == "127.0.0.1"
        assert s.rest_port == 9050
        assert s.grpc_host == "127.0.0.1"
        assert s.grpc_port == 9060
        assert s.persistence_mode is PersistenceMode.EPHEMERAL
        assert s.data_dir is None
        assert s.default_project_id == "test-project"
        assert s.max_concurrent_jobs == 8
        assert s.log_level is LogLevel.INFO
        assert s.log_format is LogFormat.JSON
        assert s.metrics_enabled is True
        assert s.tracing_enabled is False
        assert s.admin_enabled is False


class TestEnvironmentVariables:
    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BQEMU_REST_PORT", "11111")
        monkeypatch.setenv("BQEMU_LOG_LEVEL", "debug")
        s = Settings()
        assert s.rest_port == 11111
        assert s.log_level is LogLevel.DEBUG

    def test_persistence_mode_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("BQEMU_PERSISTENCE_MODE", "persistent")
        monkeypatch.setenv("BQEMU_DATA_DIR", str(tmp_path))
        s = Settings()
        assert s.persistence_mode is PersistenceMode.PERSISTENT
        assert s.data_dir == tmp_path.resolve()


class TestValidation:
    def test_rejects_port_out_of_range(self) -> None:
        with pytest.raises(Exception):
            Settings(rest_port=99999)

    def test_rejects_negative_concurrency(self) -> None:
        with pytest.raises(Exception):
            Settings(max_concurrent_jobs=0)

    def test_rejects_retention_over_90_days(self) -> None:
        with pytest.raises(Exception):
            Settings(time_travel_retention_days=91)

    def test_port_zero_is_allowed(self) -> None:
        s = Settings(rest_port=0, grpc_port=0)
        assert s.rest_port == 0
        assert s.grpc_port == 0


class TestDataDirExpansion:
    def test_tilde_is_expanded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        s = Settings(data_dir=Path("~/bqemu"))
        assert s.data_dir is not None
        assert "~" not in str(s.data_dir)


class TestDuckDbPath:
    def test_ephemeral_returns_memory(self) -> None:
        assert Settings(persistence_mode=PersistenceMode.EPHEMERAL).duckdb_path() == ":memory:"

    def test_persistent_without_data_dir_raises(self) -> None:
        with pytest.raises(ValueError, match="data_dir"):
            Settings(persistence_mode=PersistenceMode.PERSISTENT).duckdb_path()

    def test_persistent_uses_data_dir(self, tmp_path: Path) -> None:
        s = Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=tmp_path)
        path = s.duckdb_path()
        assert path.endswith("bqemulator.duckdb")
        assert str(tmp_path.resolve()) in path

    def test_import_mode_requires_data_dir(self) -> None:
        with pytest.raises(ValueError, match="data_dir"):
            Settings(persistence_mode=PersistenceMode.IMPORT).duckdb_path()
