from __future__ import annotations

from deepagents import FilesystemPermission
from deepagents.middleware.subagents import SubAgent
from langchain.agents.structured_output import ToolStrategy
from pydantic import BaseModel

from philips_wgq_import import save_philips_wgq_extraction
from tecan_import import save_tecan_extraction


class ExtractionReference(BaseModel):
    extractor: str
    artifact_path: str


_READ_ONLY_FILES = [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
_RESPONSE_FORMAT = ToolStrategy(
    ExtractionReference,
    tool_message_content="Extraction artifact reference recorded.",
)


def workflow_subagents() -> list[SubAgent]:
    """Return the four stateless declarative extractors registered on the main agent."""
    return [
        _extractor(
            name="philips-wgq-extractor-a",
            description="Independent Philips WGQ PDF-field extractor A; use only when the Philips skill explicitly requests its A vote.",
            prompt=_PHILIPS_PROMPT,
            tool=save_philips_wgq_extraction,
        ),
        _extractor(
            name="philips-wgq-extractor-b",
            description="Independent Philips WGQ PDF-field extractor B; use only when the Philips skill explicitly requests its B vote.",
            prompt=_PHILIPS_PROMPT,
            tool=save_philips_wgq_extraction,
        ),
        _extractor(
            name="tecan-extractor-a",
            description="Independent Tecan transport-field extractor A; use only when the Tecan skill explicitly requests its A vote.",
            prompt=_TECAN_PROMPT,
            tool=save_tecan_extraction,
        ),
        _extractor(
            name="tecan-extractor-b",
            description="Independent Tecan transport-field extractor B; use only when the Tecan skill explicitly requests its B vote.",
            prompt=_TECAN_PROMPT,
            tool=save_tecan_extraction,
        ),
    ]


def _extractor(*, name: str, description: str, prompt: str, tool: object) -> SubAgent:
    return {
        "name": name,
        "description": description,
        "system_prompt": prompt.format(extractor=name),
        "tools": [tool],
        "permissions": _READ_ONLY_FILES,
        "response_format": _RESPONSE_FORMAT,
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
