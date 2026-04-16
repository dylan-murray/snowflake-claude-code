"""Configuration loading with precedence: CLI flags > env vars > TOML file > defaults."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

CONFIG_DIR = Path.home() / ".snowflake-claude-code"
CONFIG_FILE = CONFIG_DIR / "config.toml"

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_PORT = 4000


@dataclass(frozen=True, slots=True)
class Config:
    account: str
    user: str
    model: str
    port: int
    token: str

    @classmethod
    def load(
        cls,
        *,
        account: str | None = None,
        user: str | None = None,
        model: str | None = None,
        port: int | None = None,
        token: str | None = None,
    ) -> Config:
        f = _load_config_file()
        env = os.environ.get

        return cls(
            account=account or env("SNOWFLAKE_ACCOUNT") or f.get("account", ""),
            user=user or env("SNOWFLAKE_USER") or env("SNOWFLAKE_USERNAME") or f.get("user", ""),
            model=model or env("SNOWFLAKE_MODEL") or f.get("default_model") or DEFAULT_MODEL,
            port=port or int(env("SNOWFLAKE_PORT") or f.get("port") or DEFAULT_PORT),
            token=token or env("SNOWFLAKE_TOKEN") or f.get("token", ""),
        )

    def validate(self) -> None:
        if not self.account:
            raise SystemExit("Error: --account is required (or set SNOWFLAKE_ACCOUNT / config file)")
        if not self.token and not self.user:
            raise SystemExit("Error: --user is required for SSO auth (or set SNOWFLAKE_USER / config file)")


def _load_config_file() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open("rb") as f:
        return tomllib.load(f)
