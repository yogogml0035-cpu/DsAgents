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

from runtime.observability import MAIN_AGENT_NAME
from skills.philips_wgq_inbound_recognition import WAG_WORKFLOW, PhilipsWgqRecognitionResult
from skills.tecan_import import DK_WORKFLOW, TecanOverseasRecognitionResult


BACKEND_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(BACKEND_ENV_PATH)


DEFAULT_SYSTEM_PROMPT = (
    "你是文档处理智能体。不要发送无关的评论。你需要花更多时间思考；不需要输出你的思考内容和报告进度。当用户给出本地 `/artifacts/` 路径时："
    "图片或媒体用 `read_file` 查看；需要结构化抽取的文档用 `parse_documents`。"
    "已加载的业务 Skill 对材料类型另有约束时，以该 Skill 为准。"
    "仅当用户明确要求对应业务结果时才使用业务 Skill；仅文件名或普通 PDF 抽取请求不够。"
    "业务工具只接受本轮消息中的显式 artifact 路径，禁止搜索最近文件或历史任务。"
    "大体积输出写入 `/artifacts/`。"
)

CHANNEL_WORKFLOW_PROMPT = (
    "这是渠道供应链工作流：只处理本轮显式 artifact。"
    "将可支持 PDF 一次调用 parse_documents，将全部 XLSX 一次调用 inspect_supply_chain_workbooks；"
    "parse_documents 返回 archive_path 时，调用 extract_archives 后读取解压文本或 Markdown。"
    "按内容识别材料并归集唯一票次；多票或核心事实无法确认时使用 input_problems。"
    "仅对唯一确认的 12NC 调用 lookup_philips_wgq_master_data 补齐稳定空值，不覆盖本票事实。"
    "相同 12NC 默认不合并；发票和运单按材料与原行顺序归集。"
    "最终必须通过本 workflow 配置的结构化 schema 提交结果；run.result 是唯一业务 JSON；不输出待办、计划、Skill/参考文件内容、规则复述、字段分析、查询/校验重试或 JSON 草稿，文本只保留一句完成摘要。"
)

WAG_WORKFLOW_PROMPT = (
    "API 已选择 workflow=WGQ（飞利浦外高桥进境识别）。"
    "本 run 必须加载并遵循 `/skills/philips-wgq-inbound-recognition/SKILL.md`，"
    "并按其引用读取货代版式说明。"
    "最终使用 PhilipsWgqRecognitionResult schema。只将唯一确认的 Tracking 传给主数据查询。"
)

DK_WORKFLOW_PROMPT = (
    "API 已选择 workflow=DK（帝肯境外供应链识别）。"
    "本 run 必须加载并遵循 `/skills/tecan-import/SKILL.md`。"
    "DK 不传 tracking_artifact；最终使用 TecanOverseasRecognitionResult schema，不调用 Tecan finalizer。"
)

# Both workflow tool tables retain shared document and master-data tools while
# excluding the non-workflow Tecan finalizer.
_WORKFLOW_EXCLUDED_TOOLS = frozenset({"finalize_tecan_overseas_recognition"})

SKILLS_SOURCE = "/skills/"

__all__ = [
    "BACKEND_ENV_PATH",
    "Brain",
    "BrainFactory",
    "CHANNEL_WORKFLOW_PROMPT",
    "DEFAULT_SYSTEM_PROMPT",
    "DeepAgentsBrainFactory",
    "MAIN_AGENT_NAME",
    "DK_WORKFLOW_PROMPT",
    "SKILLS_SOURCE",
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
        kwargs: dict[str, Any] = {
            "model": self.model,
            "tools": tools,
            "system_prompt": self.system_prompt,
            "middleware": list(middleware),
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
        workflow_config = _WORKFLOW_CONFIGS.get(workflow)
        if workflow_config is not None:
            workflow_prompt, response_format = workflow_config
            kwargs["system_prompt"] = (
                f"{self.system_prompt}\n\n{CHANNEL_WORKFLOW_PROMPT}\n\n{workflow_prompt}"
            )
            kwargs["response_format"] = response_format
            kwargs["tools"] = [
                tool
                for tool in tools
                if getattr(tool, "__name__", "") not in _WORKFLOW_EXCLUDED_TOOLS
            ]
        return create_deep_agent(**kwargs)


_WAG_RESPONSE_FORMAT = ToolStrategy(
    PhilipsWgqRecognitionResult,
    tool_message_content="已记录飞利浦外高桥识别结果。",
)

_DK_RESPONSE_FORMAT = ToolStrategy(
    TecanOverseasRecognitionResult,
    tool_message_content="已记录帝肯境外供应链识别结果。",
)

_WORKFLOW_CONFIGS = {
    WAG_WORKFLOW: (WAG_WORKFLOW_PROMPT, _WAG_RESPONSE_FORMAT),
    DK_WORKFLOW: (DK_WORKFLOW_PROMPT, _DK_RESPONSE_FORMAT),
}
