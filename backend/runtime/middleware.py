"""Reusable middleware for the DeepAgents runtime graphs."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import get_stream_writer

from runtime import observability
from runtime.observability import MAIN_AGENT_NAME


NO_PROGRESS_WINDOW = 3

__all__ = [
    "NO_PROGRESS_WINDOW",
    "NoProgressLoop",
    "NoProgressMiddleware",
    "StructuredOutputCompatibility",
    "ToolTelemetry",
    "runtime_middlewares",
]


class NoProgressLoop(Exception):
    """Raised when the agent repeats the same tool call without progress."""


class ToolTelemetry(AgentMiddleware):
    """Emit tool start/complete/error events with timing and agent scope."""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Any,
    ) -> Any:
        call = request.tool_call
        name = call.get("name")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        agent_name = _runtime_agent_name(request)
        writer = _safe_writer()
        started_at = time.monotonic()
        _emit(
            writer,
            {
                "name": name,
                "agent_name": agent_name,
                "status": "started",
                "args": args,
            },
        )
        try:
            result = handler(request)
        except Exception:
            _emit(
                writer,
                {
                    "name": name,
                    "agent_name": agent_name,
                    "status": "error",
                    "duration_ms": int((time.monotonic() - started_at) * 1000),
                },
            )
            raise
        _emit(
            writer,
            {
                "name": name,
                "agent_name": agent_name,
                "status": "completed",
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "result": str(result)[:200],
            },
        )
        return result


class NoProgressMiddleware(AgentMiddleware):
    """Detect repeated tool calls after the latest human message.

    The decision is derived from the graph's message state on every model turn
    instead of being stored on the middleware instance.  A compiled graph can
    be invoked more than once, and instance-local mutable state would otherwise
    leak between threads or concurrent invocations.
    """

    def before_model(self, state: Any, runtime: Any) -> None:
        del runtime
        messages = _messages_of(state)
        recent = _recent_tool_call_tokens(messages)
        if len(recent) < NO_PROGRESS_WINDOW:
            return
        if len(set(recent[:NO_PROGRESS_WINDOW])) == 1:
            raise NoProgressLoop(
                f"no_progress_loop: repeated {recent[0]} "
                f"{NO_PROGRESS_WINDOW} times"
            )


class StructuredOutputCompatibility(AgentMiddleware):
    """Disable thinking only for ToolStrategy model requests."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        if (
            isinstance(request.response_format, ToolStrategy)
            and isinstance(request.model, BaseChatModel)
            and getattr(request.model, "thinking", None) is not None
        ):
            request = request.override(
                model=request.model.model_copy(update={"thinking": None})
            )
        return handler(request)


def runtime_middlewares() -> list[AgentMiddleware]:
    """Return fresh middleware instances for each agent graph."""
    return [
        ToolTelemetry(),
        NoProgressMiddleware(),
        StructuredOutputCompatibility(),
    ]


def _runtime_agent_name(request: ToolCallRequest) -> str:
    runtime = getattr(request, "runtime", None)
    config = getattr(runtime, "config", None)
    metadata = config.get("metadata") if isinstance(config, dict) else None
    name = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
    return name if isinstance(name, str) and name else MAIN_AGENT_NAME


def _safe_writer() -> Any:
    try:
        return get_stream_writer()
    except (KeyError, RuntimeError):
        return None


def _emit(writer: Any, payload: dict[str, Any]) -> None:
    if writer is not None:
        writer(payload)


def _tool_call_token(tool_call: dict[str, Any]) -> str | None:
    name = tool_call.get("name")
    args = tool_call.get("args")
    if not isinstance(name, str) or not isinstance(args, dict):
        return None
    return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"


def _messages_of(state: Any) -> list[Any]:
    messages = (
        state.get("messages", [])
        if isinstance(state, dict)
        else getattr(state, "messages", [])
    )
    return messages if isinstance(messages, list) else []


def _recent_tool_call_tokens(messages: list[Any]) -> list[str]:
    """Return tool calls in the current turn, newest first.

    Tool result messages are intentionally skipped.  A non-tool assistant
    message marks progress and ends the scan, while a human message starts a
    new turn and also ends it.
    """
    recent: list[str] = []
    for message in reversed(messages):
        if _is_human_message(message):
            break
        tool_calls = observability.tool_calls_of(message)
        if tool_calls:
            for tool_call in reversed(tool_calls):
                token = _tool_call_token(tool_call)
                if token is not None:
                    recent.append(token)
            continue
        if _is_ai_message(message):
            break
    return recent


def _is_human_message(message: Any) -> bool:
    if isinstance(message, HumanMessage):
        return True
    if isinstance(message, dict):
        return message.get("role") in {"user", "human"} or message.get("type") == "human"
    return (
        getattr(message, "type", None) == "human"
        or getattr(message, "role", None) == "user"
    )


def _is_ai_message(message: Any) -> bool:
    if isinstance(message, AIMessage):
        return True
    if isinstance(message, dict):
        return message.get("role") in {"assistant", "ai"} or message.get("type") == "ai"
    return (
        getattr(message, "type", None) == "ai"
        or getattr(message, "role", None) == "assistant"
    )
