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
from collections.abc import Sequence
from dataclasses import replace

import httpx
import typer
import uvicorn
from rich.console import Console

from snowflake_claude_code.auth import ConnectionManager
from snowflake_claude_code.config import DEFAULT_MODEL, DEFAULT_PORT, Config
from snowflake_claude_code.models import (
    FAMILIES,
    CortexModel,
    advertised,
    discover,
    fallback_models,
    resolve,
)
from snowflake_claude_code.proxy import create_app

_NOISY_LOGGERS = (
    "snowflake",
    "httpx",
    "httpcore",
    "urllib3",
)

app = typer.Typer(add_completion=False)

# Spinners for the waits that would otherwise look like a hang. Rich emits
# nothing at all when stdout is not a terminal, so piped and CI output stay
# free of escape codes.
console = Console()


@app.command()
def main(
    account: str | None = typer.Option(None, help="Snowflake account identifier"),
    user: str | None = typer.Option(None, help="Snowflake username"),
    model: str | None = typer.Option(
        None,
        help=(
            f"Cortex model ID, or a family alias ({', '.join(FAMILIES)}) resolving "
            f"to its newest GA model (default: {DEFAULT_MODEL})"
        ),
    ),
    port: int | None = typer.Option(None, help=f"Local proxy port (default: {DEFAULT_PORT})"),
    token: str | None = typer.Option(None, help="Snowflake programmatic access token (skips SSO)"),
    list_models: bool = typer.Option(
        False,
        "--list-models",
        help="List the Cortex models this account can reach, then exit",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    _configure_logging(verbose)

    config = Config.load(account=account, user=user, model=model, port=port, token=token)
    config.validate()

    manager = ConnectionManager(config)
    # Safe for browser SSO too: Rich's status redirects stdout, so the
    # connector's own instructions — and its input() prompt when the browser
    # won't open — render above the spinner rather than being overwritten.
    with console.status(f"Authenticating to Snowflake ({config.account})..."):
        manager.open()
    typer.echo("Authenticated.")

    with console.status("Listing Cortex models..."):
        available = discover(manager.connection)

    if list_models:
        try:
            _echo_models(available)
        finally:
            manager.close()
        return

    config = replace(config, model=resolve(config.model, available))
    if available:
        typer.echo(f"Found {len(available)} Claude models.")
    else:
        typer.echo("Could not list models; using the built-in fallback list.")

    server = _start_proxy(manager, config, advertised(available))
    try:
        with console.status("Starting proxy..."):
            _wait_for_proxy(config.port)
        typer.echo(f"Proxy ready on 127.0.0.1:{config.port}")
        exit_code = _launch_claude(config)
    finally:
        server.should_exit = True
        manager.close()

    raise SystemExit(exit_code)


def _echo_models(available: Sequence[CortexModel]) -> None:
    models = available
    if models:
        typer.echo(f"Found {len(models)} Claude models on this account:")
    else:
        typer.echo("Could not list models for this account; showing the built-in fallback list:")
        models = fallback_models()

    for model in sorted(models, key=lambda m: (FAMILIES.index(m.family), tuple(-p for p in m.version))):
        typer.echo(f"  {model.name:<24} {model.lifecycle or '-'}")

    typer.echo("\nFamily aliases resolve to:")
    for family in FAMILIES:
        typer.echo(f"  --model {family:<8} -> {resolve(family, available)}")


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


def _start_proxy(manager: ConnectionManager, config: Config, models: Sequence[str]) -> uvicorn.Server:
    fastapi_app = create_app(manager=manager, model=config.model, models=models)
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
    family, _, version = model.removeprefix("claude-").partition("-")
    if not version or family not in {"sonnet", "opus", "haiku"}:
        return model
    return f"{family.capitalize()} {version.replace('-', '.')}"
