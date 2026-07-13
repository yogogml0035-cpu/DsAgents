from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage

from dsagents.runtime.agent import (
    DEFAULT_SYSTEM_PROMPT,
    MAIN_AGENT_NAME,
    SKILLS_SOURCE,
    DeepAgentsBrainFactory,
    workflow_subagents,
)
from dsagents.runtime.execution import _update_events
from dsagents.runtime.resources import AgentResources, ResourceConfig


def run() -> None:
    skills_root = Path(__file__).resolve().parents[1] / "dsagents" / "skills"
    philips = (skills_root / "philipswgqimport" / "SKILL.md").read_text(encoding="utf-8")
    tecan = (skills_root / "tecanimport" / "SKILL.md").read_text(encoding="utf-8")
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
    # Each declarative SubAgent installs its own runtime middleware (telemetry +
    # no-progress) since they do not inherit the main agent's middleware.
    assert all(len(spec["middleware"]) == 2 for spec in specs)
    assert all(isinstance(spec["response_format"], ToolStrategy) for spec in specs)
    assert all(
        spec["permissions"]
        == [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
        for spec in specs
    )

    sentinel = object()
    resources = SimpleNamespace(backend=object(), checkpointer=object(), store=object())
    with patch("dsagents.runtime.agent.create_deep_agent", return_value=sentinel) as create:
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
            assert "/skills/philipswgqimport" in paths
            assert "/skills/tecanimport" in paths
            skill = mounted.backend.read("/skills/philipswgqimport/SKILL.md")
            assert skill.error is None and skill.file_data is not None
            assert "philips-wgq-import" in skill.file_data["content"]

    # _update_events turns an `updates`-mode node diff into tool_execution /
    # assistant_message events. Two tool calls on one assistant message -> two
    # tool_execution entries; a terminal text message -> one assistant_message.
    task_message = AIMessage(
        content="",
        id="task-message",
        tool_calls=[
            {
                "id": f"task-call-{name}",
                "name": "task",
                "args": {"subagent_type": f"philips-wgq-extractor-{name}", "description": "go"},
            }
            for name in ("a", "b")
        ],
    )
    events = list(
        _update_events(
            {"agent": {"messages": [task_message]}}
        )
    )
    assert [event[0] for event in events] == ["tool_execution", "tool_execution"]
    assert all(event[1]["name"] == "task" for event in events)

    final = AIMessage(
        content=[{"type": "thinking", "thinking": "plan"}, {"type": "text", "text": "done"}],
        id="final-message",
    )
    assert list(_update_events({"agent": {"messages": [final]}})) == [
        ("assistant_message", {"message_id": "final-message", "thinking": "plan", "text": "done"})
    ]


if __name__ == "__main__":
    run()
