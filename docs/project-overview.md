# 项目总览

> 本文件承接 AGENTS.md 的详细说明（当前重点、技术栈指针、源码阅读入口）。项目定位、关键约定（`uv` 包管理、扁平模块、无 `from session import run_session`）见 [`AGENTS.md`](../AGENTS.md)。

## 当前重点

- 对话短期上下文：LangGraph `checkpointer` + `thread_id=session_id`
- 本地 SQLite：run ledger + LangGraph store/checkpointer，路径固定在 `backend/data/`，文件按需创建
- HTTP：`POST /runs`、`GET /runs/{run_id}`、`POST /upload`
- 业务能力：Philips 外高桥与 Tecan 进口 Skills；临时 A/B extractor + 确定性 canonical/Excel 工具，状态只存唯一 artifact

## 技术栈指针

完整技术栈（含 DeepAgents/LangGraph、SQLite、MinerU、openpyxl 与可选 oracledb）见 [`coding_maps/SYSTEM_MAP.md`](../coding_maps/SYSTEM_MAP.md) §2 与 [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)。

## 源码阅读入口

- 运行时主链：`backend/harness.py`
- 业务 Skill/规则：`backend/skills/`、`backend/philips_wgq_import.py`、`backend/tecan_import.py`
- artifact 基础设施：`backend/artifact_names.py` — 文件名清洗与去重；`backend/workflow_artifacts.py` — workflow artifact 虚拟路径与物理路径互转、原子落盘
- Run 持久化：`backend/run_ledger.py`
- HTTP 契约：[INTERFACES.md](../INTERFACES.md)
- 系统地图：[coding_maps/SYSTEM_MAP.md](../coding_maps/SYSTEM_MAP.md)
- 按任务分类的完整阅读顺序：[reading-order.md](reading-order.md)
