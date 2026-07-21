# STACK — backend 技术栈事实

> Analysis Date: 2026-07-22。本文描述 `backend/` 当前权威源码；不把历史 `build/` 产物当作实现。

## 运行时与包管理

| 项目 | 当前事实 |
|---|---|
| 语言 | Python `>=3.11,<4.0` |
| 包管理 | `uv`；锁定依赖以 `backend/uv.lock` 为准 |
| 发行名 | `dsagents`；源码顶层为 `api.py`、`runtime/`、`integrations/`、`skills/` |
| HTTP | FastAPI + uvicorn，轮询四端点，无 SSE |
| Agent | DeepAgents、LangChain、LangGraph；`create_deep_agent(...)` 由 `DeepAgentsBrainFactory` 创建 |
| 状态 | SQLite run ledger、LangGraph SQLite checkpointer、LangGraph SQLite store 三库分离 |

## 关键依赖与用途

| 依赖 | 用途 |
|---|---|
| `deepagents` | Agent harness、Skill、memory、虚拟文件系统与默认工具 |
| `langchain` / `langchain-core` / `langchain-anthropic` | Chat model、ToolStrategy、middleware |
| `langgraph` / `langgraph-checkpoint-sqlite` | stream、RunControl、checkpointer、store |
| `fastapi` / `uvicorn` / `python-multipart` | HTTP 接口与上传 |
| `requests` | MinerU HTTP 调用 |
| `openpyxl` | 只读 XLSX 材料并转为 JSON artifact；不生成 Excel |
| `oracledb` | Philips 可选主数据补齐；可用 thick mode |
| `python-dotenv` | 从 `backend/.env` 加载本地模型配置 |

## 运行时装配

- `runtime.execution.create_harness()` 创建 `HarnessRuntime`、`default_tool_catalog()` 与 `DeepAgentsBrainFactory`。
- `ToolCatalog` 静态注册五个 callable：`parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`、`inspect_supply_chain_workbooks`、`finalize_tecan_overseas_recognition`。
- `workflow=philips_wgq_inbound_recognition` 使用 `ToolStrategy(PhilipsWgqRecognitionResult)`；Tecan 不新增 HTTP workflow，由 `/skills/tecan-import/SKILL.md` 驱动并以专用 finalizer 写入 `run.result`。
- DeepAgents 默认 general-purpose subagent 已关闭，当前不配置业务 SubAgent；单票归集在同一主 Agent run 内完成。
- 主 Agent 以 `thread_id=session_id` 使用 checkpointer。run ledger 承担外部终态和事件投影；没有额外消息状态表或业务任务状态机。

## Middleware 与状态取舍

- `StructuredOutputRecovery` 是 Philips 专用的 class-based `after_model` hook：需读/改完整消息状态、恢复文本 JSON，并通过 `jump_to` 结束或重试。
- `ToolTelemetry`、`NoProgressMiddleware`、`StructuredOutputCompatibility` 仍是运行时通用横切能力；Memory middleware 只在主 Agent 绑定 store backend 时追加。
- Tecan 的同票识别、字段证据和 outcome 属于业务合同，放在 Skill 提示词和 `finalize_tecan_overseas_recognition` Pydantic 校验，而不是全局 middleware 或新的 graph state。
- 这一分层避免同一事实同时存在于 LangGraph state、业务状态表和 run ledger；只有真实跨 run 暂停/恢复需求出现时才重新评估。

## 配置与平台前提

- `.env` 中仅由环境读取模型 provider 配置；文档不记录其值。
- MinerU 通过现有 HTTP 配置调用；外部失败按工具问题返回或使当前 run 失败，不添加旁路队列。
- Oracle thick mode 依赖 `ORACLE_CLIENT_LIB_DIR`；缺失时不阻塞已能确认的 Philips 结果，详情见 `CONCERNS.md`。
- 运行数据默认落在 `backend/data/` 与 `backend/log/`；日志目录不入 VCS。

## 常用命令

```powershell
cd backend
uv sync
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
```

根目录文档变更另跑 `git diff --check`。真实模型、MinerU、Oracle 与本地回归分开执行。
