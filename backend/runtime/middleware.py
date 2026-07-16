"""Reusable middleware for the DeepAgents runtime graphs."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from deepagents.middleware.memory import MemoryMiddleware
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
from pydantic import BaseModel, ValidationError

from runtime import observability
from runtime.observability import MAIN_AGENT_NAME
from runtime.resources import RUNTIME_AGENTS_PATH
from skills.philipswgqinboundrecognition import PhilipsWgqRecognitionResult


NO_PROGRESS_WINDOW = 3

# Restricted MemoryMiddleware prompt: auto-load handbook; append only after tool failures.
RUNTIME_MEMORY_SYSTEM_PROMPT = """\
<agent_memory>
{agent_memory}
</agent_memory>

<memory_guidelines>
The handbook above is shared runtime tool-use guidance. Follow it for document/result consumption.

After a tool call fails and the failure is a reusable tool-misuse pattern, append one short entry to
`/memories/AGENTS.md` with `edit_file` using this shape:

### <tool_name>
- Error: <what failed>
- Next: <correct next step>

Only append verified tool-misuse patterns. Do not write business data, user preferences, secrets,
private paths, full file contents, or unverified guesses. Do not update the handbook for one-off
environment glitches or when no tool failed.
</memory_guidelines>
"""

__all__ = [
    "NO_PROGRESS_WINDOW",
    "RUNTIME_MEMORY_SYSTEM_PROMPT",
    "NoProgressLoop",
    "NoProgressMiddleware",
    "StructuredOutputCompatibility",
    "StructuredOutputRecovery",
    "ToolTelemetry",
    "runtime_middlewares",
]

_FENCED_JSON = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


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


class StructuredOutputRecovery(AgentMiddleware):
    """Recover ToolStrategy structured_response from plain-text JSON.

    MiniMax and similar models sometimes finish with a fenced JSON body instead
    of calling the schema tool. Harness only accepts ``structured_response`` from
    stream updates, so this middleware parses the latest AI text, validates it
    against the configured schema, and writes ``structured_response`` into state.

    Does not rewrite outcome (e.g. success + problems stays success). Does not
    invent fields. If parse/validation fails, returns None and lets the harness
    fail with ``structured_response missing``.
    """

    def __init__(self, schema: type[BaseModel] | None = None) -> None:
        super().__init__()
        self.schema = schema or PhilipsWgqRecognitionResult

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        del runtime
        if _state_get(state, "structured_response") is not None:
            return None
        messages = _messages_of(state)
        if not messages:
            return None
        latest = messages[-1]
        if not _is_ai_message(latest):
            return None
        if observability.tool_calls_of(latest):
            # Pending or structured tool_calls: leave ToolStrategy / tools path alone.
            return None
        text = _message_text(latest)
        if not text or not text.strip():
            return None
        payload = _extract_json_object(text)
        if payload is None:
            return None
        try:
            validated = self.schema.model_validate(payload)
        except ValidationError:
            return None
        return {"structured_response": validated}


def runtime_middlewares(*, memory_backend: Any | None = None) -> list[AgentMiddleware]:
    """Return fresh middleware instances for each agent graph.

    When ``memory_backend`` is set (main agent), attach built-in MemoryMiddleware
    with a restricted prompt so ``/memories/AGENTS.md`` is auto-loaded without the
    default user-preference memory semantics. Subagents omit memory_backend.

    Order notes (onion model):
    - Recovery is listed first so its ``after_model`` runs last among after hooks
      and can still fill ``structured_response`` after other layers run.
    - Compatibility wraps model calls (thinking off for ToolStrategy).
    """
    middleware: list[AgentMiddleware] = [
        StructuredOutputRecovery(),
        ToolTelemetry(),
        NoProgressMiddleware(),
        StructuredOutputCompatibility(),
    ]
    if memory_backend is not None:
        middleware.append(
            MemoryMiddleware(
                backend=memory_backend,
                sources=[RUNTIME_AGENTS_PATH],
                system_prompt=RUNTIME_MEMORY_SYSTEM_PROMPT,
            )
        )
    return middleware


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


def _state_get(state: Any, key: str) -> Any:
    if isinstance(state, dict):
        return state.get(key)
    return getattr(state, key, None)


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue
                # Some providers put body under type=text with nested fields only.
                if block.get("type") == "text" and isinstance(block.get("content"), str):
                    parts.append(block["content"])
        return "".join(parts)
    return "" if content is None else str(content)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first valid JSON object from fenced or raw assistant text."""
    candidates: list[str] = []
    for match in _FENCED_JSON.finditer(text):
        candidates.append(match.group(1))
    # Prefer the largest balanced {...} slice when fences are missing or partial.
    balanced = _largest_json_object_slice(text)
    if balanced is not None:
        candidates.append(balanced)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _largest_json_object_slice(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    last_end: int | None = None
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                last_end = index
                # Keep scanning for a later top-level close only if nested restarts;
                # first complete object from the first '{' is usually the payload.
                break
    if last_end is None:
        return None
    return text[start : last_end + 1]
