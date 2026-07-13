# 项目总览

> 本文件承接 AGENTS.md 的详细说明（当前重点、技术栈指针、源码阅读入口）。项目定位、关键约定（`uv` 包管理、Python 包 `dsagents/`、无 `from session import run_session`）见 [`AGENTS.md`](../AGENTS.md)。

## 当前重点

- 对话短期上下文：LangGraph `checkpointer` + `thread_id=session_id`
- 本地 SQLite：run ledger + LangGraph store/checkpointer，路径固定在 `backend/data/`，文件按需创建；新 schema 使用 UTC ISO-8601 毫秒时间
- HTTP：`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`、`POST /upload`，启动 `uvicorn dsagents.api:app`
- 业务能力：Philips 外高桥与 Tecan 进口 Skills；每个 Skill 仅暴露 `save_*_extraction` + `generate_*_import` 两个 Tool，业务问题统一 `input_problems`

## 技术栈指针

完整技术栈（含 DeepAgents/LangGraph、SQLite、MinerU、openpyxl 与可选 oracledb）见 [`coding_maps/SYSTEM_MAP.md`](../coding_maps/SYSTEM_MAP.md) §2 与 [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)。

## 源码阅读入口

- 运行时主链：`backend/dsagents/runtime/execution.py`（`HarnessRuntime.execute_run`）、`backend/dsagents/runtime/agent.py`（Brain/SubAgent/middleware）
- 业务 Skill/规则：`backend/dsagents/skills/philipswgqimport/`、`backend/dsagents/skills/tecanimport/`（各含 `SKILL.md` + `scripts/{tools.py,documents.py}` + `references/` + `assets/`）
- artifact 基础设施：`backend/dsagents/integrations/artifacts.py` — 文件名清洗、artifact 虚拟路径与物理路径互转、原子落盘
- MinerU 集成：`backend/dsagents/integrations/mineru.py` — `parse_documents` / `extract_archives`
- Run 持久化：`backend/dsagents/runtime/runs.py`
- HTTP 契约：[INTERFACES.md](../INTERFACES.md)
- 系统地图：[coding_maps/SYSTEM_MAP.md](../coding_maps/SYSTEM_MAP.md)
- 按任务分类的完整阅读顺序：[reading-order.md](reading-order.md)
