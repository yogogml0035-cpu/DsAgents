from __future__ import annotations

import json
from typing import Any, Callable, Protocol, Sequence

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command

from session import SessionStore


class Hands(Protocol):
    def middleware(self, session_id: str) -> Sequence[AgentMiddleware]: ...


class TraceHands:
    def __init__(self, sessions: SessionStore) -> None:
        self.sessions = sessions

    def middleware(self, session_id: str) -> Sequence[AgentMiddleware]:
        return [TraceMiddleware(session_id, self.sessions)]


class TraceMiddleware(AgentMiddleware):
    def __init__(self, session_id: str, sessions: SessionStore) -> None:
        super().__init__()
        self.session_id = session_id
        self.sessions = sessions

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        self.sessions.emit_event(self.session_id, "model_request", {"messages": request.messages})
        try:
            response = handler(request)
        except Exception as exc:
            self.sessions.emit_event(self.session_id, "model_error", {"error": repr(exc)})
            raise
        self.sessions.emit_event(self.session_id, "model_response", {"messages": response.result})
        print(f"[model] {_message_text(response.result[-1]) if response.result else ''}")
        return response

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        call = request.tool_call
        self.sessions.emit_event(
            self.session_id,
            "tool_request",
            {"name": call.get("name"), "args": call.get("args")},
        )
        try:
            result = handler(request)
        except Exception as exc:
            self.sessions.emit_event(
                self.session_id,
                "tool_error",
                {"name": call.get("name"), "error": repr(exc)},
            )
            raise
        self.sessions.emit_event(
            self.session_id,
            "tool_response",
            {"name": call.get("name"), "result": result},
        )
        print(f"[tool] {call.get('name')} completed")
        return result


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    return json.dumps(_safe(content), ensure_ascii=False)


def _safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
