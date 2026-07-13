from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.middleware.subagents import SubAgent
from dotenv import load_dotenv
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.config import get_stream_writer
from pydantic import BaseModel

from runtime import observability
from runtime.observability import MAIN_AGENT_NAME


BACKEND_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(BACKEND_ENV_PATH)


DEFAULT_SYSTEM_PROMPT = (
    "You are a document-processing agent. When a user provides a local "
    "/artifacts/ path, use `read_file` for images or media inspection and "
    "`parse_documents` for documents when structured extraction is needed. "
    "Use a business Skill only when the user clearly requests that business "
    "outcome; a filename or an ordinary PDF extraction request is not enough. "
    "Business tools accept only explicit artifact paths and never search for "
    "a recent file or prior task. "
    "Persist important notes under /memories/ and write large outputs under "
    "/artifacts/."
)

SKILLS_SOURCE = "/skills/"
NO_PROGRESS_WINDOW = 3


# deepagents 0.6.12 exposes profile registration, not a create_deep_agent
# harness_profile argument. Disable its auto-added fifth subagent at the
# provider profile and keep the four explicit workflow extractors below.
register_harness_profile(
    "anthropic",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
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
        resources: Any,
        middleware: Sequence[AgentMiddleware],
        tools: Sequence[Any],
    ) -> Brain: ...


class NoProgressLoop(Exception):
    """Raised by NoProgressMiddleware when the agent repeats the same tool call."""


class ToolTelemetry(AgentMiddleware):
    """wrap_tool_call: emit tool_execution start/complete/error with timing and
    a scope path so run queries can reconstruct 主 Agent → SubAgent → Tool."""

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
    """before_model: detect a no-progress loop.

    After the most recent HumanMessage, if the same tool + normalized args
    appears NO_PROGRESS_WINDOW times in a row, raise NoProgressLoop.
    """

    def __init__(self) -> None:
        self._recent: deque[str] = deque(maxlen=NO_PROGRESS_WINDOW)

    def before_model(self, state: Any, runtime: Any) -> None:
        messages = state["messages"] if isinstance(state, dict) else getattr(state, "messages", [])
        for message in messages:
            if isinstance(message, HumanMessage):
                self._recent.clear()
                return
        # No new human turn since last check: inspect the trailing tool calls.
        for message in reversed(messages):
            tool_calls = observability.tool_calls_of(message)
            for tool_call in reversed(tool_calls):
                token = _tool_call_token(tool_call)
                if token is None:
                    continue
                self._recent.appendleft(token)
                if (
                    len(self._recent) == NO_PROGRESS_WINDOW
                    and len(set(self._recent)) == 1
                ):
                    raise NoProgressLoop(
                        f"no_progress_loop: repeated {self._recent[0]} "
                        f"{NO_PROGRESS_WINDOW} times"
                    )
                return  # only the most recent tool call is new per model turn


def runtime_middlewares() -> list[AgentMiddleware]:
    """Fresh middleware instances. Each SubAgent must install its own because
    declarative SubAgents do not inherit the main agent's middleware."""
    return [ToolTelemetry(), NoProgressMiddleware()]


class DeepAgentsBrainFactory:
    def __init__(self, model: str | BaseChatModel | None = None, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        if model is None:
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
        resources: Any,
        middleware: Sequence[AgentMiddleware],
        tools: Sequence[Any],
    ) -> Brain:
        return create_deep_agent(
            model=self.model,
            tools=tools,
            system_prompt=self.system_prompt,
            middleware=list(middleware),
            backend=resources.backend,
            checkpointer=resources.checkpointer,
            store=resources.store,
            skills=[SKILLS_SOURCE],
            subagents=workflow_subagents(),
            permissions=[
                FilesystemPermission(
                    operations=["write"],
                    paths=["/skills/**"],
                    mode="deny",
                )
            ],
            name=MAIN_AGENT_NAME,
        )


class ExtractionReference(BaseModel):
    extractor: str
    artifact_path: str


_RESPONSE_FORMAT = ToolStrategy(
    ExtractionReference,
    tool_message_content="Extraction artifact reference recorded.",
)
_READ_ONLY_FILES = [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]


def workflow_subagents() -> list[SubAgent]:
    """Return the four stateless declarative extractors registered on the main agent.

    Each installs its own runtime middleware (telemetry + no-progress) since
    declarative SubAgents do not inherit the main agent's middleware.
    """
    return [
        _extractor(
            name="philips-wgq-extractor-a",
            description=(
                "Independent Philips WGQ PDF-field extractor A; use only when "
                "the Philips skill explicitly requests its A vote."
            ),
            prompt=_PHILIPS_PROMPT,
            tool="save_philips_wgq_extraction",
        ),
        _extractor(
            name="philips-wgq-extractor-b",
            description=(
                "Independent Philips WGQ PDF-field extractor B; use only when "
                "the Philips skill explicitly requests its B vote."
            ),
            prompt=_PHILIPS_PROMPT,
            tool="save_philips_wgq_extraction",
        ),
        _extractor(
            name="tecan-extractor-a",
            description=(
                "Independent Tecan transport-field extractor A; use only when "
                "the Tecan skill explicitly requests its A vote."
            ),
            prompt=_TECAN_PROMPT,
            tool="save_tecan_extraction",
        ),
        _extractor(
            name="tecan-extractor-b",
            description=(
                "Independent Tecan transport-field extractor B; use only when "
                "the Tecan skill explicitly requests its B vote."
            ),
            prompt=_TECAN_PROMPT,
            tool="save_tecan_extraction",
        ),
    ]


def _extractor(*, name: str, description: str, prompt: str, tool: str) -> SubAgent:
    from runtime.tools import default_tool_catalog

    tool_handler = next(
        handler for handler in default_tool_catalog().handlers if handler.__name__ == tool
    )
    return {
        "name": name,
        "description": description,
        "system_prompt": prompt.format(extractor=name),
        "tools": [tool_handler],
        "permissions": _READ_ONLY_FILES,
        "response_format": _RESPONSE_FORMAT,
        "middleware": runtime_middlewares(),
    }


_PHILIPS_PROMPT = """You are {extractor}, a stateless independent extractor.
Read only the exact source_artifact path in the task description. Do not use prior conclusions,
search for another file, or infer missing values. Extract exactly the Philips logistics and nine
item fields requested by the task, with high/medium/low confidence. Call
save_philips_wgq_extraction exactly once using extractor={extractor}; use null+low for missing values.
After the save tool returns, emit ExtractionReference with that exact extractor and artifact_path.
If structured output fails, the final text must still be the same two-field JSON object."""

_TECAN_PROMPT = """You are {extractor}, a stateless independent extractor.
Read only the exact source_artifact path in the task description. Do not use prior conclusions,
search for another file, or infer missing values. Extract only pieces and gross_weight, each with
high/medium/low confidence, and keep items as an empty list. Call save_tecan_extraction exactly once
using extractor={extractor}; use null+low for missing values. After the save tool returns, emit
ExtractionReference with that exact extractor and artifact_path. If structured output fails, the
final text must still be the same two-field JSON object."""


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
    if writer is None:
        return
    writer(payload)


def _tool_call_token(tool_call: dict[str, Any]) -> str | None:
    name = tool_call.get("name")
    args = tool_call.get("args")
    if not isinstance(name, str) or not isinstance(args, dict):
        return None
    import json

    return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"
