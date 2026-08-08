from __future__ import annotations

from typer.testing import CliRunner

from snowflake_claude_code.cli import app

runner = CliRunner()


class TestCliSmoke:
    def test_help_exits_cleanly(self):
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "--account" in result.output
        assert "--token" in result.output
