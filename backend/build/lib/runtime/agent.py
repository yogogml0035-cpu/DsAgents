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
    StructuredOutputRecovery,
    ToolTelemetry,
    philips_structured_output_error_message,
    runtime_middlewares,
)
from runtime.observability import MAIN_AGENT_NAME
from skills.philipswgqinboundrecognition import WORKFLOW, PhilipsWgqRecognitionResult


BACKEND_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(BACKEND_ENV_PATH)


DEFAULT_SYSTEM_PROMPT = (
    "你是文档处理智能体。当用户给出本地 `/artifacts/` 路径时："
    "图片或媒体用 `read_file` 查看；需要结构化抽取的文档用 `parse_documents`。"
    "仅当用户明确要求对应业务结果时才使用业务 Skill；仅文件名或普通 PDF 抽取请求不够。"
    "业务工具只接受本轮消息中的显式 artifact 路径，禁止搜索最近文件或历史任务。"
    "大体积输出写入 `/artifacts/`。"
)

PHILIPS_WORKFLOW_PROMPT = (
    "API 已选择 workflow=philips_wgq_inbound_recognition。"
    "本 run 必须加载并遵循 `/skills/philips-wgq-inbound-recognition/SKILL.md`。"
    "最终结果必须通过 PhilipsWgqRecognitionResult 结构化工具提交。"
    "禁止提交 data: {}，也不得省略 shipment/header/items。"
    "outcome 为 success 或 partial_success 时，data 必须包含完整嵌套对象："
    "shipment（pieces、total_gross_weight）、header（全部固定英文字段）、"
    "items（非空数组，每项为完整商品对象）；未知值填 null。"
    "outcome 为 input_problems 时，data 必须为 null（不能是 {}），且 problems 至少一条。"
    "若 schema 工具校验失败，须用完整嵌套结构重新提交，禁止重复提交空 data 壳。"
    "提交顺序：先在助手文本中输出恰好一个完整 ```json``` 对象（含 data.shipment/"
    "data.header/data.items，未知 null），再调用 PhilipsWgqRecognitionResult，"
    "tool 参数必须与该 JSON 相同；禁止只改 problems 却留下 data:{}。"
    "同一 HAWB/运单下多张商业发票、多个 PO/DN（如 UPS 普货）视为一票 consolidated："
    "header 中 invoice_number/po/dn 可逗号拼接，items 按发票顺序展开多行。"
    "自然语言摘要不能替代业务结果。"
)

# Philips drops only Tecan business tools; shared MinerU tools stay available so
# /memories/AGENTS.md ZIP guidance (extract_archives) matches the tool table.
_PHILIPS_EXCLUDED_TOOLS = frozenset(
    {
        "save_tecan_extraction",
        "generate_tecan_import",
    }
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
    "StructuredOutputRecovery",
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
        if workflow == WORKFLOW:
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
            # Keep shared MinerU tools (parse_documents / extract_archives) so the
            # runtime handbook ZIP path stays valid; only strip Tecan business tools.
            kwargs["tools"] = [
                tool
                for tool in tools
                if getattr(tool, "__name__", "") not in _PHILIPS_EXCLUDED_TOOLS
            ]
        return create_deep_agent(**kwargs)


class ExtractionReference(BaseModel):
    """帝肯抽取完成后的结构化引用：extractor 名与 artifact_path。"""

    extractor: str
    artifact_path: str


_RESPONSE_FORMAT = ToolStrategy(
    ExtractionReference,
    tool_message_content="已记录抽取结果 artifact 引用。",
)
_PHILIPS_RESPONSE_FORMAT = ToolStrategy(
    PhilipsWgqRecognitionResult,
    tool_message_content="已记录飞利浦外高桥识别结果。",
    handle_errors=philips_structured_output_error_message,
)
_READ_ONLY_FILES = [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]


def workflow_subagents() -> list[SubAgent]:
    """返回主 Agent 注册的两个无状态 Tecan 抽取 SubAgent。

    声明式 SubAgent 不继承主 Agent middleware，各自安装 runtime middleware。
    """
    return [
        _extractor(
            name="tecan-extractor-a",
            description=(
                "独立的帝肯运输字段抽取器 A；仅当 Tecan Skill 明确要求 A 票时使用。"
            ),
            prompt=_TECAN_PROMPT,
            tool="save_tecan_extraction",
        ),
        _extractor(
            name="tecan-extractor-b",
            description=(
                "独立的帝肯运输字段抽取器 B；仅当 Tecan Skill 明确要求 B 票时使用。"
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


_TECAN_PROMPT = """你是 {extractor}，无状态的独立抽取器。
只读取任务描述中给出的精确 source_artifact 路径。不得使用既有结论、搜索其他文件或推断缺失值。
仅抽取 pieces 与 gross_weight，每项带 high/medium/low 置信度；items 必须为空列表。
调用 save_tecan_extraction 恰好一次，extractor={extractor}；缺失值用 null + low。
save 工具返回后，提交 ExtractionReference，其中 extractor 与 artifact_path 必须与本次保存一致。
若结构化输出失败，最终文本仍须是同一双字段 JSON 对象。"""
