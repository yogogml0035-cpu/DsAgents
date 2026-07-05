# STACK

## 1. Python 与打包

- Python：`>=3.11,<4.0`
- 包管理：`uv`
- 打包：setuptools `py-modules`

当前 `py-modules`：

- `api`
- `hands`
- `harness`
- `resources`
- `run_ledger`
- `tools`
- `self_check`

## 2. 运行时依赖

- `deepagents`
- `fastapi`
- `langchain`
- `langchain-anthropic`
- `langchain-core`
- `langgraph`
- `langgraph-checkpoint-sqlite`
- `python-dotenv`
- `python-multipart`
- `requests`
- `uvicorn`

## 3. 本地持久化

- 标准库 `sqlite3`：`run_ledger.py`
- `SqliteStore`：LangGraph store
- `SqliteSaver`：LangGraph checkpointer
- 文件系统：上传文件与大 run event 外溢

## 4. 配置加载

`.env` 由以下模块在导入时加载：

- `harness.py`
- `tools.py`

## 5. 当前运行模型

- HTTP handler 是同步 `def`
- 后台 run 用 `threading.Thread`
- 并发保护用 `threading.Lock`
- stream API 使用 LangGraph classic `stream(..., version="v2")`
