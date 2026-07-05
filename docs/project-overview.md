# 项目总览

`DsAgents` 当前只有 `backend/` 一个产品子项目。它是一个 **run-first agent runtime**，而不是 session-event replay runtime。

## 当前重点

- 对话短期上下文：LangGraph `checkpointer` + `thread_id=session_id`
- 本地 SQLite：只保留窄用途 run ledger `dsagents_runs.db`
- HTTP：`POST /runs`、`GET /runs/{run_id}`、`POST /files`

## 目录形态

`backend/` 采用扁平顶层模块，不是 Python 包；没有 `__init__.py` / `__main__.py`。模块内继续用 `from harness import ...` 这类绝对导入。

## 常用入口

- 自检：`python backend/self_check.py`
- HTTP：`cd backend && uv run uvicorn api:app --host 0.0.0.0 --port 8000`

程序内如需调用，不再使用 `from session import run_session`；改为显式组合 `AgentResources` + `create_harness(resources).execute_run(...)`。

## 阅读顺序

- 运行时主链：`backend/harness.py`
- Run 持久化：`backend/run_ledger.py`
- HTTP 契约：[INTERFACES.md](../INTERFACES.md)
- 系统地图：[coding_maps/SYSTEM_MAP.md](../coding_maps/SYSTEM_MAP.md)
