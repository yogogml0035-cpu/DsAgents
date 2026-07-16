from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain.chat_models import init_chat_model
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage

from deepagents.middleware.memory import MemoryMiddleware

from runtime.agent import (
    DEFAULT_SYSTEM_PROMPT,
    MAIN_AGENT_NAME,
    PHILIPS_WORKFLOW_PROMPT,
    SKILLS_SOURCE,
    DeepAgentsBrainFactory,
    StructuredOutputCompatibility,
    StructuredOutputRecovery,
    workflow_subagents,
)
from runtime.execution import _update_events
from runtime.middleware import RUNTIME_MEMORY_SYSTEM_PROMPT, runtime_middlewares
from runtime.resources import RUNTIME_AGENTS_PATH, AgentResources, ResourceConfig
from runtime.tools import default_tool_catalog


def run() -> None:
    skills_root = Path(__file__).resolve().parents[1] / "skills"
    philips = (skills_root / "philipswgqinboundrecognition" / "SKILL.md").read_text(encoding="utf-8")
    tecan = (skills_root / "tecanimport" / "SKILL.md").read_text(encoding="utf-8")
    assert len(philips.splitlines()) <= 100
    assert len(tecan.splitlines()) <= 100
    assert "重复 12NC 保留" in philips
    assert "ZIP、DOCX" in philips
    assert "两个以上真实票次" in philips
    assert "同一个主模型回合并行" in tecan
    assert "普通 PDF" in philips and "普通 PDF" in tecan
    assert "普通 PDF 抽取请求不够" in DEFAULT_SYSTEM_PROMPT
    assert "Persist important notes under /memories/" not in DEFAULT_SYSTEM_PROMPT
    assert "业务 Skill" in DEFAULT_SYSTEM_PROMPT
    assert "philipswgqinboundrecognition/SKILL.md" in PHILIPS_WORKFLOW_PROMPT

    # Subagents get recovery + telemetry stack — no shared handbook.
    sub_middleware = runtime_middlewares()
    assert len(sub_middleware) == 4
    assert isinstance(sub_middleware[0], StructuredOutputRecovery)
    assert not any(isinstance(item, MemoryMiddleware) for item in sub_middleware)

    main_backend = object()
    main_middleware = runtime_middlewares(memory_backend=main_backend)
    assert isinstance(main_middleware[0], StructuredOutputRecovery)
    memory_items = [item for item in main_middleware if isinstance(item, MemoryMiddleware)]
    assert len(memory_items) == 1
    assert memory_items[0].sources == [RUNTIME_AGENTS_PATH]
    assert memory_items[0].system_prompt == RUNTIME_MEMORY_SYSTEM_PROMPT
    assert memory_items[0]._add_cache_control is True
    assert "Learning from feedback" not in RUNTIME_MEMORY_SYSTEM_PROMPT
    assert "{agent_memory}" in RUNTIME_MEMORY_SYSTEM_PROMPT
    assert "edit_file" in RUNTIME_MEMORY_SYSTEM_PROMPT

    specs = workflow_subagents()
    assert [spec["name"] for spec in specs] == [
        "tecan-extractor-a",
        "tecan-extractor-b",
    ]
    assert all(len(spec["tools"]) == 1 for spec in specs)
    # Each declarative SubAgent installs its own runtime middleware because it
    # does not inherit the main agent's middleware. Handbook is main-agent only.
    assert all(len(spec["middleware"]) == 4 for spec in specs)
    assert all(
        not any(isinstance(item, MemoryMiddleware) for item in spec["middleware"])
        for spec in specs
    )
    assert all(
        any(isinstance(item, StructuredOutputRecovery) for item in spec["middleware"])
        for spec in specs
    )
    assert all(
        any(isinstance(item, StructuredOutputCompatibility) for item in spec["middleware"])
        for spec in specs
    )
    assert all(isinstance(spec["response_format"], ToolStrategy) for spec in specs)
    assert all(
        spec["permissions"]
        == [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
        for spec in specs
    )

    sentinel = object()
    resources = SimpleNamespace(backend=object(), checkpointer=object(), store=object())
    model = init_chat_model(
        "anthropic:test",
        api_key="test-key",
        thinking={"type": "adaptive"},
    )
    # Use the real static catalog so denylist drift vs tools.py is caught.
    catalog_tools = default_tool_catalog().as_list()
    catalog_names = {tool.__name__ for tool in catalog_tools}
    assert catalog_names == {
        "parse_documents",
        "extract_archives",
        "lookup_philips_wgq_master_data",
        "save_tecan_extraction",
        "generate_tecan_import",
    }
    with patch("runtime.agent.create_deep_agent", return_value=sentinel) as create:
        assert DeepAgentsBrainFactory(model=model).create(
            resources=resources,
            middleware=[],
            tools=catalog_tools,
            workflow="philips_wgq_inbound_recognition",
        ) is sentinel
    kwargs = create.call_args.kwargs
    assert kwargs["skills"] == [SKILLS_SOURCE]
    assert kwargs["subagents"] == []
    assert kwargs["name"] == MAIN_AGENT_NAME
    assert kwargs["permissions"] == [
        FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny")
    ]
    philips_tool_names = {getattr(tool, "__name__", "") for tool in kwargs["tools"]}
    assert philips_tool_names == {
        "parse_documents",
        "extract_archives",
        "lookup_philips_wgq_master_data",
    }
    assert philips_tool_names.isdisjoint(
        {"save_tecan_extraction", "generate_tecan_import"}
    )
    assert "extract_archives" in philips_tool_names
    assert isinstance(kwargs["response_format"], ToolStrategy)
    assert PHILIPS_WORKFLOW_PROMPT in kwargs["system_prompt"]
    assert "data: {}" in kwargs["system_prompt"]
    assert "shipment" in kwargs["system_prompt"]
    assert "```json" in kwargs["system_prompt"]
    assert "consolidated" in kwargs["system_prompt"]
    assert callable(kwargs["response_format"].handle_errors)
    empty_shell_msg = kwargs["response_format"].handle_errors(
        Exception("placeholder")
    )
    # Without ai_message args, still returns a fix prompt; empty-shell path is
    # covered in test_harness. Ensure handle_errors is our custom callable.
    assert "请修正后重试" in empty_shell_msg
    assert kwargs["model"] is model
    compatibility = next(
        middleware
        for middleware in kwargs["middleware"]
        if isinstance(middleware, StructuredOutputCompatibility)
    )
    request = ModelRequest(
        model=model,
        messages=[],
        response_format=kwargs["response_format"],
    )
    seen_requests = []
    structured_response = object()

    def handler(adjusted_request: ModelRequest) -> ModelResponse:
        seen_requests.append(adjusted_request)
        return ModelResponse(
            result=[AIMessage(content="done")],
            structured_response=structured_response,
        )

    response = compatibility.wrap_model_call(request, handler)
    assert response.structured_response is structured_response
    assert len(seen_requests) == 1
    assert seen_requests[0].model is not model
    assert seen_requests[0].model.thinking is None
    assert model.thinking == {"type": "adaptive"}
    compatibility.wrap_model_call(request.override(response_format=None), handler)
    assert seen_requests[-1].model is model

    with patch("runtime.agent.create_deep_agent", return_value=sentinel) as create:
        DeepAgentsBrainFactory(model="anthropic:test").create(
            resources=resources,
            middleware=[],
            tools=[],
            workflow=None,
        )
    assert "response_format" not in create.call_args.kwargs
    assert [spec["name"] for spec in create.call_args.kwargs["subagents"]] == [
        spec["name"] for spec in specs
    ]

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        with AgentResources(ResourceConfig(data_dir=Path(tmp) / "data")) as mounted:
            listing = mounted.backend.ls("/skills/")
            assert listing.error is None
            paths = {entry["path"].rstrip("/") for entry in listing.entries}
            assert "/skills/philipswgqinboundrecognition" in paths
            assert "/skills/tecanimport" in paths
            skill = mounted.backend.read("/skills/philipswgqinboundrecognition/SKILL.md")
            assert skill.error is None and skill.file_data is not None
            assert "philips-wgq-inbound-recognition" in skill.file_data["content"]

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
                "args": {"subagent_type": f"tecan-extractor-{name}", "description": "go"},
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
