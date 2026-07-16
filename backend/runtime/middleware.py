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
from skills.philipswgqinboundrecognition import PhilipsWgqRecognitionResult


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
    "EMPTY_DATA_SHELL_HINT",
    "NO_PROGRESS_WINDOW",
    "RUNTIME_MEMORY_SYSTEM_PROMPT",
    "NoProgressLoop",
    "NoProgressMiddleware",
    "StructuredOutputCompatibility",
    "StructuredOutputRecovery",
    "StructuredOutputRecoveryState",
    "ToolTelemetry",
    "is_empty_recognition_data_shell",
    "philips_structured_output_error_message",
    "runtime_middlewares",
]

# 模型提交 success/partial 却 data:{} 或缺 shipment/header/items 时的共享纠错文案。
# 供 ToolStrategy handle_errors 与 recovery 重试使用。
EMPTY_DATA_SHELL_HINT = (
    "PhilipsWgqRecognitionResult 无效：data 不能是 {}，也不能省略 shipment/header/items。"
    "outcome 为 success 或 partial_success 时，请用结构化工具重新提交完整嵌套对象："
    "shipment（pieces、total_gross_weight）、header（全部固定英文字段）、"
    "items（非空数组，每项为完整商品对象）。未知值填 null。"
    "outcome 为 input_problems 时，data 必须为 null（不能是 {}），且至少一条 problem。"
    "不要编造业务值；复用已从 PDF 与主数据查询得到的字段。禁止重复提交相同的空 data 壳。"
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

    On parse/validation failure, append a correction HumanMessage and
    ``jump_to: "model"`` up to ``max_retries`` times (default 2). Exhausted
    retries use ``jump_to: "end"`` so the agent graph exits without an infinite
    ToolStrategy re-generation loop; the harness then fails with
    ``structured_response missing``.

    Also intercepts empty ``data: {}`` structured tool attempts (ToolStrategy
    validation failures that only produce a generic ToolMessage). When the
    latest tool-error turn is an empty recognition shell, append a specific
    correction and jump back to the model (bounded by the same retry budget).

    Does not rewrite business outcome fields. Does not invent schema values.
    """

    state_schema = StructuredOutputRecoveryState

    def __init__(
        self,
        schema: type[BaseModel] | None = None,
        *,
        max_retries: int = DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES,
    ) -> None:
        super().__init__()
        self.schema = schema or PhilipsWgqRecognitionResult
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
            # ToolStrategy validation failures end with ToolMessage(s). When the
            # rejected structured args were an empty data shell, coach a full
            # resubmit. Do not invent field values.
            empty_shell = _latest_empty_structured_shell(messages, self.schema)
            if empty_shell is not None:
                return self._retry_or_give_up(
                    state,
                    reason=EMPTY_DATA_SHELL_HINT,
                    model_text=json.dumps(empty_shell, ensure_ascii=False, default=str),
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
            if is_empty_recognition_data_shell(payload):
                return self._retry_or_give_up(
                    state,
                    reason=EMPTY_DATA_SHELL_HINT,
                    model_text=text,
                )
            try:
                validated = self.schema.model_validate(payload)
            except ValidationError as exc:
                reason = _format_validation_error(exc)
                if _validation_indicates_empty_data(exc):
                    reason = f"{EMPTY_DATA_SHELL_HINT}\n\nValidator detail:\n{reason}"
                return self._retry_or_give_up(
                    state,
                    reason=reason,
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
    ) -> dict[str, Any]:
        attempts = _recovery_attempts(state)
        if attempts >= self.max_retries:
            # Critical: with ToolStrategy and no tools, create_agent's
            # model_to_model edge re-enters the model whenever structured_response
            # is missing. Returning None would infinite-loop; jump to end instead.
            return {"jump_to": "end"}
        schema_name = getattr(self.schema, "__name__", "structured schema")
        return {
            "messages": [
                HumanMessage(
                    content=_build_recovery_retry_message(
                        reason=reason,
                        model_text=model_text,
                        schema_name=schema_name,
                    )
                )
            ],
            "jump_to": "model",
            "structured_recovery_attempts": attempts + 1,
        }


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


def is_empty_recognition_data_shell(payload: Any) -> bool:
    """True when payload claims success/partial but data is {} or missing nested keys.

    Does not invent values. ``input_problems`` with ``data: null`` is not a shell.
    """
    if not isinstance(payload, dict):
        return False
    outcome = payload.get("outcome")
    if outcome not in {"success", "partial_success"}:
        return False
    data = payload.get("data")
    if data is None:
        return True
    if not isinstance(data, dict):
        return False
    if data == {}:
        return True
    # Partial shells: missing any required nested section.
    for key in ("shipment", "header", "items"):
        if key not in data:
            return True
    shipment = data.get("shipment")
    header = data.get("header")
    items = data.get("items")
    if isinstance(shipment, dict) and shipment == {}:
        return True
    if isinstance(header, dict) and header == {}:
        return True
    if items == [] or items is None:
        return True
    return False


def _validation_indicates_empty_data(exc: ValidationError) -> bool:
    """Heuristic: pydantic reported missing data.shipment/header/items on empty dict."""
    text = str(exc)
    missing = ("data.shipment" in text, "data.header" in text, "data.items" in text)
    if sum(1 for hit in missing if hit) >= 2:
        return True
    return "input_value={}" in text and "data" in text.lower()


def philips_structured_output_error_message(exc: Exception) -> str:
    """ToolStrategy handle_errors callback for PhilipsWgqRecognitionResult.

    Returns a specific empty-data coaching message when the failed tool args are
    an empty shell; otherwise the default LangChain-style fix prompt.
    """
    args = _structured_validation_error_args(exc)
    if args is not None and is_empty_recognition_data_shell(args):
        return f"错误：{EMPTY_DATA_SHELL_HINT}\n请修正后重试。"
    # 其他 schema 问题仍回传原始解析错误。
    return f"错误：{exc}\n请修正后重试。"


def _structured_validation_error_args(exc: Exception) -> dict[str, Any] | None:
    """Best-effort extract of rejected tool args from StructuredOutputValidationError."""
    ai_message = getattr(exc, "ai_message", None)
    if ai_message is None:
        return None
    tool_name = getattr(exc, "tool_name", None)
    for tool_call in observability.tool_calls_of(ai_message):
        if not isinstance(tool_call, dict):
            continue
        if tool_name is not None and tool_call.get("name") != tool_name:
            continue
        args = tool_call.get("args")
        if isinstance(args, dict):
            return args
    return None


def _schema_tool_name(schema: type[BaseModel]) -> str:
    return getattr(schema, "__name__", "structured schema")


def _empty_structured_shell_from_ai(
    message: Any,
    schema: type[BaseModel],
) -> dict[str, Any] | None:
    """Return empty-shell structured tool args from an AI message, if any."""
    expected = _schema_tool_name(schema)
    for tool_call in observability.tool_calls_of(message):
        if not isinstance(tool_call, dict):
            continue
        if tool_call.get("name") != expected:
            continue
        args = tool_call.get("args")
        if isinstance(args, dict) and is_empty_recognition_data_shell(args):
            return args
    return None


def _latest_empty_structured_shell(
    messages: list[Any],
    schema: type[BaseModel],
) -> dict[str, Any] | None:
    """Find empty Philips structured args in the current turn after a tool error.

    Scans newest-first until a human message. Requires a recent ToolMessage that
    looks like a structured-output validation error, then the AI tool call shell.
    """
    saw_structured_error = False
    for message in reversed(messages):
        if _is_human_message(message):
            break
        if _is_tool_message(message):
            text = _message_text(message)
            name = _tool_message_name(message)
            if name == _schema_tool_name(schema) or _looks_like_structured_parse_error(text):
                saw_structured_error = True
            continue
        if _is_ai_message(message):
            if not saw_structured_error:
                return None
            return _empty_structured_shell_from_ai(message, schema)
    return None


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


def _looks_like_structured_parse_error(text: str) -> bool:
    lowered = text.lower()
    return (
        "failed to parse structured output" in lowered
        or "please fix your mistakes" in lowered
        or "请修正后重试" in text
        or "data 不能是 {}" in text
        or "data must not be {}" in lowered
        or "data.shipment" in lowered
    )


def _build_recovery_retry_message(
    *,
    reason: str,
    model_text: str,
    schema_name: str,
) -> str:
    snippet = model_text.strip()
    if len(snippet) > _RECOVERY_MODEL_SNIPPET_CHARS:
        snippet = snippet[:_RECOVERY_MODEL_SNIPPET_CHARS] + "\n...[truncated]"
    empty_shell = (
        "data 不能是 {}" in reason
        or "data must not be {}" in reason
        or "data: {}" in reason
    )
    if empty_shell:
        lead = (
            f"结构化输出被拒绝：`{schema_name}` 使用了空的 `data` 壳。"
            "请再次调用结构化工具，提交完整嵌套的 `shipment`、`header` 与非空 `items`。"
            "不要编造业务值；未知填 null。禁止再次提交 data: {}。\n\n"
        )
    else:
        lead = (
            f"结构化输出被拒绝。请重新提交合法的 `{schema_name}`："
            "优先调用结构化输出工具，或输出恰好一个符合 schema 的 JSON 对象。"
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
