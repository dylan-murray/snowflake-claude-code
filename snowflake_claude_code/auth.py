"""Snowflake authentication helpers. Uses PAT (OAuth) when a token is provided,
otherwise falls back to browser-based SSO. Exposes ``ConnectionManager`` which
owns the lifetime of a Snowflake connection and transparently re-auths when
tokens expire.
"""

from __future__ import annotations

import logging
import threading
from contextlib import suppress

import snowflake.connector
from snowflake.connector import SnowflakeConnection
from snowflake.core import Root
from snowflake.core.cortex.inference_service import CortexInferenceService

from snowflake_claude_code.config import Config

logger = logging.getLogger(__name__)


def connect(config: Config) -> SnowflakeConnection:
    if config.token:
        return snowflake.connector.connect(
            account=config.account,
            token=config.token,
            authenticator="oauth",
        )

    return snowflake.connector.connect(
        account=config.account,
        user=config.user,
        authenticator="externalbrowser",
    )


class ConnectionManager:
    """Owns a Snowflake connection and its derived Cortex service.

    Call :meth:`open` once to establish the initial session, then access
    :attr:`service` for inference calls. When a Cortex call raises a 401,
    call :meth:`reauth` to rebuild the connection in place. Safe to call
    from multiple threads — ``reauth`` is serialized and idempotent.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._conn: SnowflakeConnection | None = None
        self._service: CortexInferenceService | None = None

    def open(self) -> None:
        with self._lock:
            self._rebuild()

    def reauth(self) -> None:
        """Close the current connection and establish a new one."""
        with self._lock:
            logger.warning("Snowflake session expired; re-authenticating...")
            self._rebuild()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                with suppress(Exception):
                    self._conn.close()
            self._conn = None
            self._service = None

    @property
    def service(self) -> CortexInferenceService:
        if self._service is None:
            raise RuntimeError("ConnectionManager.open() must be called first")
        return self._service

    def _rebuild(self) -> None:
        if self._conn is not None:
            with suppress(Exception):
                self._conn.close()
        self._conn = connect(self._config)
        self._service = CortexInferenceService(Root(self._conn))
