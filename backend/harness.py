from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

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

DEFAULT_MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic"
DEFAULT_MINIMAX_MODEL = "MiniMax-M3"
DEFAULT_SYSTEM_PROMPT = (
    "You are a document-processing agent. Use MinerU when a user asks to parse "
    "PDF, image, DOCX, PPTX, or XLSX files. Persist important notes under "
    "/memories/ and write large outputs under /artifacts/."
)


class Brain(Protocol):
    def invoke(self, payload: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]: ...


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
                f"anthropic:{os.getenv('MINIMAX_MODEL') or DEFAULT_MINIMAX_MODEL}",
                api_key=os.getenv("MINIMAX_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
                base_url=os.getenv("MINIMAX_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL") or DEFAULT_MINIMAX_BASE_URL,
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


def create_mineru_harness(resources: AgentResources) -> HarnessRuntime:
    return HarnessRuntime(
        resources=resources,
        hands=TraceHands(resources.sessions),
        tools=default_tool_catalog(),
        brain_factory=DeepAgentsBrainFactory(),
    )


def create_mineru_agent(resources: AgentResources, session_id: str) -> Brain:
    harness = create_mineru_harness(resources)
    return harness.brain_factory.create(
        resources=resources,
        middleware=harness.hands.middleware(session_id),
        tools=harness.tools.as_list(),
        session_id=session_id,
    )


def _assistant_content(result: dict[str, Any]) -> Any:
    messages = result.get("messages") or []
    if not messages:
        return None
    return getattr(messages[-1], "content", messages[-1])


def _reset_messages(context: ContextWindow) -> list[Any]:
    return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *context.messages]
