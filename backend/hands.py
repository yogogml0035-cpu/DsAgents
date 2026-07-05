from __future__ import annotations

from typing import Any, Callable, Protocol, Sequence

from langchain.agents.middleware import AgentMiddleware
from langchain.tools.tool_node import ToolCallRequest
from langgraph.config import get_stream_writer


class Hands(Protocol):
    def middleware(self) -> Sequence[AgentMiddleware]: ...


class ToolStatusHands:
    def middleware(self) -> Sequence[AgentMiddleware]:
        return [ToolStatusMiddleware()]


class ToolStatusMiddleware(AgentMiddleware):
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        call = request.tool_call
        writer = _stream_writer()
        _emit_tool_status(writer, {"name": call.get("name"), "status": "started"})
        try:
            result = handler(request)
        except Exception:
            _emit_tool_status(writer, {"name": call.get("name"), "status": "error"})
            raise
        _emit_tool_status(writer, {"name": call.get("name"), "status": "completed"})
        return result


def _stream_writer() -> Callable[[Any], None] | None:
    try:
        return get_stream_writer()
    except (KeyError, RuntimeError):
        return None


def _emit_tool_status(writer: Callable[[Any], None] | None, payload: dict[str, Any]) -> None:
    if writer is None:
        return
    writer(payload)
