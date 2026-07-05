# 项目总览

> 本文件只放 AGENTS.md 未覆盖的增量信息（当前重点、技术栈指针、源码阅读入口）。项目定位、关键约定（`uv` 包管理、扁平模块、无 `from session import run_session`）见 [`AGENTS.md`](../AGENTS.md)。

## 当前重点

- 对话短期上下文：LangGraph `checkpointer` + `thread_id=session_id`
- 本地 SQLite：只保留窄用途 run ledger `dsagents_runs.db`
- HTTP：`POST /runs`、`GET /runs/{run_id}`、`POST /files`

## 技术栈指针

完整技术栈（Python 版本、`uv` + setuptools、FastAPI/uvicorn、`deepagents`/`langchain`/`langgraph`/`langchain-anthropic`、SQLite、`requests`）见 [`coding_maps/SYSTEM_MAP.md`](../coding_maps/SYSTEM_MAP.md) §2 与 [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)。

## 源码阅读入口

- 运行时主链：`backend/harness.py`
- Run 持久化：`backend/run_ledger.py`
- HTTP 契约：[INTERFACES.md](../INTERFACES.md)
- 系统地图：[coding_maps/SYSTEM_MAP.md](../coding_maps/SYSTEM_MAP.md)
- 按任务分类的完整阅读顺序：[reading-order.md](reading-order.md)
