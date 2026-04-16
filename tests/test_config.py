from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from snowflake_claude_code.config import DEFAULT_MODEL, DEFAULT_PORT, Config


class TestConfigPrecedence:
    def test_cli_args_take_priority(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.toml"
        config_file.write_text('account = "file-account"\nuser = "file-user"\n')
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "env-account")
        monkeypatch.setenv("SNOWFLAKE_USER", "env-user")

        with patch("snowflake_claude_code.config.CONFIG_FILE", config_file):
            cfg = Config.load(account="cli-account", user="cli-user")

        assert cfg.account == "cli-account"
        assert cfg.user == "cli-user"

    def test_env_vars_over_file(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.toml"
        config_file.write_text('account = "file-account"\nuser = "file-user"\n')
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "env-account")
        monkeypatch.setenv("SNOWFLAKE_USER", "env-user")

        with patch("snowflake_claude_code.config.CONFIG_FILE", config_file):
            cfg = Config.load()

        assert cfg.account == "env-account"
        assert cfg.user == "env-user"

    def test_file_as_fallback(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            'account = "file-account"\nuser = "file-user"\ndefault_model = "claude-opus-4-5"\n'
        )
        monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
        monkeypatch.delenv("SNOWFLAKE_USER", raising=False)
        monkeypatch.delenv("SNOWFLAKE_USERNAME", raising=False)
        monkeypatch.delenv("SNOWFLAKE_MODEL", raising=False)

        with patch("snowflake_claude_code.config.CONFIG_FILE", config_file):
            cfg = Config.load()

        assert cfg.account == "file-account"
        assert cfg.user == "file-user"
        assert cfg.model == "claude-opus-4-5"

    def test_defaults_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
        monkeypatch.delenv("SNOWFLAKE_USER", raising=False)
        monkeypatch.delenv("SNOWFLAKE_USERNAME", raising=False)
        monkeypatch.delenv("SNOWFLAKE_MODEL", raising=False)
        monkeypatch.delenv("SNOWFLAKE_PORT", raising=False)
        monkeypatch.delenv("SNOWFLAKE_TOKEN", raising=False)

        with patch("snowflake_claude_code.config.CONFIG_FILE", Path("/nonexistent")):
            cfg = Config.load()

        assert cfg.account == ""
        assert cfg.user == ""
        assert cfg.model == DEFAULT_MODEL
        assert cfg.port == DEFAULT_PORT
        assert cfg.token == ""

    def test_port_from_env(self, monkeypatch):
        monkeypatch.setenv("SNOWFLAKE_PORT", "8080")
        monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
        monkeypatch.delenv("SNOWFLAKE_USER", raising=False)
        monkeypatch.delenv("SNOWFLAKE_USERNAME", raising=False)

        with patch("snowflake_claude_code.config.CONFIG_FILE", Path("/nonexistent")):
            cfg = Config.load()

        assert cfg.port == 8080

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("SNOWFLAKE_TOKEN", "pat-abc123")
        monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
        monkeypatch.delenv("SNOWFLAKE_USER", raising=False)
        monkeypatch.delenv("SNOWFLAKE_USERNAME", raising=False)

        with patch("snowflake_claude_code.config.CONFIG_FILE", Path("/nonexistent")):
            cfg = Config.load(account="acct")

        assert cfg.token == "pat-abc123"


class TestConfigValidation:
    def test_missing_account_exits(self, monkeypatch):
        monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)

        with patch("snowflake_claude_code.config.CONFIG_FILE", Path("/nonexistent")):
            cfg = Config.load()

        with pytest.raises(SystemExit, match="--account is required"):
            cfg.validate()

    def test_missing_user_without_token_exits(self, monkeypatch):
        monkeypatch.delenv("SNOWFLAKE_USER", raising=False)
        monkeypatch.delenv("SNOWFLAKE_USERNAME", raising=False)
        monkeypatch.delenv("SNOWFLAKE_TOKEN", raising=False)

        with patch("snowflake_claude_code.config.CONFIG_FILE", Path("/nonexistent")):
            cfg = Config.load(account="acct")

        with pytest.raises(SystemExit, match="--user is required"):
            cfg.validate()

    def test_token_without_user_is_valid(self, monkeypatch):
        monkeypatch.delenv("SNOWFLAKE_USER", raising=False)
        monkeypatch.delenv("SNOWFLAKE_USERNAME", raising=False)

        with patch("snowflake_claude_code.config.CONFIG_FILE", Path("/nonexistent")):
            cfg = Config.load(account="acct", token="pat-123")

        cfg.validate()

    def test_user_without_token_is_valid(self, monkeypatch):
        monkeypatch.delenv("SNOWFLAKE_TOKEN", raising=False)

        with patch("snowflake_claude_code.config.CONFIG_FILE", Path("/nonexistent")):
            cfg = Config.load(account="acct", user="me@co.com")

        cfg.validate()
