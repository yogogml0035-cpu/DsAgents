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
from dotenv import load_dotenv
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from runtime.middleware import (
    NO_PROGRESS_WINDOW,
    NoProgressLoop,
    NoProgressMiddleware,
    StructuredOutputCompatibility,
    StructuredOutputRecovery,
    ToolTelemetry,
    philips_structured_output_error_message,
)
from runtime.observability import MAIN_AGENT_NAME
from skills.philips_wgq_inbound_recognition import WAG_WORKFLOW, PhilipsWgqRecognitionResult
from skills.tecan_import import DK_WORKFLOW


BACKEND_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(BACKEND_ENV_PATH)


DEFAULT_SYSTEM_PROMPT = (
    "你是文档处理智能体。当用户给出本地 `/artifacts/` 路径时："
    "图片或媒体用 `read_file` 查看；需要结构化抽取的文档用 `parse_documents`。"
    "已加载的业务 Skill 对材料类型另有约束时，以该 Skill 为准。"
    "仅当用户明确要求对应业务结果时才使用业务 Skill；仅文件名或普通 PDF 抽取请求不够。"
    "业务工具只接受本轮消息中的显式 artifact 路径，禁止搜索最近文件或历史任务。"
    "大体积输出写入 `/artifacts/`。"
)

WAG_WORKFLOW_PROMPT = (
    "API 已选择 workflow=WGQ（飞利浦外高桥进境识别）。"
    "本 run 必须加载并遵循 `/skills/philips-wgq-inbound-recognition/SKILL.md`，"
    "并按其引用读取货代版式说明。"
    "最终结果必须通过 PhilipsWgqRecognitionResult 结构化工具提交。"
    "禁止提交 data: {}，也不得省略 header/items。"
    "outcome 为 success 或 partial_success 时，data 必须包含完整嵌套对象："
    "header（全部固定英文字段）、"
    "items（非空数组，每项为完整商品对象）；未知值填 null。"
    "outcome 为 input_problems 时，data 仍须包含完整 header 与 items（可为空数组），且 problems 至少一条。"
    "若 schema 工具校验失败，须用完整嵌套结构重新提交，禁止重复提交空 data 壳。"
    "正常路径只调用 PhilipsWgqRecognitionResult 提交业务结果；不要在助手文本重复完整业务 JSON，"
    "避免文本与 tool 参数分叉。仅在无法形成有效工具调用时，才输出恰好一个符合 schema 的完整 ```json```"
    "作为后备；该 JSON 不能替代 tool 参数。禁止只改 problems 却留下 data:{}。"
    "自然语言摘要不能替代业务结果。"
)

DK_WORKFLOW_PROMPT = (
    "API 已选择 workflow=DK（帝肯境外供应链识别）。"
    "本 run 必须加载并遵循 `/skills/tecan-import/SKILL.md`。"
    "确认唯一 12NC 后必须调用 lookup_philips_wgq_master_data 查询共享 Oracle 主数据；"
    "DK 不传 tracking_artifact，且只用稳定字段补齐空值。"
    "最终必须调用 finalize_tecan_overseas_recognition；其返回值是唯一业务结果。"
    "自然语言摘要不能替代业务结果。"
)

# Workflow denylist only removes the other channel's business tool; shared
# MinerU tools stay available so /memories/AGENTS.md ZIP guidance remains valid.
_WAG_EXCLUDED_TOOLS = frozenset(
    {
        "finalize_tecan_overseas_recognition",
    }
)

# The legacy-named 12NC lookup is shared by WGQ and DK. DK has no other
# channel-only tool to exclude while retaining its required finalizer.
_DK_EXCLUDED_TOOLS = frozenset()

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
    "DK_WORKFLOW_PROMPT",
    "SKILLS_SOURCE",
    "StructuredOutputCompatibility",
    "StructuredOutputRecovery",
    "ToolTelemetry",
    "WAG_WORKFLOW_PROMPT",
]


# deepagents 0.6.12 exposes profile registration, not a create_deep_agent
# harness_profile argument. This document workflow keeps all material context
# in one agent, so there is no task/subagent state to coordinate.
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
        if workflow == WAG_WORKFLOW:
            if not any(
                isinstance(item, StructuredOutputCompatibility)
                for item in configured_middleware
            ):
                configured_middleware.append(StructuredOutputCompatibility())
            if not any(
                isinstance(item, StructuredOutputRecovery)
                for item in configured_middleware
            ):
                # Recovery first so after_model runs last among after_* hooks.
                configured_middleware.insert(0, StructuredOutputRecovery())

        kwargs: dict[str, Any] = {
            "model": self.model,
            "tools": tools,
            "system_prompt": self.system_prompt,
            "middleware": configured_middleware,
            "backend": resources.backend,
            "checkpointer": resources.checkpointer,
            "store": resources.store,
            "skills": [SKILLS_SOURCE],
            "subagents": [],
            "permissions": [
                FilesystemPermission(
                    operations=["write"],
                    paths=["/skills/**"],
                    mode="deny",
                )
            ],
            "name": MAIN_AGENT_NAME,
        }
        if workflow == WAG_WORKFLOW:
            kwargs["system_prompt"] = f"{self.system_prompt}\n\n{WAG_WORKFLOW_PROMPT}"
            kwargs["response_format"] = _WAG_RESPONSE_FORMAT
            # Keep shared MinerU tools (parse_documents / extract_archives) so the
            # runtime handbook ZIP path stays valid; only strip Tecan business tools.
            kwargs["tools"] = [
                tool
                for tool in tools
                if getattr(tool, "__name__", "") not in _WAG_EXCLUDED_TOOLS
            ]
        elif workflow == DK_WORKFLOW:
            kwargs["system_prompt"] = f"{self.system_prompt}\n\n{DK_WORKFLOW_PROMPT}"
            kwargs["tools"] = [
                tool
                for tool in tools
                if getattr(tool, "__name__", "") not in _DK_EXCLUDED_TOOLS
            ]
        return create_deep_agent(**kwargs)


_WAG_RESPONSE_FORMAT = ToolStrategy(
    PhilipsWgqRecognitionResult,
    tool_message_content="已记录飞利浦外高桥识别结果。",
    handle_errors=philips_structured_output_error_message,
)
