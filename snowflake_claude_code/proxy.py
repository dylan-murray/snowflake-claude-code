"""FastAPI proxy that translates Anthropic Messages API calls to Snowflake
Cortex Inference REST calls, and Cortex responses back to Anthropic format.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from snowflake.core.cortex.inference_service import (
    CompleteRequest,
    CompleteRequestMessagesInner,
)
from snowflake.core.cortex.inference_service._generated.models import (
    Tool,
    ToolChoice,
    ToolToolSpec,
    ToolToolSpecInputSchema,
)
from snowflake.core.exceptions import APIError
from urllib3.exceptions import ProtocolError

from snowflake_claude_code.auth import ConnectionManager
from snowflake_claude_code.translate import (
    StreamAdapter,
    anthropic_to_cortex,
    cortex_to_anthropic,
    normalize_model,
    sse_event,
)

logger = logging.getLogger(__name__)

# Transient network failures where retrying on a fresh socket is safe.
# Most commonly: urllib3 pulled a dead keep-alive connection out of the pool
# after the server closed it for being idle. These errors aren't logged at
# ERROR level — they're noisy and Claude Code recovers transparently.
_TRANSIENT_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    ProtocolError,
    ConnectionError,
    TimeoutError,
)

CORTEX_MODELS: tuple[str, ...] = (
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-haiku-4-5",
)


def create_app(*, manager: ConnectionManager, model: str) -> FastAPI:
    """Build the FastAPI app that serves the Anthropic-compatible endpoints.

    Args:
        manager: Holds the Snowflake connection. Its ``service`` is used per
            request so that a ``reauth()`` mid-session is picked up without
            restarting the app.
        model: Default Cortex model ID to use when a request omits one.
    """
    default_model = normalize_model(model)

    app = FastAPI()

    @app.head("/v1")
    @app.get("/v1")
    async def v1_root() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {"id": m, "object": "model", "created": 0, "owned_by": "snowflake"} for m in CORTEX_MODELS
            ],
        }

    @app.post("/v1/messages")
    async def messages(request: Request) -> Any:
        body = await request.json()
        requested = body.get("model") or default_model
        resolved = normalize_model(requested)
        cortex_body = anthropic_to_cortex(body, resolved)
        is_stream = bool(cortex_body.get("stream", False))

        try:
            complete_req = _build_complete_request(cortex_body)
        except Exception as e:
            logger.exception("Failed to build CompleteRequest")
            return _error_response(400, "invalid_request_error", str(e))

        logger.debug("POST /v1/messages model=%s stream=%s", resolved, is_stream)

        if is_stream:
            return StreamingResponse(
                _stream(manager, complete_req, resolved),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

        try:
            full_response = await asyncio.to_thread(_collect_response, manager, complete_req)
        except Exception as e:
            _log_cortex_error("Cortex error", e)
            return _error_response(502, "api_error", str(e))

        return JSONResponse(content=cortex_to_anthropic(full_response, resolved))

    return app


def _build_complete_request(cortex_body: dict[str, Any]) -> CompleteRequest:
    return CompleteRequest(
        model=cortex_body["model"],
        messages=[_build_message(m) for m in cortex_body.get("messages", [])],
        max_tokens=cortex_body.get("max_tokens"),
        temperature=cortex_body.get("temperature"),
        top_p=cortex_body.get("top_p"),
        tools=_build_tools(cortex_body.get("tools")),
        tool_choice=_build_tool_choice(cortex_body.get("tool_choice")),
        stream=cortex_body.get("stream", True),
    )


def _build_message(msg: dict[str, Any]) -> CompleteRequestMessagesInner:
    return CompleteRequestMessagesInner(
        role=msg.get("role", "user"),
        content=msg.get("content"),
        content_list=msg.get("content_list"),
    )


def _build_tools(raw_tools: list[dict[str, Any]] | None) -> list[Tool] | None:
    if not raw_tools:
        return None
    return [_build_tool(t) for t in raw_tools]


def _build_tool(tool: dict[str, Any]) -> Tool:
    spec = tool["tool_spec"]
    schema = ToolToolSpecInputSchema()
    if input_schema := spec.get("input_schema"):
        schema.additional_properties = input_schema
    return Tool(
        tool_spec=ToolToolSpec(
            type=spec.get("type", "generic"),
            name=spec["name"],
            description=spec.get("description", ""),
            input_schema=schema,
        ),
    )


def _build_tool_choice(raw: dict[str, Any] | None) -> ToolChoice | None:
    if not raw:
        return None
    return ToolChoice(type=raw.get("type", "auto"), name=raw.get("name"))


def _complete_with_reauth(manager: ConnectionManager, request: CompleteRequest) -> Any:
    """Call ``service.complete`` with one retry on 401 (re-auth) or transient
    network failures (fresh socket). Anything else propagates."""
    try:
        return manager.service.complete(request)
    except APIError as e:
        if getattr(e, "status", None) != 401:
            raise
        manager.reauth()
        return manager.service.complete(request)
    except _TRANSIENT_NETWORK_ERRORS as e:
        logger.debug("Transient network error from Cortex (%s); retrying once", e)
        return manager.service.complete(request)


def _collect_response(manager: ConnectionManager, request: CompleteRequest) -> dict[str, Any]:
    sse_client = _complete_with_reauth(manager, request)
    full_response: dict[str, Any] = {}
    for event in sse_client.events():
        full_response = json.loads(event.data)
    return full_response


async def _stream(
    manager: ConnectionManager,
    request: CompleteRequest,
    model: str,
) -> Any:
    adapter = StreamAdapter(model)

    try:
        sse_client = await asyncio.to_thread(_complete_with_reauth, manager, request)
        event_iter = iter(sse_client.events())

        while True:
            event = await asyncio.to_thread(next, event_iter, None)
            if event is None:
                break
            try:
                data = json.loads(event.data)
            except json.JSONDecodeError:
                continue
            for sse in adapter.feed(data):
                yield sse
    except Exception as e:
        _log_cortex_error("Cortex stream error", e)
        yield sse_event("error", {"type": "error", "error": {"type": "api_error", "message": str(e)}})
        return

    for sse in adapter.finish():
        yield sse


def _log_cortex_error(label: str, exc: BaseException) -> None:
    """Log at DEBUG for transient network blips (routine; Claude Code recovers),
    at ERROR with a traceback for everything else."""
    if isinstance(exc, _TRANSIENT_NETWORK_ERRORS):
        logger.debug("%s (transient): %s", label, exc)
    else:
        logger.exception(label)


def _error_response(status: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": error_type, "message": message}},
    )
