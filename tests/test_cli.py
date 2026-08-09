from __future__ import annotations

import contextlib
import re
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from snowflake_claude_code.cli import _pretty_model_name, app
from snowflake_claude_code.config import DEFAULT_MODEL
from snowflake_claude_code.models import FAMILIES, advertised, fallback_models

runner = CliRunner()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class TestCliSmoke:
    def test_help_exits_cleanly(self):
        result = runner.invoke(app, ["--help"])
        output = ANSI_RE.sub("", result.output)

        assert result.exit_code == 0
        assert "--account" in output
        assert "--token" in output


class TestStartupProgress:
    """Every step slow enough to look like a hang must announce itself."""

    def _run(self, monkeypatch, discovered, spy_status=True):
        import snowflake_claude_code.cli as cli

        statuses: list[str] = []

        @contextlib.contextmanager
        def fake_status(message, *args, **kwargs):
            statuses.append(message)
            yield

        if spy_status:
            monkeypatch.setattr(cli.console, "status", fake_status)
        monkeypatch.setattr(cli, "ConnectionManager", MagicMock())
        monkeypatch.setattr(cli, "discover", lambda conn: discovered)
        monkeypatch.setattr(cli, "_start_proxy", MagicMock())
        monkeypatch.setattr(cli, "_wait_for_proxy", MagicMock())
        monkeypatch.setattr(cli, "_launch_claude", MagicMock(return_value=0))
        result = runner.invoke(app, ["--account", "acct", "--user", "someone"])
        return result, statuses

    def test_announces_each_slow_step(self, monkeypatch):
        result, statuses = self._run(monkeypatch, list(fallback_models()))
        output = ANSI_RE.sub("", result.output)

        assert "Authenticating to Snowflake" in output
        assert "Listing Cortex models..." in statuses
        assert "Starting proxy..." in statuses

    def test_reports_discovered_model_count(self, monkeypatch):
        discovered = [m for m in fallback_models() if m.family == "sonnet"]

        result, _ = self._run(monkeypatch, discovered)

        assert f"Found {len(discovered)} Claude models." in ANSI_RE.sub("", result.output)

    def test_says_so_when_falling_back(self, monkeypatch):
        result, _ = self._run(monkeypatch, [])

        assert "using the built-in fallback list" in ANSI_RE.sub("", result.output)

    def test_emits_no_escape_codes_when_not_a_terminal(self, monkeypatch):
        """The real spinner must stay silent when stdout is piped, so logs and
        CI output don't fill with cursor-control sequences."""
        result, _ = self._run(monkeypatch, list(fallback_models()), spy_status=False)

        assert "\x1b[" not in result.output


class TestPrettyModelName:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("claude-sonnet-5", "Sonnet 5"),
            ("claude-opus-5", "Opus 5"),
            ("claude-sonnet-4-6", "Sonnet 4.6"),
            ("claude-opus-4-8", "Opus 4.8"),
            ("claude-haiku-4-5", "Haiku 4.5"),
        ],
    )
    def test_formats_claude_models(self, model, expected):
        assert _pretty_model_name(model) == expected

    @pytest.mark.parametrize("model", ["mistral-large2", "llama3.1-70b", "claude"])
    def test_passes_through_non_claude_models(self, model):
        assert _pretty_model_name(model) == model

    def test_every_advertised_model_formats(self):
        for model in advertised(()):
            assert _pretty_model_name(model) != model, model


class TestDefaultModel:
    def test_default_is_a_family_alias(self):
        assert DEFAULT_MODEL in FAMILIES

    def test_default_resolves_without_discovery(self):
        from snowflake_claude_code.models import resolve

        resolved = resolve(DEFAULT_MODEL, ())
        assert resolved in advertised(())
        assert any(m.name == resolved and m.is_ga for m in fallback_models())
