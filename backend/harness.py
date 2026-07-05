from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence

from deepagents import create_deep_agent
from dotenv import load_dotenv
from langchain.agents.middleware import AgentMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from hands import Hands, ToolStatusHands
from resources import AgentResources
from run_ledger import RunEvent
from tools import ToolCatalog, ToolHandler, default_tool_catalog


load_dotenv(Path(__file__).with_name(".env"))


DEFAULT_SYSTEM_PROMPT = (
    "You are a document-processing agent. When a user provides a local file path "
    "and asks to parse PDF, image, DOCX, PPTX, or XLSX files, call "
    "`parse_document`. Persist important notes under /memories/ and write large "
    "outputs under /artifacts/."
)


class Brain(Protocol):
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

    def execute_run(self, message: str, session_id: str, run_id: str) -> Iterator[RunEvent]:
        assistant_text = ""
        text_parts: list[str] = []
        yield self.resources.runs.emit_run_status(run_id, "running")
        try:
            brain = self.brain_factory.create(
                resources=self.resources,
                middleware=self.hands.middleware(),
                tools=self.tools.as_list(),
            )
            for chunk in brain.stream(
                {"messages": [{"role": "user", "content": message}]},
                config={"configurable": {"thread_id": session_id}},
                stream_mode=["messages", "custom", "values"],
                version="v2",
            ):
                if not isinstance(chunk, dict):
                    continue
                if chunk["type"] == "messages":
                    thinking = _thinking_delta(chunk["data"])
                    if thinking:
                        yield self.resources.runs.emit_run_event(
                            run_id,
                            "thinking",
                            {"content": thinking},
                            raw=chunk,
                        )
                    text = _message_delta(chunk["data"])
                    if text:
                        text_parts.append(text)
                        yield self.resources.runs.emit_run_event(
                            run_id,
                            "text_delta",
                            {"content": text},
                            raw=chunk,
                        )
                elif chunk["type"] == "custom":
                    yield self.resources.runs.emit_run_event(
                        run_id,
                        "tool_status",
                        _stream_payload(chunk["data"]),
                        raw=chunk,
                    )
                elif chunk["type"] == "values":
                    text = _assistant_text(chunk["data"])
                    if text:
                        assistant_text = text
                    yield self.resources.runs.emit_run_event(
                        run_id,
                        "values",
                        {"text": text} if text else {},
                        raw=chunk,
                    )
        except Exception as exc:
            yield self.resources.runs.emit_run_status(
                run_id,
                "failed",
                error=_error_text(exc),
                raw={"status": "failed", "error": repr(exc)},
            )
            return

        if not assistant_text and text_parts:
            assistant_text = "".join(text_parts)
        yield self.resources.runs.emit_run_status(
            run_id,
            "succeeded",
            reply=assistant_text,
            raw={"status": "succeeded", "reply": assistant_text},
        )


def create_harness(resources: AgentResources) -> HarnessRuntime:
    return HarnessRuntime(
        resources=resources,
        hands=ToolStatusHands(),
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


def _assistant_text(result: dict[str, Any]) -> str:
    return _content_text(_assistant_content(result))


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


def _stream_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"value": value}


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__
