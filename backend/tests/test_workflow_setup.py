from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deepagents.middleware.filesystem import FilesystemPermission
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage

from runtime.agent import (
    DK_WORKFLOW_PROMPT,
    DEFAULT_SYSTEM_PROMPT,
    MAIN_AGENT_NAME,
    SKILLS_SOURCE,
    DeepAgentsBrainFactory,
    StructuredOutputCompatibility,
    StructuredOutputRecovery,
    WAG_WORKFLOW_PROMPT,
)
from runtime.execution import _update_events
from runtime.middleware import RUNTIME_MEMORY_SYSTEM_PROMPT, runtime_middlewares
from runtime.resources import RUNTIME_AGENTS_PATH, AgentResources, ResourceConfig
from runtime.tools import default_tool_catalog
from skills.philips_wgq_inbound_recognition import WAG_WORKFLOW
from skills.tecan_import import DK_WORKFLOW


def run() -> None:
    skills_root = Path(__file__).resolve().parents[1] / "skills"
    philips = (skills_root / "philips_wgq_inbound_recognition" / "SKILL.md").read_text(encoding="utf-8")
    tecan = (skills_root / "tecan_import" / "SKILL.md").read_text(encoding="utf-8")
    assert len(philips.splitlines()) <= 100
    assert len(tecan.splitlines()) <= 100
    assert "相同 12NC 默认不合并" in philips
    assert "original_waybill_number" in philips
    assert "input_problems" in tecan
    assert "不生成 Excel" in tecan
    assert "lookup_philips_wgq_master_data" in tecan
    assert "name: philips-wgq-inbound-recognition" in philips
    assert "name: tecan-import" in tecan
    freight_forwarders = (
        skills_root / "philips_wgq_inbound_recognition" / "references" / "freight-forwarders.md"
    ).read_text(encoding="utf-8")
    assert all(name in freight_forwarders for name in ("DHL", "DSV", "FedEx", "UPS", "康捷空"))
    assert "普通 PDF 抽取请求不够" in DEFAULT_SYSTEM_PROMPT
    assert WAG_WORKFLOW == "WGQ"
    assert DK_WORKFLOW == "DK"
    assert "philips-wgq-inbound-recognition/SKILL.md" in WAG_WORKFLOW_PROMPT
    assert "tecan-import/SKILL.md" in DK_WORKFLOW_PROMPT

    default_middleware = runtime_middlewares()
    assert isinstance(default_middleware[0], StructuredOutputRecovery)
    assert len(default_middleware) == 4
    assert not any(isinstance(item, StructuredOutputRecovery) for item in runtime_middlewares(structured_schema=None))
    main_middleware = runtime_middlewares(memory_backend=object())
    memory_items = [item for item in main_middleware if isinstance(item, MemoryMiddleware)]
    assert len(memory_items) == 1
    assert memory_items[0].sources == [RUNTIME_AGENTS_PATH]
    assert memory_items[0].system_prompt == RUNTIME_MEMORY_SYSTEM_PROMPT

    resources = SimpleNamespace(backend=object(), checkpointer=object(), store=object())
    model = init_chat_model("anthropic:test", api_key="test-key", thinking={"type": "adaptive"})
    catalog_tools = default_tool_catalog().as_list()
    assert {tool.__name__ for tool in catalog_tools} == {
        "parse_documents",
        "extract_archives",
        "lookup_philips_wgq_master_data",
        "inspect_supply_chain_workbooks",
        "finalize_tecan_overseas_recognition",
    }
    sentinel = object()
    with patch("runtime.agent.create_deep_agent", return_value=sentinel) as create:
        assert DeepAgentsBrainFactory(model=model).create(
            resources=resources,
            middleware=[],
            tools=catalog_tools,
            workflow=WAG_WORKFLOW,
        ) is sentinel
    kwargs = create.call_args.kwargs
    assert kwargs["skills"] == [SKILLS_SOURCE]
    assert kwargs["subagents"] == []
    assert kwargs["name"] == MAIN_AGENT_NAME
    assert kwargs["permissions"] == [
        FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny")
    ]
    assert {tool.__name__ for tool in kwargs["tools"]} == {
        "parse_documents",
        "extract_archives",
        "lookup_philips_wgq_master_data",
        "inspect_supply_chain_workbooks",
    }
    assert isinstance(kwargs["response_format"], ToolStrategy)
    assert WAG_WORKFLOW_PROMPT in kwargs["system_prompt"]
    assert "shipment" not in kwargs["system_prompt"]
    assert "data: {}" in kwargs["system_prompt"]
    compatibility = next(item for item in kwargs["middleware"] if isinstance(item, StructuredOutputCompatibility))
    seen_requests = []

    def handler(request: ModelRequest) -> ModelResponse:
        seen_requests.append(request)
        return ModelResponse(result=[AIMessage(content="done")])

    compatibility.wrap_model_call(
        ModelRequest(model=model, messages=[], response_format=kwargs["response_format"]),
        handler,
    )
    assert seen_requests[0].model is not model
    assert seen_requests[0].model.thinking is None

    with patch("runtime.agent.create_deep_agent", return_value=sentinel) as create:
        DeepAgentsBrainFactory(model="anthropic:test").create(
            resources=resources,
            middleware=runtime_middlewares(structured_schema=None),
            tools=catalog_tools,
            workflow=DK_WORKFLOW,
        )
    dk_kwargs = create.call_args.kwargs
    assert DK_WORKFLOW_PROMPT in dk_kwargs["system_prompt"]
    assert "response_format" not in dk_kwargs
    assert {tool.__name__ for tool in dk_kwargs["tools"]} == {
        "parse_documents",
        "extract_archives",
        "lookup_philips_wgq_master_data",
        "inspect_supply_chain_workbooks",
        "finalize_tecan_overseas_recognition",
    }

    with patch("runtime.agent.create_deep_agent", return_value=sentinel) as create:
        DeepAgentsBrainFactory(model="anthropic:test").create(
            resources=resources,
            middleware=runtime_middlewares(structured_schema=None),
            tools=catalog_tools,
            workflow=None,
        )
    assert "response_format" not in create.call_args.kwargs
    assert create.call_args.kwargs["subagents"] == []

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        with AgentResources(ResourceConfig(data_dir=Path(tmp) / "data")) as mounted:
            paths = {entry["path"].rstrip("/") for entry in mounted.backend.ls("/skills/").entries}
            assert paths == {
                "/skills/philips-wgq-inbound-recognition",
                "/skills/tecan-import",
            }
            assert mounted.backend.read(
                "/skills/philips-wgq-inbound-recognition/references/freight-forwarders.md"
            ).error is None
            assert mounted.backend.read("/skills/tecan-import/references/fields.md").error is None
            with patch("deepagents.middleware.skills.logger.warning") as warning:
                loaded_skills = SkillsMiddleware(
                    backend=mounted.backend,
                    sources=[SKILLS_SOURCE],
                    system_prompt=None,
                ).before_agent({}, None, {})
            assert not warning.called
            assert loaded_skills is not None
            assert {
                (skill["name"], skill["path"])
                for skill in loaded_skills["skills_metadata"]
            } == {
                (
                    "philips-wgq-inbound-recognition",
                    "/skills/philips-wgq-inbound-recognition/SKILL.md",
                ),
                ("tecan-import", "/skills/tecan-import/SKILL.md"),
            }

    final = AIMessage(
        content=[{"type": "thinking", "thinking": "plan"}, {"type": "text", "text": "done"}],
        id="final-message",
    )
    assert list(_update_events({"agent": {"messages": [final]}})) == [
        ("assistant_message", {"message_id": "final-message", "thinking": "plan", "text": "done"})
    ]


if __name__ == "__main__":
    run()
