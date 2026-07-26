"""Reusable middleware for the DeepAgents runtime graphs."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Annotated, Any

from deepagents.middleware.memory import MemoryMiddleware
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
    hook_config,
)
from langchain.agents.middleware.types import OmitFromSchema
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import get_stream_writer
from pydantic import BaseModel, ValidationError
from typing_extensions import NotRequired

from runtime import observability
from runtime.observability import MAIN_AGENT_NAME
from runtime.resources import RUNTIME_AGENTS_PATH


NO_PROGRESS_WINDOW = 3
# How many after_model correction loops are allowed when text JSON fails validation
# or cannot be parsed. Each attempt jumps back to the model once.
DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES = 2
_RECOVERY_MODEL_SNIPPET_CHARS = 3000
_RECOVERY_ERROR_CHARS = 1500

# 受限 MemoryMiddleware 提示：自动加载手册；仅在工具失败后追加。
RUNTIME_MEMORY_SYSTEM_PROMPT = """\
<agent_memory>
{agent_memory}
</agent_memory>

<memory_guidelines>
上方手册是共享的运行时工具使用指引。处理文档与结果时请遵循。

工具调用失败且属于可复用的工具误用模式时，用 `edit_file` 向 `/memories/AGENTS.md` 追加一条短记录，格式如下：

### <tool_name>
- 错误: <失败现象>
- 下一步: <正确下一步>

