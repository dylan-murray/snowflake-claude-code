"""Format translators between Anthropic Messages API and Snowflake Cortex
Inference API, including a streaming adapter for SSE passthrough.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

MIN_MAX_TOKENS = 32

_DATE_SUFFIX = re.compile(r"-\d{8}$")
_LONG_CONTEXT_SUFFIX = "[1m]"

_STOP_REASON_MAP = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "end_turn",
}


def normalize_model(model: str) -> str:
    base = model.removesuffix(_LONG_CONTEXT_SUFFIX)
    return _DATE_SUFFIX.sub("", base)


def anthropic_to_cortex(body: dict[str, Any], model: str) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []

    if system := body.get("system"):
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            # Keep each text block separate so per-block cache_control markers
            # survive. Anthropic and Cortex both use ephemeral cache breakpoints
            # placed on the last few blocks of long prompts; joining would drop
            # them and force a full recompute every turn.
            text_blocks = [b for b in system if b.get("type") == "text"]
            if any(b.get("cache_control") for b in text_blocks):
                messages.append(
                    {
                        "role": "system",
                        "content_list": [_convert_content_block(b, {}) for b in text_blocks],
                    }
                )
            else:
                joined = "\n".join(b.get("text", "") for b in text_blocks)
                messages.append({"role": "system", "content": joined})

    # Cortex's tool_results block requires the tool's name, which Anthropic's
    # tool_result block doesn't carry. Walk earlier tool_use blocks to build
    # an id → name map that tool_result entries can look up.
    tool_names: dict[str, str] = {}
    for msg in body.get("messages", []):
        messages.append(_convert_message(msg, tool_names))

    result: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }

    if (max_tokens := body.get("max_tokens")) is not None:
        result["max_tokens"] = max(max_tokens, MIN_MAX_TOKENS)
    if (temperature := body.get("temperature")) is not None:
        result["temperature"] = temperature
    if (top_p := body.get("top_p")) is not None:
        result["top_p"] = top_p
    if body.get("stream"):
        result["stream"] = True
    if tools := body.get("tools"):
        result["tools"] = [_convert_tool(t) for t in tools]
    if (tool_choice := body.get("tool_choice")) is not None:
        result["tool_choice"] = _convert_tool_choice(tool_choice)

    return result


def cortex_to_anthropic(response: dict[str, Any], model: str) -> dict[str, Any]:
    content = response.get("content", [])
    usage = response.get("usage", {})

    if not content:
        content = [{"type": "text", "text": ""}]

    return {
        "id": response.get("id", _msg_id()),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": _map_stop_reason(response.get("stop_reason")),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        },
    }


class StreamAdapter:
    def __init__(self, model: str) -> None:
        self._model = model
        self._block_index = 0
        self._in_text_block = False
        self._open_tool_blocks: list[int] = []
        self._tool_indices: dict[int, int] = {}
        self._active_cortex_tool_id: str | None = None
        self._started = False
        self._input_tokens = 0
        self._output_tokens = 0
        self._cached_tokens = 0
        self._stop_reason: str | None = None

    def feed(self, data: dict[str, Any]) -> list[str]:
        events: list[str] = []

        if not self._started:
            self._started = True
            usage = data.get("usage", {})
            self._input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
            self._cached_tokens = usage.get("cache_read_input_tokens", _extract_cached_tokens(usage))
            events.append(
                sse_event(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": data.get("id", _msg_id()),
                            "type": "message",
                            "role": "assistant",
                            "model": self._model,
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {
                                "input_tokens": self._input_tokens,
                                "output_tokens": 0,
                                "cache_read_input_tokens": self._cached_tokens,
                            },
                        },
                    },
                )
            )

        if "choices" in data:
            for choice in data["choices"]:
                delta = choice.get("delta", {})
                if delta.get("type") == "tool_use":
                    events.extend(self._process_cortex_tool_delta(delta))
                else:
                    events.extend(self._process_oai_delta(delta, choice.get("finish_reason")))
        elif "delta" in data:
            events.extend(self._process_anthropic_delta(data))

        if usage := data.get("usage"):
            self._input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", self._input_tokens))
            self._output_tokens = usage.get(
                "output_tokens", usage.get("completion_tokens", self._output_tokens)
            )
            self._cached_tokens = usage.get(
                "cache_read_input_tokens", _extract_cached_tokens(usage, self._cached_tokens)
            )

        return events

    def finish(self) -> list[str]:
        events: list[str] = []

        if self._in_text_block:
            events.append(
                sse_event("content_block_stop", {"type": "content_block_stop", "index": self._block_index})
            )
            self._in_text_block = False

        events.extend(self._close_open_tool_blocks())

        events.append(
            sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": self._stop_reason or "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": self._output_tokens},
                },
            )
        )
        events.append(sse_event("message_stop", {"type": "message_stop"}))
        return events

    def _process_anthropic_delta(self, data: dict[str, Any]) -> list[str]:
        events: list[str] = []
        event_type = data.get("type", "")
        delta = data.get("delta", {})

        if event_type == "content_block_start":
            block = data.get("content_block", {})
            idx = data.get("index", self._block_index)
            if self._in_text_block and block.get("type") != "text":
                events.append(
                    sse_event(
                        "content_block_stop", {"type": "content_block_stop", "index": self._block_index}
                    )
                )
                self._in_text_block = False
            events.append(sse_event("content_block_start", data))
            if block.get("type") == "text":
                self._in_text_block = True
                self._block_index = idx
            elif block.get("type") == "tool_use":
                self._open_tool_blocks.append(idx)
                self._block_index = idx + 1
        elif event_type == "content_block_delta":
            events.append(sse_event("content_block_delta", data))
        elif event_type == "content_block_stop":
            idx = data.get("index", self._block_index)
            if self._in_text_block and idx == self._block_index:
                self._in_text_block = False
            self._open_tool_blocks = [b for b in self._open_tool_blocks if b != idx]
            events.append(sse_event("content_block_stop", data))
        elif event_type == "message_delta":
            self._stop_reason = delta.get("stop_reason")
            if out := delta.get("usage", {}).get("output_tokens"):
                self._output_tokens = out

        if stop := data.get("stop_reason"):
            self._stop_reason = _map_stop_reason(stop)

        return events

    def _process_oai_delta(self, delta: dict[str, Any], finish_reason: str | None) -> list[str]:
        events: list[str] = []

        if content := delta.get("content"):
            if not self._in_text_block:
                # Close any open tool blocks before opening a new text block.
                # Cortex will stream text *after* a tool_use within the same
                # turn (Claude narrates, then calls another tool, etc.), and
                # Claude Code rejects SSE where content blocks overlap.
                events.extend(self._close_open_tool_blocks())
                self._in_text_block = True
                events.append(
                    sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": self._block_index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                )
            events.append(
                sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self._block_index,
                        "delta": {"type": "text_delta", "text": content},
                    },
                )
            )

        if tool_calls := delta.get("tool_calls"):
            for tc in tool_calls:
                events.extend(self._process_tool_call_delta(tc))

        if finish_reason:
            self._stop_reason = _map_stop_reason(finish_reason)
            if self._in_text_block:
                events.append(
                    sse_event(
                        "content_block_stop", {"type": "content_block_stop", "index": self._block_index}
                    )
                )
                self._in_text_block = False

        return events

    def _process_tool_call_delta(self, tc: dict[str, Any]) -> list[str]:
        events: list[str] = []
        tc_index = tc.get("index", 0)
        func = tc.get("function") or {}

        if tc_index not in self._tool_indices:
            if self._in_text_block:
                events.append(
                    sse_event(
                        "content_block_stop", {"type": "content_block_stop", "index": self._block_index}
                    )
                )
                self._block_index += 1
                self._in_text_block = False

            self._tool_indices[tc_index] = self._block_index
            self._open_tool_blocks.append(self._block_index)
            events.append(
                sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": self._block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tc.get("id", _tool_id()),
                            "name": func.get("name", ""),
                            "input": {},
                        },
                    },
                )
            )
            self._block_index += 1

        if args := func.get("arguments"):
            block_idx = self._tool_indices[tc_index]
            events.append(
                sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block_idx,
                        "delta": {"type": "input_json_delta", "partial_json": args},
                    },
                )
            )

        return events

    def _process_cortex_tool_delta(self, delta: dict[str, Any]) -> list[str]:
        """Translate Snowflake Cortex's own tool_use streaming shape into Anthropic SSE.

        Cortex sends:
          1. `{"type": "tool_use", "tool_use_id": "...", "name": "..."}` — the tool declaration
          2. `{"type": "tool_use", "input": "<partial json chunk>"}` — input pieces (no id)

        We emit the matching Anthropic events: `content_block_start` for (1), then
        `content_block_delta` with `input_json_delta` for each (2), keyed by the
        tool id we cached when (1) arrived.
        """
        events: list[str] = []
        tool_id = delta.get("tool_use_id")
        tool_name = delta.get("name")
        input_chunk = delta.get("input")

        # Declaration event: has tool_use_id + name, no input yet.
        if tool_id and tool_name and tool_id not in self._tool_indices:
            if self._in_text_block:
                events.append(
                    sse_event(
                        "content_block_stop", {"type": "content_block_stop", "index": self._block_index}
                    )
                )
                self._block_index += 1
                self._in_text_block = False

            # Close any previous tool blocks so we don't overlap.
            events.extend(self._close_open_tool_blocks())

            self._tool_indices[tool_id] = self._block_index
            self._open_tool_blocks.append(self._block_index)
            self._active_cortex_tool_id = tool_id
            self._stop_reason = "tool_use"
            events.append(
                sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": self._block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": tool_name,
                            "input": {},
                        },
                    },
                )
            )
            self._block_index += 1

        # Input chunk event: has `input`, routes to the currently-active tool block.
        if input_chunk:
            active_id = tool_id or self._active_cortex_tool_id
            if active_id and active_id in self._tool_indices:
                block_idx = self._tool_indices[active_id]
                events.append(
                    sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": block_idx,
                            "delta": {"type": "input_json_delta", "partial_json": input_chunk},
                        },
                    )
                )

        return events

    def _close_open_tool_blocks(self) -> list[str]:
        """Emit content_block_stop events for every still-open tool block and clear state."""
        events: list[str] = []
        for block_idx in self._open_tool_blocks:
            events.append(sse_event("content_block_stop", {"type": "content_block_stop", "index": block_idx}))
        if self._open_tool_blocks:
            self._block_index = max(self._block_index, max(self._open_tool_blocks) + 1)
        self._open_tool_blocks = []
        self._active_cortex_tool_id = None
        return events


# -- Message conversion (Anthropic → Snowflake Cortex) --


def _convert_message(msg: dict[str, Any], tool_names: dict[str, str]) -> dict[str, Any]:
    role = msg["role"]
    content = msg.get("content")

    if isinstance(content, str):
        return {"role": role, "content": content}

    if not isinstance(content, list):
        return {"role": role, "content": content}

    filtered = [
        _convert_content_block(b, tool_names)
        for b in content
        if b.get("type") not in ("thinking", "redacted_thinking")
    ]

    if len(filtered) == 1 and filtered[0].get("type") == "text":
        return {"role": role, "content": filtered[0]["text"]}

    return {"role": role, "content_list": filtered}


def _convert_content_block(block: dict[str, Any], tool_names: dict[str, str]) -> dict[str, Any]:
    """Reshape Anthropic content blocks into Cortex's nested content_list shape.

    Cortex schemas (from the generated SDK):
      - tool_use:   {"type": "tool_use", "tool_use": {"tool_use_id", "name", "input"}}
      - tool_results (plural):
                    {"type": "tool_results", "tool_results": {"tool_use_id", "name", "content"}}

    Cortex requires ``name`` on tool_results, so ``tool_names`` threads the
    id → name map earlier tool_use blocks established through the conversation.

    ``cache_control`` markers (ephemeral prompt caching) are preserved on every
    block type that supports them — text, tool_use, tool_results, image.
    Dropping these turns every turn into a full-prompt cache miss and balloons
    latency; keeping them lets Cortex reuse the cached prefix.
    """
    block_type = block.get("type")
    cache_control = block.get("cache_control")

    if block_type == "tool_use":
        tool_use_id = block.get("id") or block.get("tool_use_id", "")
        name = block.get("name", "")
        if tool_use_id and name:
            tool_names[tool_use_id] = name
        out: dict[str, Any] = {
            "type": "tool_use",
            "tool_use": {
                "tool_use_id": tool_use_id,
                "name": name,
                "input": block.get("input") or {},
            },
        }
        if cache_control:
            out["cache_control"] = cache_control
        return out

    if block_type == "tool_result":
        tool_use_id = block.get("tool_use_id", "")
        payload = block.get("content", "")
        if isinstance(payload, str):
            content_blocks = [{"type": "text", "text": payload}]
        elif isinstance(payload, list):
            content_blocks = [_convert_tool_result_entry(p) for p in payload]
        else:
            content_blocks = [{"type": "text", "text": str(payload)}]
        out = {
            "type": "tool_results",
            "tool_results": {
                "tool_use_id": tool_use_id,
                "name": tool_names.get(tool_use_id, ""),
                "content": content_blocks,
            },
        }
        if cache_control:
            out["cache_control"] = cache_control
        return out

    if block_type == "text":
        out = {"type": "text", "text": block.get("text", "")}
        if cache_control:
            out["cache_control"] = cache_control
        return out

    return block


def _convert_tool_result_entry(entry: Any) -> dict[str, Any]:
    """Normalize a single entry inside an Anthropic tool_result.content list."""
    if isinstance(entry, dict):
        entry_type = entry.get("type")
        if entry_type == "text":
            return {"type": "text", "text": entry.get("text", "")}
        if entry_type == "image":
            # Pass image blocks through unchanged; Cortex accepts the Anthropic shape.
            return entry
    return {"type": "text", "text": str(entry)}


# -- Tool schema conversion --


def _convert_tool(tool: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "tool_spec": {
            "type": "generic",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("input_schema", {}),
        },
    }
    # Forward cache_control so Cortex can reuse the cached prefix of the tool
    # catalog between turns. Claude Code typically marks the last tool in the
    # list for ephemeral caching.
    if cache_control := tool.get("cache_control"):
        out["cache_control"] = cache_control
    return out


def _convert_tool_choice(choice: Any) -> Any:
    if isinstance(choice, dict):
        tc_type = choice.get("type")
        if tc_type == "auto":
            return {"type": "auto"}
        if tc_type == "any":
            return {"type": "required"}
        if tc_type == "tool":
            return {"type": "tool", "name": [choice["name"]]}
    return {"type": "auto"}


# -- Helpers --


def _map_stop_reason(reason: str | None) -> str:
    return _STOP_REASON_MAP.get(reason or "end_turn", "end_turn")


def _extract_cached_tokens(usage: dict[str, Any], default: int = 0) -> int:
    return (usage.get("prompt_tokens_details") or {}).get("cached_tokens", default)


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _msg_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _tool_id() -> str:
    return f"toolu_{uuid.uuid4().hex[:24]}"
