from __future__ import annotations

import os
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
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from runtime.middleware import (
    NO_PROGRESS_WINDOW,
    NoProgressLoop,
    NoProgressMiddleware,
    StructuredOutputCompatibility,
    ToolTelemetry,
    runtime_middlewares,
)
from runtime.observability import MAIN_AGENT_NAME
from skills.philipswgqinboundrecognition import WORKFLOW, PhilipsWgqRecognitionResult


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

PHILIPS_WORKFLOW_PROMPT = (
    "The API selected workflow philips_wgq_inbound_recognition. Load and follow "
    "/skills/philipswgqinboundrecognition/SKILL.md for this run."
)

SKILLS_SOURCE = "/skills/"

__all__ = [
    "BACKEND_ENV_PATH",
    "Brain",
    "BrainFactory",
    "DEFAULT_SYSTEM_PROMPT",
    "DeepAgentsBrainFactory",
    "MAIN_AGENT_NAME",
    "NO_PROGRESS_WINDOW",
    "NoProgressLoop",
    "NoProgressMiddleware",
    "PHILIPS_WORKFLOW_PROMPT",
    "SKILLS_SOURCE",
    "StructuredOutputCompatibility",
    "ToolTelemetry",
    "workflow_subagents",
]


# deepagents 0.6.12 exposes profile registration, not a create_deep_agent
# harness_profile argument. Disable its auto-added general-purpose subagent at
# the provider profile and keep the two explicit Tecan extractors below.
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
        workflow: str | None = None,
    ) -> Brain: ...


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
        workflow: str | None = None,
    ) -> Brain:
        configured_middleware = list(middleware)
        if workflow == WORKFLOW and not any(
            isinstance(item, StructuredOutputCompatibility)
            for item in configured_middleware
        ):
            configured_middleware.append(StructuredOutputCompatibility())

        kwargs: dict[str, Any] = {
            "model": self.model,
            "tools": tools,
            "system_prompt": self.system_prompt,
            "middleware": configured_middleware,
            "backend": resources.backend,
            "checkpointer": resources.checkpointer,
            "store": resources.store,
            "skills": [SKILLS_SOURCE],
            "subagents": [] if workflow == WORKFLOW else workflow_subagents(),
            "permissions": [
                FilesystemPermission(
                    operations=["write"],
                    paths=["/skills/**"],
                    mode="deny",
                )
            ],
            "name": MAIN_AGENT_NAME,
        }
        if workflow == WORKFLOW:
            kwargs["system_prompt"] = f"{self.system_prompt}\n\n{PHILIPS_WORKFLOW_PROMPT}"
            kwargs["response_format"] = _PHILIPS_RESPONSE_FORMAT
            kwargs["tools"] = [
                tool
                for tool in tools
                if getattr(tool, "__name__", "")
                in {"parse_documents", "lookup_philips_wgq_master_data"}
            ]
        return create_deep_agent(**kwargs)


class ExtractionReference(BaseModel):
    extractor: str
    artifact_path: str


_RESPONSE_FORMAT = ToolStrategy(
    ExtractionReference,
    tool_message_content="Extraction artifact reference recorded.",
)
_PHILIPS_RESPONSE_FORMAT = ToolStrategy(
    PhilipsWgqRecognitionResult,
    tool_message_content="Philips WGQ recognition result recorded.",
)
_READ_ONLY_FILES = [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]


def workflow_subagents() -> list[SubAgent]:
    """Return the two stateless Tecan extractors registered on the main agent.

    Each installs its own runtime middleware since declarative SubAgents do not
    inherit the main agent's middleware.
    """
    return [
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


_TECAN_PROMPT = """You are {extractor}, a stateless independent extractor.
Read only the exact source_artifact path in the task description. Do not use prior conclusions,
search for another file, or infer missing values. Extract only pieces and gross_weight, each with
high/medium/low confidence, and keep items as an empty list. Call save_tecan_extraction exactly once
using extractor={extractor}; use null+low for missing values. After the save tool returns, emit
ExtractionReference with that exact extractor and artifact_path. If structured output fails, the
final text must still be the same two-field JSON object."""
