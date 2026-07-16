# 项目总览

> 本文件承接 AGENTS.md 的详细说明（当前重点、技术栈指针、源码阅读入口）。项目定位、关键约定见 [`AGENTS.md`](../AGENTS.md)。

## 当前重点

- 对话短期上下文：LangGraph `checkpointer` + `thread_id=session_id`
- 本地 SQLite：run ledger + LangGraph store/checkpointer，路径固定在 `backend/data/`，文件按需创建；新 schema 使用 UTC ISO-8601 毫秒时间；三库互不共享连接
- HTTP：`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`、`POST /upload`；`POST /runs` 可选固定 Philips workflow；无 SSE、无鉴权/CORS
- 事件：固定 7 类；GET 返回快照、顶层 `workflow`/`result`、增量 events、`latest_content_event` 与 `usage`
- 业务能力：Philips 外高桥用 `philips_wgq_inbound_recognition` + 单一主数据 Tool 返回结构化 JSON；Tecan 保留 2 Tool 与 A/B extractor/Excel
- 源码布局：`api.py` + `runtime/` + `integrations/` + `skills/`（发行名 `dsagents`）

## 技术栈指针

完整技术栈（含 DeepAgents/LangGraph、SQLite、MinerU、openpyxl 与可选 oracledb）见 [`coding_maps/SYSTEM_MAP.md`](../coding_maps/SYSTEM_MAP.md) §2 与 [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)。

## 源码阅读入口

- 运行时主链：`backend/runtime/execution.py`（`HarnessRuntime.execute_run`）、`backend/runtime/agent.py`（Brain/SubAgent 装配）、`backend/runtime/middleware.py`（middleware hook）
- Philips 识别：`backend/skills/philipswgqinboundrecognition/`（`SKILL.md` + `schema.py` + `scripts/tools.py`）及 [`philips-wgq-inbound-recognition-prd.md`](philips-wgq-inbound-recognition-prd.md)
- Tecan：`backend/skills/tecanimport/`（`SKILL.md` + `scripts/{tools.py,documents.py}` + `references/` + `assets/`）
- artifact 基础设施：`backend/integrations/artifacts.py` — 文件名清洗、artifact 虚拟路径与物理路径互转、原子落盘
- MinerU 集成：`backend/integrations/mineru.py` — `parse_documents` / `extract_archives`
- Run 持久化：`backend/runtime/runs.py`
- HTTP 契约：[INTERFACES.md](../INTERFACES.md)
- 系统地图：[coding_maps/SYSTEM_MAP.md](../coding_maps/SYSTEM_MAP.md)
- 按任务分类的完整阅读顺序：[reading-order.md](reading-order.md)
