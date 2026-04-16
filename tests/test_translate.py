from __future__ import annotations

import json

from snowflake_claude_code.translate import (
    StreamAdapter,
    anthropic_to_cortex,
    cortex_to_anthropic,
    normalize_model,
)


def _parse_sse(raw: str) -> list[dict]:
    events = []
    for block in raw.strip().split("\n\n"):
        if not block:
            continue
        lines = block.strip().split("\n")
        data_line = next(line for line in lines if line.startswith("data: "))
        events.append(json.loads(data_line[6:]))
    return events


def _parse_sse_list(raw_list: list[str]) -> list[dict]:
    return _parse_sse("".join(raw_list))


class TestNormalizeModel:
    def test_strips_date_suffix(self):
        assert normalize_model("claude-sonnet-4-5-20250514") == "claude-sonnet-4-5"

    def test_strips_date_from_haiku(self):
        assert normalize_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"

    def test_preserves_clean_name(self):
        assert normalize_model("claude-sonnet-4-5") == "claude-sonnet-4-5"

    def test_preserves_long_context_suffix(self):
        assert normalize_model("claude-opus-4-6-long-context") == "claude-opus-4-6-long-context"

    def test_preserves_unknown_model(self):
        assert normalize_model("gpt-4o") == "gpt-4o"

    def test_only_strips_8_digit_suffix(self):
        assert normalize_model("claude-3-5-sonnet") == "claude-3-5-sonnet"
        assert normalize_model("model-1234567") == "model-1234567"

    def test_strips_1m_suffix(self):
        assert normalize_model("claude-sonnet-4-6[1m]") == "claude-sonnet-4-6"

    def test_strips_1m_suffix_with_date(self):
        assert normalize_model("claude-opus-4-6-20251015[1m]") == "claude-opus-4-6"


class TestAnthropicToCortex:
    def test_simple_message(self):
        result = anthropic_to_cortex(
            {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 1024},
            model="claude-sonnet-4-5",
        )
        assert result["model"] == "claude-sonnet-4-5"
        assert result["messages"] == [{"role": "user", "content": "hello"}]
        assert result["max_tokens"] == 1024

    def test_system_string(self):
        result = anthropic_to_cortex(
            {"system": "Be helpful", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
            model="m",
        )
        assert result["messages"][0] == {"role": "system", "content": "Be helpful"}
        assert result["messages"][1] == {"role": "user", "content": "hi"}

    def test_system_content_blocks(self):
        result = anthropic_to_cortex(
            {
                "system": [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}],
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            },
            model="m",
        )
        assert result["messages"][0] == {"role": "system", "content": "line1\nline2"}

    def test_max_tokens_floor(self):
        result = anthropic_to_cortex(
            {"messages": [], "max_tokens": 1},
            model="m",
        )
        assert result["max_tokens"] == 32

    def test_stream_flag(self):
        result = anthropic_to_cortex(
            {"messages": [], "max_tokens": 100, "stream": True},
            model="m",
        )
        assert result["stream"] is True

    def test_tool_conversion_snowflake_format(self):
        result = anthropic_to_cortex(
            {
                "messages": [],
                "max_tokens": 100,
                "tools": [
                    {
                        "name": "read_file",
                        "description": "Read a file",
                        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                    }
                ],
            },
            model="m",
        )
        tool = result["tools"][0]
        assert tool["tool_spec"]["type"] == "generic"
        assert tool["tool_spec"]["name"] == "read_file"
        assert tool["tool_spec"]["description"] == "Read a file"
        assert tool["tool_spec"]["input_schema"]["properties"]["path"]["type"] == "string"

    def test_tool_choice_auto(self):
        result = anthropic_to_cortex(
            {"messages": [], "max_tokens": 100, "tool_choice": {"type": "auto"}},
            model="m",
        )
        assert result["tool_choice"] == {"type": "auto"}

    def test_tool_choice_any(self):
        result = anthropic_to_cortex(
            {"messages": [], "max_tokens": 100, "tool_choice": {"type": "any"}},
            model="m",
        )
        assert result["tool_choice"] == {"type": "required"}

    def test_tool_choice_specific_tool(self):
        result = anthropic_to_cortex(
            {"messages": [], "max_tokens": 100, "tool_choice": {"type": "tool", "name": "read_file"}},
            model="m",
        )
        assert result["tool_choice"] == {"type": "tool", "name": ["read_file"]}

    def test_optional_params_omitted_when_absent(self):
        result = anthropic_to_cortex({"messages": [], "max_tokens": 100}, model="m")
        assert "temperature" not in result
        assert "top_p" not in result
        assert "tools" not in result
        assert "tool_choice" not in result
        assert "stream" not in result


