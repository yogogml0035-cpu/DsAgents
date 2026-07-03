from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterator, Protocol, Sequence

from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.messages import RemoveMessage
from langchain_core.language_models import BaseChatModel
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from hands import Hands, TraceHands
from resources import AgentResources
from session import ContextWindow
from tools import ToolCatalog, ToolHandler, default_tool_catalog



DEFAULT_SYSTEM_PROMPT = (
    "You are a document-processing agent. When a user provides a local file path "
    "and asks to parse PDF, image, DOCX, PPTX, or XLSX files, call "
    "`parse_document`. Persist important notes under /memories/ and write large "
    "outputs under /artifacts/."
)


class Brain(Protocol):
    def invoke(self, payload: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]: ...

    def stream(
        self,
        payload: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any] | Any]: ...


class BrainFactory(Protocol):
    def create(
        self,
        *,
        resources: AgentResources,
        middleware: Sequence[AgentMiddleware],
        tools: Sequence[ToolHandler],
        session_id: str,
    ) -> Brain: ...


class DeepAgentsBrainFactory:
    def __init__(self, model: str | BaseChatModel | None = None, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        if model is None:
            # ponytail: keep existing MINIMAX_* keys and adapt them onto LangChain's Anthropic client.
            model = init_chat_model(
                f"anthropic:{os.getenv('MINIMAX_MODEL')}",
                api_key=os.getenv("MINIMAX_API_KEY"),
                base_url=os.getenv("MINIMAX_BASE_URL"),
                thinking={"type": "adaptive"},
            )
        self.model = model
        self.system_prompt = system_prompt

    def create(
        self,
        *,
        resources: AgentResources,
        middleware: Sequence[AgentMiddleware],
        tools: Sequence[ToolHandler],
        session_id: str,
    ) -> Brain:
        return create_deep_agent(
            model=self.model,
            tools=tools,
            system_prompt=self.system_prompt,
            middleware=list(middleware),
            backend=resources.backend,
            checkpointer=resources.checkpointer,
            store=resources.store,
        )


@dataclass(frozen=True)
class HarnessTurn:
    session_id: str
    context: ContextWindow
    result: dict[str, Any]


class HarnessRuntime:
    def __init__(
        self,
        *,
        resources: AgentResources,
        hands: Hands,
        tools: ToolCatalog,
        brain_factory: BrainFactory,
    ) -> None:
        self.resources = resources
        self.hands = hands
        self.tools = tools
        self.brain_factory = brain_factory

    def run_turn(self, message: str, session_id: str) -> HarnessTurn:
        context, brain = self._prepare_turn(message, session_id)
        result = brain.invoke(
            {"messages": _reset_messages(context)},
            config={"configurable": {"thread_id": session_id}},
        )

        self.resources.sessions.emit_event(
            session_id,
            "assistant_message",
            {"role": "assistant", "content": _assistant_content(result)},
        )
        return HarnessTurn(session_id=session_id, context=context, result=result)

    def stream_turn(self, message: str, session_id: str) -> Iterator[tuple[str, dict[str, Any]]]:
        context, brain = self._prepare_turn(message, session_id)
        assistant_content = None
        text_parts: list[str] = []

        for chunk in brain.stream(
            {"messages": _reset_messages(context)},
            config={"configurable": {"thread_id": session_id}},
            stream_mode=["messages", "custom", "values"],
            version="v2",
        ):
            if not isinstance(chunk, dict):
                continue
            if chunk["type"] == "messages":
                thinking = _thinking_delta(chunk["data"])
                if thinking:
                    yield ("thinking_delta", {"content": thinking})
                text = _message_delta(chunk["data"])
                if text:
                    text_parts.append(text)
                    yield ("text_delta", {"content": text})
            elif chunk["type"] == "custom":
                if isinstance(chunk["data"], dict):
                    yield ("tool_status", chunk["data"])
            elif chunk["type"] == "values":
                content = _assistant_content(chunk["data"])
                if content is not None:
                    assistant_content = content

        if assistant_content is None and text_parts:
            assistant_content = "".join(text_parts)

        self.resources.sessions.emit_event(
            session_id,
            "assistant_message",
            {"role": "assistant", "content": assistant_content},
        )

    def _prepare_turn(self, message: str, session_id: str) -> tuple[ContextWindow, Brain]:
        self.resources.sessions.ensure_session(session_id)
        self.resources.sessions.emit_event(
            session_id,
            "user_message",
            {"role": "user", "content": message},
        )
        context = self.resources.sessions.context_window(session_id)

        brain = self.brain_factory.create(
            resources=self.resources,
            middleware=self.hands.middleware(session_id),
            tools=self.tools.as_list(),
            session_id=session_id,
        )
        return context, brain


def create_harness(resources: AgentResources) -> HarnessRuntime:
    return HarnessRuntime(
        resources=resources,
        hands=TraceHands(resources.sessions),
        tools=default_tool_catalog(),
        brain_factory=DeepAgentsBrainFactory(),
    )


def _assistant_content(result: dict[str, Any]) -> Any:
    messages = result.get("messages") or []
    for message in reversed(messages):
        if _message_role(message) in {"assistant", "ai"}:
            return _message_content(message)
    if messages:
        return _message_content(messages[-1])
    return None


def assistant_reply_text(result: dict[str, Any]) -> str:
    content = _assistant_content(result)
    text = _content_text(content)
    if text:
        return text
    return _stringify_content(content)


def _reset_messages(context: ContextWindow) -> list[Any]:
    return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *context.messages]


def _message_delta(data: Any) -> str:
    message = data[0] if isinstance(data, tuple) and data else data
    if isinstance(message, dict):
        event = message.get("event")
        if event and not str(event).endswith("delta"):
            return ""
        return _content_text(message.get("delta")) or _content_text(message.get("content")) or _content_text(message)
    return _content_text(_message_content(message))


def _thinking_delta(data: Any) -> str:
    message = data[0] if isinstance(data, tuple) and data else data
    if isinstance(message, dict):
        event = message.get("event")
        if event and not str(event).endswith("delta"):
            return ""
        return (
            _thinking_text(message.get("delta"))
            or _thinking_text(message.get("content"))
            or _thinking_text(message)
        )
    return _thinking_text(getattr(message, "content_blocks", None)) or _thinking_text(
        _message_content(message)
    )


def _message_role(message: Any) -> str | None:
    role = getattr(message, "role", None)
    if isinstance(role, str):
        return role
    message_type = getattr(message, "type", None)
    if isinstance(message_type, str):
        return "assistant" if message_type == "ai" else message_type
    if isinstance(message, dict):
        value = message.get("role") or message.get("type")
        if isinstance(value, str):
            return "assistant" if value == "ai" else value
    return None


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", message)


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return (
            _content_text(content.get("text"))
            or _content_text(content.get("delta"))
            or _content_text(content.get("content"))
        )
    if isinstance(content, list):
        return "".join(_content_text(item) for item in content)
    return ""


def _thinking_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, dict):
        block_type = content.get("type")
        if block_type == "thinking":
            return _content_text(content.get("thinking")) or _content_text(
                content.get("text")
            )
        if block_type == "reasoning":
            return _content_text(content.get("reasoning")) or _content_text(
                content.get("text")
            )
        if block_type == "non_standard":
            return _thinking_text(content.get("value"))
        return _thinking_text(content.get("delta")) or _thinking_text(content.get("content"))
    if isinstance(content, list):
        return "".join(_thinking_text(item) for item in content)
    return ""


def _stringify_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, (str, int, float, bool)):
        return str(content)
    return repr(content)
