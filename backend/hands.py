from __future__ import annotations

import json
from typing import Any, Callable, Protocol, Sequence

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools.tool_node import ToolCallRequest
from langgraph.config import get_stream_writer

from session import SessionStore


class Hands(Protocol):
    def middleware(self, session_id: str, run_id: str | None = None) -> Sequence[AgentMiddleware]: ...


class TraceHands:
    def __init__(self, sessions: SessionStore) -> None:
        self.sessions = sessions

    def middleware(self, session_id: str, run_id: str | None = None) -> Sequence[AgentMiddleware]:
        return [TraceMiddleware(session_id, self.sessions, run_id=run_id)]


class TraceMiddleware(AgentMiddleware):
    def __init__(self, session_id: str, sessions: SessionStore, run_id: str | None = None) -> None:
        super().__init__()
        self.session_id = session_id
        self.sessions = sessions
        self.run_id = run_id

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        payload = {"messages": request.messages}
        self.sessions.emit_event(self.session_id, "model_request", payload)
        self._emit_run_event("model_request", {"message_count": len(request.messages)}, raw=payload)
        try:
            response = handler(request)
        except Exception as exc:
            payload = {"error": repr(exc)}
            self.sessions.emit_event(self.session_id, "model_error", payload)
            self._emit_run_event("model_error", payload, raw=payload)
            raise
        payload = {"messages": response.result}
        self.sessions.emit_event(self.session_id, "model_response", payload)
        self._emit_run_event("model_response", {"message_count": len(response.result)}, raw=payload)
        print(f"[model] {_message_text(response.result[-1]) if response.result else ''}")
        return response

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        call = request.tool_call
        writer = _stream_writer()
        _emit_tool_status(writer, {"name": call.get("name"), "status": "started"})
        payload = {"name": call.get("name"), "args": call.get("args")}
        self.sessions.emit_event(self.session_id, "tool_request", payload)
        self._emit_run_event("tool_request", {"name": call.get("name")}, raw=payload)
        try:
            result = handler(request)
        except Exception as exc:
            payload = {"name": call.get("name"), "error": repr(exc)}
            self.sessions.emit_event(self.session_id, "tool_error", payload)
            self._emit_run_event("tool_error", payload, raw=payload)
            _emit_tool_status(writer, {"name": call.get("name"), "status": "error"})
            raise
        payload = {"name": call.get("name"), "result": result}
        self.sessions.emit_event(self.session_id, "tool_response", payload)
        self._emit_run_event("tool_response", {"name": call.get("name")}, raw=payload)
        _emit_tool_status(writer, {"name": call.get("name"), "status": "completed"})
        print(f"[tool] {call.get('name')} completed")
        return result

    def _emit_run_event(self, event_type: str, payload: dict[str, Any], *, raw: Any) -> None:
        if self.run_id is None:
            return
        self.sessions.emit_run_event(self.run_id, event_type, payload, raw=raw)


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


def _stream_writer() -> Callable[[Any], None] | None:
    try:
        return get_stream_writer()
    except (KeyError, RuntimeError):
        return None


def _emit_tool_status(writer: Callable[[Any], None] | None, payload: dict[str, Any]) -> None:
    if writer is None:
        return
    writer(payload)
