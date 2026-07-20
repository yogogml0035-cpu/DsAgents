# 项目总览

> 本文件承接 AGENTS.md 的详细说明（当前重点、技术栈指针、源码阅读入口）。项目定位、关键约定见 [`AGENTS.md`](../AGENTS.md)。

## 当前重点

- 对话短期上下文：LangGraph `checkpointer` + `thread_id=session_id`
- 本地 SQLite：run ledger + LangGraph store/checkpointer，路径固定在 `backend/data/`，文件按需创建；新 schema 使用中国时区 UTC+8 本地时间 `YYYY-MM-DD HH:MM:SS`（与 OMS 一致）；三库互不共享连接
- HTTP：`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`、`POST /upload`；`POST /runs` 可选固定 Philips workflow；成功 `create_run` 后 best-effort OMS 旁路索引；无 SSE、无鉴权/CORS
- 事件：固定 7 类；GET 返回快照、顶层 `workflow`/`result`、增量 events、`latest_content_event` 与 `usage`；OMS JSONL **不是** run event
- 业务能力：Philips 外高桥用 `philips_wgq_inbound_recognition` + 单一主数据 Tool 返回结构化 JSON；workflow 工具收窄用 denylist（保留共享 MinerU）；Tecan 保留 2 Tool 与 A/B extractor/Excel
- middleware：集中在 `runtime/middleware.py`（含 `StructuredOutputRecovery` 有界重试、空 data 壳按 `tool_call_id` 同回合恢复、空壳耗尽 all-null skeleton；SubAgent 默认 recovery schema 仍是 Philips）
- 源码布局：`api.py` + `runtime/`（含 `oms_log.py`）+ `integrations/` + `skills/`（发行名 `dsagents`）；Skill 为 kebab 资源目录 + 可 import 包成对；OMS 日志 `backend/log/oms_log.log`

## 技术栈指针

完整技术栈（含 DeepAgents/LangGraph、SQLite、MinerU、openpyxl 与可选 oracledb）见 [`coding_maps/SYSTEM_MAP.md`](../coding_maps/SYSTEM_MAP.md) §2 与 [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)。

## 源码阅读入口

- 运行时主链：`backend/runtime/execution.py`（`HarnessRuntime.execute_run`）、`backend/runtime/agent.py`（Brain/SubAgent 装配）、`backend/runtime/middleware.py`（middleware hook）
- Philips 识别（成对）：资源 `backend/skills/philips-wgq-inbound-recognition/SKILL.md` + 包 `backend/skills/philipswgqinboundrecognition/`（`schema.py` + `scripts/tools.py`），以及 [`philips-wgq-inbound-recognition-prd.md`](philips-wgq-inbound-recognition-prd.md)
- Tecan（成对）：资源 `backend/skills/tecan-import/`（`SKILL.md` + `references/` + `assets/`）+ 包 `backend/skills/tecanimport/scripts/{tools.py,documents.py}`
- artifact 基础设施：`backend/integrations/artifacts.py` — 文件名清洗、artifact 虚拟路径与物理路径互转、原子落盘
- MinerU 集成：`backend/integrations/mineru.py` — `parse_documents` / `extract_archives`
- Run 持久化：`backend/runtime/runs.py`
- HTTP 契约：[INTERFACES.md](../INTERFACES.md)
- 系统地图：[coding_maps/SYSTEM_MAP.md](../coding_maps/SYSTEM_MAP.md)
- 按任务分类的完整阅读顺序：[reading-order.md](reading-order.md)
