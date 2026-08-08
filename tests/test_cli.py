from __future__ import annotations

import re

from typer.testing import CliRunner

from snowflake_claude_code.cli import app

runner = CliRunner()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class TestCliSmoke:
    def test_help_exits_cleanly(self):
        result = runner.invoke(app, ["--help"])
        output = ANSI_RE.sub("", result.output)

        assert result.exit_code == 0
        assert "--account" in output
        assert "--token" in output