class TestMessageConversion:
    def test_string_content_passthrough(self):
        result = anthropic_to_cortex(
            {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 100},
            model="m",
        )
        assert result["messages"][0] == {"role": "user", "content": "hello"}

    def test_single_text_block_flattened(self):
        result = anthropic_to_cortex(
            {
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                "max_tokens": 100,
            },
            model="m",
        )
        assert result["messages"][0] == {"role": "user", "content": "hello"}

    def test_tool_use_blocks_passed_via_content_list(self):
        result = anthropic_to_cortex(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Let me read that."},
                            {
                                "type": "tool_use",
                                "id": "toolu_123",
                                "name": "read_file",
                                "input": {"path": "/tmp/x"},
                            },
                        ],
                    }
                ],
                "max_tokens": 100,
            },
            model="m",
        )
        msg = result["messages"][0]
        assert msg["role"] == "assistant"
        assert "content_list" in msg
        assert len(msg["content_list"]) == 2
        assert msg["content_list"][0]["type"] == "text"
        assert msg["content_list"][1]["type"] == "tool_use"
        assert msg["content_list"][1]["id"] == "toolu_123"

    def test_tool_result_blocks_passed_via_content_list(self):
        result = anthropic_to_cortex(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": "toolu_123", "content": "file contents"},
                        ],
                    }
                ],
                "max_tokens": 100,
            },
            model="m",
        )
        msg = result["messages"][0]
        assert "content_list" in msg
        assert msg["content_list"][0]["type"] == "tool_result"
        assert msg["content_list"][0]["tool_use_id"] == "toolu_123"

    def test_thinking_blocks_stripped(self):
        result = anthropic_to_cortex(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "hmm..."},
                            {"type": "text", "text": "Answer."},
                        ],
                    }
                ],
                "max_tokens": 100,
            },
            model="m",
        )
        msg = result["messages"][0]
        assert msg == {"role": "assistant", "content": "Answer."}

    def test_redacted_thinking_stripped(self):
        result = anthropic_to_cortex(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "redacted_thinking", "data": "abc"},
                            {"type": "text", "text": "Answer."},
                        ],
                    }
                ],
                "max_tokens": 100,
            },
            model="m",
        )
        assert result["messages"][0] == {"role": "assistant", "content": "Answer."}


