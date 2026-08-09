from __future__ import annotations

import re

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
