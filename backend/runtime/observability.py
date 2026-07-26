"""Pure content/metadata extractors that turn streamed langgraph chunks into
observability payloads. No I/O, no run-state mutation — used by execution.py."""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk

MAIN_AGENT_NAME = "dsagents-main"
# Stage 1 records usage for MiniMax-M3 only; this constant is the model name
# written on every model_usage event. All model calls go through the same model.
MAIN_AGENT_MODEL = "MiniMax-M3"


def chunk_agent(data: Any) -> tuple[str, str]:
    """Return (scope, agent_name) for a messages-mode chunk's metadata.

    scope is "subagent" for declared subagent chunks and "main_agent" otherwise,
    matching the text-filter boundary. agent_name is the lc_agent_name for
    subagents and MAIN_AGENT_NAME for the main agent.
    """
    if isinstance(data, tuple) and len(data) >= 2 and isinstance(data[1], dict):
        name = data[1].get("lc_agent_name")
        if isinstance(name, str) and name not in {"", MAIN_AGENT_NAME}:
            return "subagent", name
    return "main_agent", MAIN_AGENT_NAME


def is_subagent_message(data: Any) -> bool:
    return chunk_agent(data)[0] == "subagent"


def is_assistant_message(data: Any) -> bool:
    """Whether a messages-mode chunk belongs to an assistant response."""
    message = data[0] if isinstance(data, tuple) and data else data
    if isinstance(message, (AIMessage, AIMessageChunk)):
        return True
    if isinstance(message, dict):
        return (message.get("role") or message.get("type")) in {
            "assistant",
            "ai",
            "AIMessage",
            "AIMessageChunk",
        }
    return (getattr(message, "role", None) or getattr(message, "type", None)) in {
        "assistant",
        "ai",
        "AIMessage",
        "AIMessageChunk",
    }


def model_usage(data: Any) -> dict[str, Any] | None:
    """Normalize a streamed chunk's usage_metadata into a model_usage payload.

    langchain_anthropic attaches usage_metadata only on the terminal
    message_delta chunk, so a non-None result is emitted exactly once per model
    call. cache token details live under input_token_details; the generic
    cache_creation is forced to 0 by the library when the 5m/1h breakdown is
    present, so summing them is safe.
    """
    message = data[0] if isinstance(data, tuple) and data else data
    metadata = getattr(message, "usage_metadata", None)
    if not metadata:
        return None
    details = metadata.get("input_token_details") or {}
    cache_creation = _usage_int(details.get("cache_creation")) + _usage_int(
        details.get("ephemeral_5m_input_tokens")
    ) + _usage_int(details.get("ephemeral_1h_input_tokens"))
    scope, agent_name = chunk_agent(data)
    return {
        "model": MAIN_AGENT_MODEL,
        "scope": scope,
        "agent_name": agent_name,
        "input_tokens": _usage_int(metadata.get("input_tokens")),
        "output_tokens": _usage_int(metadata.get("output_tokens")),
        "cache_read_input_tokens": _usage_int(details.get("cache_read")),
        "cache_creation_input_tokens": cache_creation,
    }


def thinking_delta(data: Any) -> str:
    message = data[0] if isinstance(data, tuple) and data else data
    if isinstance(message, dict):
        event = message.get("event")
        if event and not str(event).endswith("delta"):
            return ""
        return (
            thinking_text(message.get("delta"))
            or thinking_text(message.get("content"))
            or thinking_text(message)
        )
    return thinking_text(getattr(message, "content_blocks", None)) or thinking_text(
        message_content(message)
    )


def message_delta(data: Any) -> str:
    message = data[0] if isinstance(data, tuple) and data else data
    if isinstance(message, dict):
        event = message.get("event")
        if event and not str(event).endswith("delta"):
            return ""
        return content_text(message.get("delta")) or content_text(message.get("content")) or content_text(message)
    return content_text(message_content(message))


def assistant_message_payload(message: Any, *, tool_calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build an assistant_message payload from a terminal assistant message.

    Returns None when the message carries tool calls (no standalone text to emit)
    or has no usable text/id. The thinking block, if any, is attached alongside.
    """
    if tool_calls:
        return None
    message_id = message_id_of(message)
    content = message_content(message)
    text = content_text(content).strip()
    if not isinstance(message_id, str) or not text:
        return None
    payload: dict[str, Any] = {"message_id": message_id}
    thinking_block = last_thinking_block(content)
    if thinking_block:
        thinking = content_text(thinking_block.get("thinking")) or content_text(thinking_block.get("text"))
        if thinking:
            payload["thinking"] = thinking
    payload["text"] = text
    return payload


def tool_calls_of(message: Any) -> list[dict[str, Any]]:
    if isinstance(message, dict):
        tool_calls = message.get("tool_calls")
    else:
        tool_calls = getattr(message, "tool_calls", None)
    if not isinstance(tool_calls, list):
        return []
    return [tool_call for tool_call in tool_calls if isinstance(tool_call, dict)]


def tool_call_payload(message: Any, tool_call: dict[str, Any]) -> dict[str, Any] | None:
    mid = message_id_of(message)
    tool_call_id = tool_call.get("id")
    name = tool_call.get("name")
    if not isinstance(mid, str) or not isinstance(tool_call_id, str) or not isinstance(name, str):
        return None
    args = tool_call.get("args")
    return {
        "message_id": mid,
        "tool_call_id": tool_call_id,
        "name": name,
        "args": args if isinstance(args, dict) else {},
    }


def message_id_of(message: Any) -> str | None:
    if isinstance(message, dict):
        value = message.get("id")
    else:
        value = getattr(message, "id", None)
    return value if isinstance(value, str) and value else None


def message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", message)


def content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return (
            content_text(content.get("text"))
            or content_text(content.get("delta"))
            or content_text(content.get("content"))
        )
    if isinstance(content, list):
        return "".join(content_text(item) for item in content)
    return ""


def thinking_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, dict):
        block_type = content.get("type")
        if block_type == "thinking":
            return content_text(content.get("thinking")) or content_text(content.get("text"))
        if block_type == "reasoning":
            return content_text(content.get("reasoning")) or content_text(content.get("text"))
        if block_type == "non_standard":
            return thinking_text(content.get("value"))
        return thinking_text(content.get("delta")) or thinking_text(content.get("content"))
    if isinstance(content, list):
        return "".join(thinking_text(item) for item in content)
    return ""


def last_thinking_block(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict):
        if content.get("type") == "thinking":
            return content
        return last_thinking_block(content.get("delta")) or last_thinking_block(content.get("content"))
    if isinstance(content, list):
        for item in reversed(content):
            block = last_thinking_block(item)
            if block is not None:
                return block
    return None


def stream_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"value": value}


def _usage_int(value: Any) -> int:
    return value if isinstance(value, int) else 0