class TestCortexToAnthropic:
    def test_text_response(self):
        result = cortex_to_anthropic(
            {
                "id": "msg-123",
                "content": [{"type": "text", "text": "Hello!"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
            model="claude-sonnet-4-5",
        )
        assert result["id"] == "msg-123"
        assert result["type"] == "message"
        assert result["role"] == "assistant"
        assert result["model"] == "claude-sonnet-4-5"
        assert result["content"] == [{"type": "text", "text": "Hello!"}]
        assert result["stop_reason"] == "end_turn"
        assert result["usage"]["input_tokens"] == 10
        assert result["usage"]["output_tokens"] == 5

    def test_tool_use_response(self):
        result = cortex_to_anthropic(
            {
                "id": "msg-456",
                "content": [
                    {"type": "tool_use", "id": "toolu_abc", "name": "read_file", "input": {"path": "/tmp/x"}},
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 20, "output_tokens": 15},
            },
            model="m",
        )
        assert result["stop_reason"] == "tool_use"
        assert result["content"][0]["type"] == "tool_use"
        assert result["content"][0]["name"] == "read_file"

    def test_empty_response_gets_empty_text_block(self):
        result = cortex_to_anthropic(
            {"content": [], "stop_reason": "end_turn", "usage": {}},
            model="m",
        )
        assert result["content"] == [{"type": "text", "text": ""}]

    def test_mixed_content_passthrough(self):
        content = [
            {"type": "text", "text": "I'll read that."},
            {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"cmd": "ls"}},
        ]
        result = cortex_to_anthropic(
            {
                "content": content,
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
            model="m",
        )
        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "text"
        assert result["content"][1]["type"] == "tool_use"


class TestStreamAdapter:
    def test_simple_text_stream_oai_format(self):
        adapter = StreamAdapter("claude-sonnet-4-5")

        events = adapter.feed(
            {
                "id": "chatcmpl-1",
                "choices": [{"delta": {"content": "Hello"}, "finish_reason": None}],
            }
        )
        events += adapter.feed(
            {
                "choices": [{"delta": {"content": " world"}, "finish_reason": None}],
            }
        )
        events += adapter.feed(
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
        )
        events += adapter.finish()

        parsed = _parse_sse_list(events)
        types = [e["type"] for e in parsed]

        assert types == [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ]

        assert parsed[0]["message"]["model"] == "claude-sonnet-4-5"
        assert parsed[2]["delta"]["text"] == "Hello"
        assert parsed[3]["delta"]["text"] == " world"
        assert parsed[5]["delta"]["stop_reason"] == "end_turn"

    def test_anthropic_style_stream(self):
        adapter = StreamAdapter("m")

        events = adapter.feed(
            {
                "id": "msg-1",
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
                "delta": {},
                "usage": {"input_tokens": 10},
            }
        )
        events += adapter.feed(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"},
            }
        )
        events += adapter.feed(
            {
                "type": "content_block_stop",
                "index": 0,
                "delta": {},
            }
        )
        events += adapter.finish()

        parsed = _parse_sse_list(events)
        types = [e["type"] for e in parsed]
        assert "message_start" in types
        assert "content_block_start" in types
        assert "content_block_delta" in types
        assert "message_stop" in types

    def test_usage_tracking(self):
        adapter = StreamAdapter("m")

        adapter.feed(
            {
                "id": "chatcmpl-5",
                "choices": [{"delta": {"content": "hi"}, "finish_reason": None}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 0},
            }
        )
        adapter.feed(
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 5},
            }
        )
        finish_events = adapter.finish()

        parsed = _parse_sse_list(finish_events)
        msg_delta = next(e for e in parsed if e["type"] == "message_delta")
        assert msg_delta["usage"]["output_tokens"] == 5


class TestRoundTrip:
    def test_conversation_with_tool_use(self):
        anthropic_request = {
            "system": "You are a coding assistant.",
            "messages": [
                {"role": "user", "content": "Read /tmp/test.txt"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I'll read that file."},
                        {
                            "type": "tool_use",
                            "id": "toolu_abc",
                            "name": "read_file",
                            "input": {"path": "/tmp/test.txt"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_abc", "content": "file contents"},
                    ],
                },
            ],
            "max_tokens": 4096,
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ],
        }

        cortex = anthropic_to_cortex(anthropic_request, model="claude-sonnet-4-5")

        assert cortex["messages"][0]["role"] == "system"
        assert cortex["messages"][1]["role"] == "user"
        assert cortex["messages"][2]["role"] == "assistant"
        assert cortex["messages"][2]["content_list"][1]["type"] == "tool_use"
        assert cortex["messages"][3]["role"] == "user"
        assert cortex["messages"][3]["content_list"][0]["type"] == "tool_result"

        cortex_response = {
            "id": "msg-resp",
            "content": [{"type": "text", "text": "The file contains: file contents"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
        anthropic_response = cortex_to_anthropic(cortex_response, model="claude-sonnet-4-5")

        assert anthropic_response["content"][0]["text"] == "The file contains: file contents"
        assert anthropic_response["stop_reason"] == "end_turn"
        assert anthropic_response["usage"]["input_tokens"] == 100