只追加已验证的工具误用模式。不要写业务数据、用户偏好、密钥、私有路径、完整文件内容或未验证猜测。
一次性环境故障或未发生工具失败时，不要更新手册。
</memory_guidelines>
"""

__all__ = [
    "DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES",
    "NO_PROGRESS_WINDOW",
    "RUNTIME_MEMORY_SYSTEM_PROMPT",
    "NoProgressLoop",
    "NoProgressMiddleware",
    "StructuredOutputCompatibility",
    "StructuredOutputRecovery",
    "StructuredOutputRecoveryState",
    "ToolTelemetry",
    "is_empty_channel_data_shell",
    "runtime_middlewares",
]


_EMPTY_DATA_SHELL_HINT = (
    "结构化结果的 data 不能为空，必须包含完整 header 与 items。"
    "success/partial_success 需要非空 items；input_problems 可以使用空 items，但仍需完整 header 和问题说明。"
)

_FENCED_JSON = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


class StructuredOutputRecoveryState(AgentState):
    """Agent state extension for structured-output recovery retries."""

    structured_recovery_attempts: NotRequired[
        Annotated[int, OmitFromSchema(input=True, output=True)]
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

    Some models finish with fenced JSON instead of calling the configured schema
    tool. This node-style ``after_model`` hook validates that text and writes the
    configured schema instance to ``structured_response``.

    On parse/validation failure, append a correction HumanMessage and
    ``jump_to: "model"`` up to ``max_retries`` times (default 2). Exhausted
    non-shell failures explicitly ``jump_to: "end"`` so ToolStrategy cannot
    loop forever. Exhausted empty data shells become an all-null
    ``input_problems`` result for either channel schema.
    """

    state_schema = StructuredOutputRecoveryState

    def __init__(
        self,
        schema: type[BaseModel],
        *,
        max_retries: int = DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES,
    ) -> None:
        super().__init__()
        self.schema = schema
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.max_retries = max_retries

    @hook_config(can_jump_to=["model", "end"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        del runtime
        if _state_get(state, "structured_response") is not None:
            return None
        messages = _messages_of(state)
        if not messages:
            return None
        latest = messages[-1]
        if not _is_ai_message(latest):
            recovered = _validated_text_from_rejected_empty_call(messages, self.schema)
            if recovered is not None:
                return {
                    "structured_response": recovered,
                    "structured_recovery_attempts": 0,
                    "jump_to": "end",
                }
            # ToolStrategy validation failures end with ToolMessage(s). When the
            # rejected structured args were an empty data shell, coach a full
            # resubmit. Do not invent field values.
            empty_shell = _latest_empty_structured_shell(messages, self.schema)
            if empty_shell is not None:
                return self._retry_or_give_up(
                    state,
                    reason=_EMPTY_DATA_SHELL_HINT,
                    model_text=json.dumps(empty_shell, ensure_ascii=False, default=str),
                    empty_shell=True,
                )
            return None
        if observability.tool_calls_of(latest):
            # Pending tool_calls (including structured): leave ToolStrategy path.
            # Empty shells are handled after ToolStrategy emits the error ToolMessage.
            return None
        text = _message_text(latest)
        if not text or not text.strip():
            # Empty finish without structured_response: still use the bounded retry
            # path. Exhausted retries jump_to end so ToolStrategy cannot loop forever.
            return self._retry_or_give_up(
                state,
                reason="助手以空文本结束，且未产生 structured_response",
                model_text=text or "",
            )

        payload = _extract_json_object(text)
        if payload is not None:
            if is_empty_channel_data_shell(payload):
                return self._retry_or_give_up(
                    state,
                    reason=_EMPTY_DATA_SHELL_HINT,
                    model_text=text,
                    empty_shell=True,
                )
            try:
                validated = self.schema.model_validate(payload)
            except ValidationError as exc:
                return self._retry_or_give_up(
                    state,
                    reason=_format_validation_error(exc),
                    model_text=text,
                )
            return {
                "structured_response": validated,
                "structured_recovery_attempts": 0,
            }

        # Text finish without a parseable JSON object — ask the model to resubmit
        # via the schema tool or a single valid JSON object.
        return self._retry_or_give_up(
            state,
            reason="助手文本中未找到合法 JSON 对象",
            model_text=text,
        )

    def _retry_or_give_up(
        self,
        state: Any,
        *,
        reason: str,
        model_text: str,
        empty_shell: bool = False,
    ) -> dict[str, Any]:
        attempts = _recovery_attempts(state)
        if attempts >= self.max_retries:
            # Critical: with ToolStrategy and no tools, create_agent's
            # model_to_model edge re-enters the model whenever structured_response
            # is missing. Returning None would infinite-loop; jump to end instead.
            if empty_shell:
                return {
                    "structured_response": _empty_shell_fallback_result(self.schema),
                    "jump_to": "end",
                    "structured_recovery_attempts": attempts,
                }
            return {"jump_to": "end"}
        schema_name = getattr(self.schema, "__name__", "structured schema")
        return {
            "messages": [
                HumanMessage(
                    content=_build_recovery_retry_message(
                        reason=reason,
                        model_text=model_text,
                        schema_name=schema_name,
                        empty_shell=empty_shell,
                    )
                )
            ],
            "jump_to": "model",
            "structured_recovery_attempts": attempts + 1,
        }


def runtime_middlewares(
    *,
    memory_backend: Any | None = None,
    structured_schema: type[BaseModel] | None = None,
) -> list[AgentMiddleware]:
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
        *([StructuredOutputRecovery(structured_schema)] if structured_schema else []),
        ToolTelemetry(),
        NoProgressMiddleware(),
        StructuredOutputCompatibility(),
    ]
    if memory_backend is not None:
        # add_cache_control: second breakpoint on the memory block (official
        # memory= also sets this). Still sits in user middleware before
        # AnthropicPromptCachingMiddleware — not as optimal as create_deep_agent
        # tail placement, but avoids default memory= prompt semantics.
        middleware.append(
            MemoryMiddleware(
                backend=memory_backend,
                sources=[RUNTIME_AGENTS_PATH],
                system_prompt=RUNTIME_MEMORY_SYSTEM_PROMPT,
                add_cache_control=True,
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


def _recovery_attempts(state: Any) -> int:
    raw = _state_get(state, "structured_recovery_attempts")
    if isinstance(raw, int) and raw >= 0:
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 0


def _format_validation_error(exc: ValidationError) -> str:
    text = str(exc).strip()
    if len(text) > _RECOVERY_ERROR_CHARS:
        return text[:_RECOVERY_ERROR_CHARS] + "...[truncated]"
    return text


_EMPTY_SHELL_FALLBACK_PROBLEM: dict[str, str] = {
    "source": "runtime",
    "location": "structured_response",
    "issue": "model kept submitting empty data shell after recovery retries",
    "action": "returned schema-valid all-null input_problems result",
}


def _empty_shell_fallback_result(schema: type[BaseModel]) -> BaseModel:
    data_model = schema.model_fields["data"].annotation
    if not isinstance(data_model, type) or not issubclass(data_model, BaseModel):
        raise TypeError(f"{schema.__name__} data must be a Pydantic model")
    header_model = data_model.model_fields["header"].annotation
    if not isinstance(header_model, type) or not issubclass(header_model, BaseModel):
        raise TypeError(f"{schema.__name__} data.header must be a Pydantic model")
    return schema.model_validate(
        {
            "outcome": "input_problems",
            "data": {"header": {name: None for name in header_model.model_fields}, "items": []},
            "problems": [dict(_EMPTY_SHELL_FALLBACK_PROBLEM)],
        }
    )


def is_empty_channel_data_shell(payload: Any) -> bool:
    """True when a channel result omits the required ``data`` shape."""
    if not isinstance(payload, dict):
        return False
    outcome = payload.get("outcome")
    data = payload.get("data")
    if data is None or data == {}:
        return True
    if not isinstance(data, dict):
        return False
    for key in ("header", "items"):
        if key not in data:
            return True
    header = data.get("header")
    items = data.get("items")
    if not isinstance(header, dict) or not header:
        return True
    if items is None:
        return True
    if outcome in {"success", "partial_success"} and (
        not isinstance(items, list) or not items
    ):
        return True
    return False


def _schema_tool_name(schema: type[BaseModel]) -> str:
    return getattr(schema, "__name__", "structured schema")


def _rejected_empty_structured_call(
    messages: list[Any],
    schema: type[BaseModel],
) -> tuple[Any, dict[str, Any]] | None:
    """Return the AI message and empty schema args paired to the latest error.

    ToolStrategy appends a ToolMessage for a rejected structured call. The
    ``tool_call_id`` is the only safe link back to that AIMessage: scanning
    arbitrary JSON in older messages could recover a result for another call.
    """
    if not messages or not _is_tool_message(messages[-1]):
        return None
    tool_message = messages[-1]
    tool_call_id = _tool_message_call_id(tool_message)
    if tool_call_id is None:
        return None
    expected = _schema_tool_name(schema)
    tool_name = _tool_message_name(tool_message)
    if tool_name is not None and tool_name != expected:
        return None
    for message in reversed(messages[:-1]):
        if _is_human_message(message):
            break
        if not _is_ai_message(message):
            continue
        for tool_call in observability.tool_calls_of(message):
            if not isinstance(tool_call, dict):
                continue
            if (
                tool_call.get("id") != tool_call_id
                or tool_call.get("name") != expected
            ):
                continue
            args = tool_call.get("args")
            if isinstance(args, dict) and is_empty_channel_data_shell(args):
                return message, args
            return None
    return None


def _validated_text_from_rejected_empty_call(
    messages: list[Any],
    schema: type[BaseModel],
) -> BaseModel | None:
    """Recover only text paired to the empty structured call ToolStrategy rejected."""
    rejected = _rejected_empty_structured_call(messages, schema)
    if rejected is None:
        return None
    ai_message, _ = rejected
    payload = _extract_json_object(_message_text(ai_message))
    if payload is None:
        return None
    try:
        return schema.model_validate(payload)
    except ValidationError:
        return None


def _latest_empty_structured_shell(
    messages: list[Any],
    schema: type[BaseModel],
) -> dict[str, Any] | None:
    """Return empty structured args paired to the latest ToolStrategy error."""
    rejected = _rejected_empty_structured_call(messages, schema)
    return rejected[1] if rejected is not None else None


def _is_tool_message(message: Any) -> bool:
    if isinstance(message, dict):
        return message.get("role") == "tool" or message.get("type") == "tool"
    type_name = type(message).__name__
    if type_name == "ToolMessage":
        return True
    return getattr(message, "type", None) == "tool" or getattr(message, "role", None) == "tool"


def _tool_message_name(message: Any) -> str | None:
    if isinstance(message, dict):
        name = message.get("name")
        return name if isinstance(name, str) else None
    name = getattr(message, "name", None)
    return name if isinstance(name, str) else None


def _tool_message_call_id(message: Any) -> str | None:
    if isinstance(message, dict):
        tool_call_id = message.get("tool_call_id")
    else:
        tool_call_id = getattr(message, "tool_call_id", None)
    return tool_call_id if isinstance(tool_call_id, str) else None


def _build_recovery_retry_message(
    *,
    reason: str,
    model_text: str,
    schema_name: str,
    empty_shell: bool,
) -> str:
    snippet = model_text.strip()
    if len(snippet) > _RECOVERY_MODEL_SNIPPET_CHARS:
        snippet = snippet[:_RECOVERY_MODEL_SNIPPET_CHARS] + "\n...[truncated]"
    if empty_shell:
        lead = (
            f"结构化输出被拒绝：`{schema_name}` 的 data 为空或不完整。"
            "请重新提交完整 data.header 和 items；未知字段填 null。"
            "input_problems 可以使用空 items，但必须保留完整 header 和至少一个 problem。\n\n"
        )
    else:
        lead = (
            f"结构化输出被拒绝。请重新提交合法的 `{schema_name}`："
            "优先只通过结构化输出工具提交完整 args；"
            "仅无法形成有效工具调用时才输出一个符合 schema 的完整 JSON 对象。"
            "不要编造字段；未知填 null。仅自然语言摘要不能作为业务结果。\n\n"
        )
    return (
        f"{lead}"
        f"问题：\n{reason}\n\n"
        f"你之前的内容：\n{snippet}"
    )


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
