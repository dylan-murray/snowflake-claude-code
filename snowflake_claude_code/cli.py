"""Command-line entry point that authenticates to Snowflake, starts the
translation proxy on localhost, and launches Claude Code pointed at it.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

import httpx
import typer
import uvicorn

from snowflake_claude_code.auth import ConnectionManager
from snowflake_claude_code.config import DEFAULT_MODEL, DEFAULT_PORT, Config
from snowflake_claude_code.proxy import create_app

_NOISY_LOGGERS = (
    "snowflake",
    "httpx",
    "httpcore",
    "urllib3",
)

app = typer.Typer(add_completion=False)


@app.command()
def main(
    account: str | None = typer.Option(None, help="Snowflake account identifier"),
    user: str | None = typer.Option(None, help="Snowflake username"),
    model: str | None = typer.Option(None, help=f"Cortex model (default: {DEFAULT_MODEL})"),
    port: int | None = typer.Option(None, help=f"Local proxy port (default: {DEFAULT_PORT})"),
    token: str | None = typer.Option(None, help="Snowflake programmatic access token (skips SSO)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    _configure_logging(verbose)

    config = Config.load(account=account, user=user, model=model, port=port, token=token)
    config.validate()

    typer.echo(f"Authenticating to Snowflake ({config.account})...")
    manager = ConnectionManager(config)
    manager.open()
    typer.echo("Authenticated.")

    server = _start_proxy(manager, config)
    try:
        _wait_for_proxy(config.port)
        typer.echo(f"Proxy ready on 127.0.0.1:{config.port}")
        exit_code = _launch_claude(config)
    finally:
        server.should_exit = True
        manager.close()

    raise SystemExit(exit_code)


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if not verbose:
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.ERROR)


def _start_proxy(manager: ConnectionManager, config: Config) -> uvicorn.Server:
    fastapi_app = create_app(manager=manager, model=config.model)
    server = uvicorn.Server(
        uvicorn.Config(
            fastapi_app,
            host="127.0.0.1",
            port=config.port,
            log_level="warning",
        )
    )
    threading.Thread(target=server.run, daemon=True).start()
    return server


def _wait_for_proxy(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/v1", timeout=1)
            if resp.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.1)
    raise SystemExit(f"Error: proxy failed to start within {timeout}s")


def _launch_claude(config: Config) -> int:
    claude_bin = _find_claude()
    typer.echo(f"Launching Claude Code (model: {config.model})...")

    env = {
        **os.environ,
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{config.port}",
        "ANTHROPIC_API_KEY": "sk-snowflake-proxy",
        "ANTHROPIC_MODEL": config.model,
        "ANTHROPIC_CUSTOM_MODEL_OPTION": config.model,
        "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME": _pretty_model_name(config.model),
        "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION": "Via Snowflake Cortex",
    }

    proc = subprocess.Popen(
        [claude_bin, "--bare"],
        env=env,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    def _forward(signum: int, _: object) -> None:
        proc.send_signal(signum)

    prev_int = signal.signal(signal.SIGINT, _forward)
    prev_term = signal.signal(signal.SIGTERM, _forward)
    try:
        return proc.wait()
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


def _find_claude() -> str:
    if path := shutil.which("claude"):
        return path
    raise SystemExit(
        "Error: 'claude' not found in PATH. Install with: npm install -g @anthropic-ai/claude-code"
    )


def _pretty_model_name(model: str) -> str:
    segments = model.removeprefix("claude-").split("-")
    if len(segments) >= 3 and segments[0] in {"sonnet", "opus", "haiku"}:
        family, major, minor = segments[0], segments[1], segments[2]
        return f"{family.capitalize()} {major}.{minor}"
    return model
