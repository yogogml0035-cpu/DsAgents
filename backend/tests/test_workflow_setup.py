from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, ToolMessage

from harness import (
    DEFAULT_SYSTEM_PROMPT,
    MAIN_AGENT_NAME,
    SKILLS_SOURCE,
    DeepAgentsBrainFactory,
    _snapshot_events,
)
from resources import AgentResources, ResourceConfig
from subagents import workflow_subagents


def run() -> None:
    skills_root = Path(__file__).resolve().parents[1] / "skills"
    philips = (skills_root / "philips-wgq-import" / "SKILL.md").read_text(encoding="utf-8")
    tecan = (skills_root / "tecan-import" / "SKILL.md").read_text(encoding="utf-8")
    assert len(philips.splitlines()) <= 100
    assert len(tecan.splitlines()) <= 100
    assert "同一个主模型回合并行" in philips
    assert "同一个主模型回合并行" in tecan
    assert "普通 PDF" in philips and "普通 PDF" in tecan
    assert "ordinary PDF extraction request is not enough" in DEFAULT_SYSTEM_PROMPT

    specs = workflow_subagents()
    assert [spec["name"] for spec in specs] == [
        "philips-wgq-extractor-a",
        "philips-wgq-extractor-b",
        "tecan-extractor-a",
        "tecan-extractor-b",
    ]
    assert all(len(spec["tools"]) == 1 for spec in specs)
    assert all(isinstance(spec["response_format"], ToolStrategy) for spec in specs)
    assert all(
        spec["permissions"]
        == [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
        for spec in specs
    )

    sentinel = object()
    resources = SimpleNamespace(backend=object(), checkpointer=object(), store=object())
    with patch("harness.create_deep_agent", return_value=sentinel) as create:
        assert DeepAgentsBrainFactory(model="anthropic:test").create(
            resources=resources,
            middleware=[],
            tools=[],
        ) is sentinel
    kwargs = create.call_args.kwargs
    assert kwargs["skills"] == [SKILLS_SOURCE]
    assert [spec["name"] for spec in kwargs["subagents"]] == [spec["name"] for spec in specs]
    assert kwargs["name"] == MAIN_AGENT_NAME
    assert kwargs["permissions"] == [
        FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny")
    ]

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        with AgentResources(ResourceConfig(data_dir=Path(tmp) / "data")) as mounted:
            listing = mounted.backend.ls("/skills/")
            assert listing.error is None
            paths = {entry["path"].rstrip("/") for entry in listing.entries}
            assert "/skills/philips-wgq-import" in paths
            assert "/skills/tecan-import" in paths
            skill = mounted.backend.read("/skills/philips-wgq-import/SKILL.md")
            assert skill.error is None and skill.file_data is not None
            assert "philips-wgq-import" in skill.file_data["content"]

    extraction_request = "read /artifacts/downloads/source.json and extract the exact Philips fields"
    task_message = AIMessage(
        content="",
        id="task-message",
        tool_calls=[
            {
                "id": f"task-call-{name}",
                "name": "task",
                "args": {
                    "subagent_type": f"philips-wgq-extractor-{name}",
                    "description": extraction_request,
                },
            }
            for name in ("a", "b")
        ],
    )
    assert len(task_message.tool_calls) == 2
    assert {call["args"]["description"] for call in task_message.tool_calls} == {extraction_request}
    events = list(
        _snapshot_events(
            {
                "messages": [
                    task_message,
                    *[
                        ToolMessage(
                            content=(
                                f'{{"extractor":"philips-wgq-extractor-{name}",'
                                f'"artifact_path":"/artifacts/downloads/{name}.json"}}'
                            ),
                            id=f"task-result-{name}",
                            tool_call_id=f"task-call-{name}",
                            name="task",
                        )
                        for name in ("a", "b")
                    ],
                ]
            },
            seen_tool_call_ids=set(),
            seen_tool_result_ids=set(),
            seen_assistant_message_ids=set(),
            tool_call_names={},
        )
    )
    assert [event[0] for event in events] == ["tool_call", "tool_call", "tool_result", "tool_result"]
    assert all(event[1]["name"] == "task" for event in events[:2])
    assert "/artifacts/downloads/a.json" in events[2][1]["text"]
    assert "/artifacts/downloads/b.json" in events[3][1]["text"]


if __name__ == "__main__":
    run()
