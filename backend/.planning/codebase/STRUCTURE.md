# STRUCTURE

## 1. 顶层文件

| 文件 | 当前职责 |
|------|----------|
| `api.py` | FastAPI run-first HTTP 层 |
| `hands.py` | `ToolStatusMiddleware` |
| `harness.py` | Brain 装配与 stream 规范化 |
| `resources.py` | 资源装配 |
| `run_ledger.py` | SQLite run ledger |
| `tools.py` | `parse_document` |
| `self_check.py` | 端到端自检 |

已删除：

- `session.py`
- `tests/test_stream_typing.py`

## 2. 数据目录

| 路径 | 内容 |
|------|------|
| `backend/data/dsagents_runs.db` | run ledger |
| `backend/data/dsagents_store.db` | LangGraph store |
| `backend/data/dsagents_checkpoints.db` | LangGraph checkpointer |
| `backend/data/artifacts/run-events/` | 大 run event 外溢文件 |
| `backend/data/artifacts/uploads/` | 上传文件 |

## 3. 模块形态

`backend/` 不是包，仍是扁平顶层模块：

- 没有 `__init__.py`
- 没有 `__main__.py`
- `pyproject.toml` 当前 `py-modules` 为 `api/hands/harness/resources/run_ledger/tools/self_check`

## 4. 对外入口

- HTTP：
  - `POST /runs`
  - `GET /runs/{run_id}`
  - `POST /files`
- 自检：
  - `python backend/self_check.py`

程序内没有单函数 one-shot 入口；需要显式组合 `AgentResources`、`create_harness(resources)` 与 `resources.runs`。
