from __future__ import annotations

import contextlib
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from snowflake_claude_code.proxy import create_app


class _FakeEvent:
    def __init__(self, data: str):
        self.data = data


class _FakeSSEClient:
    def __init__(self, chunks: list[dict]):
        self._chunks = chunks

    def events(self):
        for chunk in self._chunks:
            yield _FakeEvent(json.dumps(chunk))


@pytest.fixture()
def mock_service():
    return MagicMock()


@pytest.fixture()
def mock_manager(mock_service):
    manager = MagicMock()
    manager.service = mock_service
    return manager


@pytest.fixture()
def client(mock_manager, mock_service):
    app = create_app(manager=mock_manager, model="claude-sonnet-4-5")
    with TestClient(app) as c:
        c._service = mock_service
        c._manager = mock_manager
        yield c


def _cortex_response(
    content: list[dict] | None = None,
    stop_reason: str = "end_turn",
) -> dict:
    if content is None:
        content = [{"type": "text", "text": "Hello!"}]
    return {
        "id": "msg-test",
        "content": content,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _anthropic_request(
    content: str = "hi",
    stream: bool = False,
    tools: list | None = None,
    model: str = "claude-sonnet-4-5-20250514",
) -> dict:
    req: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 1024,
    }
    if stream:
        req["stream"] = True
    if tools:
        req["tools"] = tools
    return req


class TestHealthEndpoint:
    def test_returns_ok(self, client):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_head_v1(self, client):
        resp = client.head("/v1")
        assert resp.status_code == 200


class TestModelsEndpoint:
    def test_returns_model_list(self, client):
        resp = client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        ids = [m["id"] for m in data["data"]]
        assert "claude-sonnet-4-6" in ids
        assert "claude-sonnet-4-5" in ids
        assert "claude-opus-4-6" in ids


class TestMessagesEndpoint:
    def test_translates_simple_request(self, client):
        cortex_resp = _cortex_response()
        client._service.complete.return_value = _FakeSSEClient([cortex_resp])

        with patch("snowflake_claude_code.proxy.CompleteRequest"):
            resp = client.post("/v1/messages", json=_anthropic_request("hi"))

        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "message"
        assert body["role"] == "assistant"
        assert body["content"][0]["text"] == "Hello!"
        assert body["stop_reason"] == "end_turn"
        assert body["usage"]["input_tokens"] == 10

    def test_tool_use_response(self, client):
        cortex_resp = _cortex_response(
            content=[
                {"type": "tool_use", "id": "toolu_123", "name": "read_file", "input": {"path": "/tmp/x"}},
            ],
            stop_reason="tool_use",
        )
        client._service.complete.return_value = _FakeSSEClient([cortex_resp])

        with patch("snowflake_claude_code.proxy.CompleteRequest"):
            resp = client.post(
                "/v1/messages",
                json=_anthropic_request(
                    tools=[{"name": "read_file", "description": "Read", "input_schema": {"type": "object"}}],
                ),
            )

        body = resp.json()
        assert body["stop_reason"] == "tool_use"
        assert body["content"][0]["type"] == "tool_use"
        assert body["content"][0]["name"] == "read_file"

    def test_cortex_error_returns_502(self, client):
        client._service.complete.side_effect = Exception("connection failed")

        with patch("snowflake_claude_code.proxy.CompleteRequest"):
            resp = client.post("/v1/messages", json=_anthropic_request())

        assert resp.status_code == 502
        body = resp.json()
        assert body["type"] == "error"

    def test_1m_suffix_stripped_routes_to_base_model(self, client):
        client._service.complete.return_value = _FakeSSEClient([_cortex_response()])

        with patch("snowflake_claude_code.proxy.CompleteRequest"):
            resp = client.post(
                "/v1/messages",
                json=_anthropic_request(model="claude-sonnet-4-6[1m]"),
            )

        assert resp.status_code == 200
        assert resp.json()["model"] == "claude-sonnet-4-6"

    def test_401_triggers_reauth_and_retry(self, client):
        from snowflake.core.exceptions import APIError

        expired = APIError(root=None, status=401, reason="Unauthorized")
        client._service.complete.side_effect = [expired, _FakeSSEClient([_cortex_response()])]

        with patch("snowflake_claude_code.proxy.CompleteRequest"):
            resp = client.post("/v1/messages", json=_anthropic_request())

        assert resp.status_code == 200
        assert client._manager.reauth.call_count == 1
        assert client._service.complete.call_count == 2

    def test_non_401_api_error_not_retried(self, client):
        from snowflake.core.exceptions import APIError

        server_err = APIError(root=None, status=500, reason="Internal Server Error")
        client._service.complete.side_effect = server_err

        with patch("snowflake_claude_code.proxy.CompleteRequest"):
            resp = client.post("/v1/messages", json=_anthropic_request())

        assert resp.status_code == 502
        assert client._manager.reauth.call_count == 0


class TestStreamingEndpoint:
    def test_streams_text_response(self, client):
        chunks = [
            {"id": "chatcmpl-s1", "choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]},
            {"id": "chatcmpl-s1", "choices": [{"delta": {"content": " world"}, "finish_reason": None}]},
            {
                "id": "chatcmpl-s1",
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        ]
        client._service.complete.return_value = _FakeSSEClient(chunks)

        with patch("snowflake_claude_code.proxy.CompleteRequest"):
            resp = client.post("/v1/messages", json=_anthropic_request(stream=True))

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        events = _parse_sse_response(resp.text)
        types = [e["type"] for e in events]

        assert types[0] == "message_start"
        assert "content_block_start" in types
        assert "content_block_delta" in types
        assert "message_stop" in types

        deltas = [e for e in events if e.get("type") == "content_block_delta"]
        text = "".join(d["delta"]["text"] for d in deltas if d["delta"].get("type") == "text_delta")
        assert text == "Hello world"

    def test_stream_error(self, client):
        client._service.complete.side_effect = Exception("timeout")

        with patch("snowflake_claude_code.proxy.CompleteRequest"):
            resp = client.post("/v1/messages", json=_anthropic_request(stream=True))

        events = _parse_sse_response(resp.text)
        assert any(e.get("type") == "error" for e in events)


def _parse_sse_response(text: str) -> list[dict]:
    events = []
    for raw_block in text.split("\n\n"):
        block = raw_block.strip()
        if not block:
            continue
        for line in block.split("\n"):
            if line.startswith("data: "):
                with contextlib.suppress(json.JSONDecodeError):
                    events.append(json.loads(line[6:]))
    return events
