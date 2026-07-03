# 技术栈 (Tech)

> 事实来源：backend/ 源码 + pyproject.toml/uv.lock（2026-07-03 刷新）

## 技术栈清单

| 技术 | 用途 | 关键事实（版本 / 来源） |
| --- | --- | --- |
| Python | 运行时 / 实现语言 | `backend/pyproject.toml`：`requires-python = ">=3.11,<4.0"`。项目自身代码使用现代类型注解（`list[Any]`、`dict[str, Any]`、`X \| None`）；所有 `.py` 第 1 行统一 `from __future__ import annotations`。包管理器为 **uv**，锁文件 `backend/uv.lock`。 |
| DeepAgents | 可插拔 Brain / 子 Harness；提供 agent 工厂与文件系统后端 | pyproject 声明 `deepagents>=0.6.12`；uv.lock 锁定 **0.6.12**。`backend/harness.py` 通过 `from deepagents import create_deep_agent` 构建代理；`backend/resources.py` 使用 `from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend`。 |
| FastAPI | HTTP API 层（阻塞/后台 run、SSE 流式、文件上传、run 轮询） | pyproject 声明 `fastapi>=0.116.1`；uv.lock 锁定 **0.139.0**。`backend/api.py` 定义 6 个端点，含 `lifespan` 启动钩子、`threading.Thread` 后台 run、`StreamingResponse` 手写 SSE。 |
| LangChain | Agent 中间件与消息/工具类型；模型构造入口 | pyproject 声明 `langchain>=1.3.11`；uv.lock 锁定 **1.3.11**。`backend/hands.py` 用 `from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse`、`from langchain.tools.tool_node import ToolCallRequest`；`backend/harness.py` 用 `from langchain.chat_models import init_chat_model` 构造模型。 |
| langchain-core | LangChain 核心抽象（消息/工具/可运行对象基类） | pyproject 声明 `langchain-core>=1.4.8`；uv.lock 锁定 **1.4.8**。`backend/harness.py` 用 `from langchain_core.messages import RemoveMessage`、`from langchain_core.language_models import BaseChatModel`。项目代码多经 langchain/langgraph 间接使用。 |
| langchain-anthropic | Anthropic 协议客户端 ChatAnthropic（接入 MiniMax Anthropic 兼容端点） | pyproject 声明 `langchain-anthropic>=1.4.8`；uv.lock 锁定 **1.4.8**。`backend/harness.py` 经 `init_chat_model(f"anthropic:{MINIMAX_MODEL}", ...)` 复用 LangChain 的 Anthropic provider 适配构造 `ChatAnthropic`。`base_url` 来自 `MINIMAX_BASE_URL`，**无内置默认值**；默认模型固定传 `thinking={"type": "adaptive"}`。 |
| LangGraph | 图运行时 / checkpointer / store / Command | pyproject 声明 `langgraph>=1.2.7`；uv.lock 锁定 **1.2.7**。`backend/harness.py` 用 `from langgraph.graph.message import REMOVE_ALL_MESSAGES`、`backend/hands.py` 用 `from langgraph.types import Command`、`backend/hands.py` 用 `from langgraph.config import get_stream_writer`、`backend/resources.py` 用 `from langgraph.checkpoint.sqlite import SqliteSaver` 与 `from langgraph.store.sqlite import SqliteStore`。 |
| langgraph-checkpoint-sqlite | 基于 SQLite 的 checkpointer | pyproject 声明 `langgraph-checkpoint-sqlite>=3.1.0`；uv.lock 锁定 **3.1.0**。`backend/resources.py` 用 `SqliteSaver.from_conn_string(...)` 创建，写入 `backend/data/dsagents_checkpoints.db`。 |
| python-multipart | FastAPI multipart 上传解析 | pyproject 声明 `python-multipart>=0.0.20`；uv.lock 锁定 **0.0.32**。`backend/api.py::post_file` 通过 `UploadFile = File(...)` 接收 `multipart/form-data`。 |
| python-dotenv | 加载 `.env` 配置 | pyproject 声明 `python-dotenv>=1.2.2`；uv.lock 锁定 **1.2.2**。`backend/session.py:14` 与 `backend/tools.py:16` 在 import 时 `load_dotenv(Path(__file__).with_name(".env"))`，读取 `backend/.env`。 |
| requests | MinerU 异步任务 HTTP 调用（同步阻塞轮询） | pyproject 声明 `requests>=2.34.2`；uv.lock 锁定 **2.34.2**。`backend/tools.py` 全量使用 `requests.post` / `requests.get`，含 `timeout` 参数。注：依赖清单列出 requests，但未列出 httpx；项目自身代码无 httpx，亦无 `async`/`await`/`asyncio`（HTTP 层是同步 `def` 端点 + `threading.Thread` 后台 run，非协程）。 |
| uvicorn | FastAPI ASGI server 入口 | pyproject 声明 `uvicorn>=0.35.0`；uv.lock 锁定 **0.49.0**。推荐命令：`cd backend && uv run uvicorn api:app --host 0.0.0.0 --port 8000`。 |
| sqlite3（标准库） | 会话事件存储（Session + Run） | `backend/session.py` 直接使用 `import sqlite3`，自建表 `sessions`、`session_events`、`runs`、`run_events`，文件 `backend/data/dsagents_sessions.db`。 |
| SqliteStore / SqliteSaver（LangGraph 提供） | 持久记忆/历史 Store 与 LangGraph 检查点 | `backend/resources.py`：`SqliteStore.from_conn_string("backend/data/dsagents_store.db")` 并 `.setup()`；`SqliteSaver.from_conn_string("backend/data/dsagents_checkpoints.db")` 并 `.setup()`。 |
| FilesystemBackend（DeepAgents） | 大产物落盘 | `backend/resources.py`：`FilesystemBackend(root_dir=backend/data/artifacts, virtual_mode=True)`，挂载到 `/artifacts/`、`/large_tool_results/`。 |
| CompositeBackend（DeepAgents） | 按路径路由的多后端组合 | `backend/resources.py`：`CompositeBackend(default=StateBackend(), routes={...})`，按前缀路由到 `StoreBackend`（`/memories/`、`/conversation_history/`、`/logs/`）或 `FilesystemBackend`（`/artifacts/`、`/large_tool_results/`）。 |
| StateBackend（DeepAgents） | 默认内存态 / 图状态后端 | `backend/resources.py`：作为 `CompositeBackend` 的 `default`。 |
| MiniMax（Anthropic 兼容）模型 | 默认 LLM 提供方 | `backend/harness.py` 的 `DeepAgentsBrainFactory.__init__`（当 `model is None` 时）：`init_chat_model(f"anthropic:{os.getenv('MINIMAX_MODEL')}", api_key=os.getenv("MINIMAX_API_KEY"), base_url=os.getenv("MINIMAX_BASE_URL"), thinking={"type": "adaptive"})` 直接构造 LangChain `ChatAnthropic`。**仅**读取 `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 三个 env，**无任何默认值、无任何 fallback**（提交 `9c78cf2` 已显式移除回退逻辑；旧文档中的 `ANTHROPIC_*` 回退与 `MINIMAX_*`→`OPENAI_*` 复制均为过时描述）。`thinking` 固定为 `{"type": "adaptive"}`，流式经 `_thinking_delta` 提取 `thinking`/`reasoning` 内容块。 |
| LangSmith（可选） | 链路追踪 / 可观测 | **需确认**：仅见于 `backend/.env.example`（`LANGSMITH_TRACING=false`、`LANGSMITH_ENDPOINT`、`LANGSMITH_PROJECT=DsAgents`、`LANGSMITH_API_KEY`）；项目自身 `.py` 代码无直接引用，通过 LangChain/LangGraph 运行时间接生效。 |

## 依赖版本对照（pyproject 声明 vs uv.lock 锁定）

| 包名 | pyproject 声明 | uv.lock 锁定 |
| --- | --- | --- |
| deepagents | `>=0.6.12` | `0.6.12` |
| fastapi | `>=0.116.1` | `0.139.0` |
| langchain | `>=1.3.11` | `1.3.11` |
| langchain-anthropic | `>=1.4.8` | `1.4.8` |
| langchain-core | `>=1.4.8` | `1.4.8` |
| langgraph | `>=1.2.7` | `1.2.7` |
| langgraph-checkpoint-sqlite | `>=3.1.0` | `3.1.0` |
| python-multipart | `>=0.0.20` | `0.0.32` |
| python-dotenv | `>=1.2.2` | `1.2.2` |
| requests | `>=2.34.2` | `2.34.2` |
| uvicorn | `>=0.35.0` | `0.49.0` |

## 技术栈说明

`backend/` 是一个 Harness 级 agent 运行时底座，技术选型围绕"薄运行时 + 可插拔 Brain"展开。核心实现依赖 **DeepAgents**（`0.6.12`）作为可插拔的 Brain/子 Harness，借助其 `create_deep_agent` 工厂与 `CompositeBackend / StateBackend / StoreBackend / FilesystemBackend` 后端组合实现"图状态默认走内存、持久历史/记忆走 SQLite Store、大产物走文件系统"的路由策略。图的运行时、checkpoint 与 store 能力来自 **LangGraph**（`1.2.7`）与 **LangChain**（`1.3.11`，核心抽象 `langchain-core 1.4.8`），项目通过 `AgentMiddleware`（`TraceMiddleware`）拦截模型/工具调用并产出可审计事件。LLM 接入用 **langchain-anthropic**（`1.4.8`），走 MiniMax 的 Anthropic 兼容协议——经 `langchain.chat_models.init_chat_model(f"anthropic:{MINIMAX_MODEL}", ..., thinking={"type": "adaptive"})` 复用 LangChain 的 Anthropic provider 适配，而不是自行包装 `anthropic` SDK。

HTTP 层用 **FastAPI**（`0.139.0`）+ **uvicorn**（`0.49.0`），采用 **run 为中心的同步模型**：所有端点都是同步 `def`（非 `async def`），后台 run 用 `threading.Thread` 实现，同一 session 的并发 run 经 `threading.Lock` 单飞（冲突返回 `409`）。HTTP 层不持有独立 service/manager，而是在 `lifespan` 启动期创建一次 `AgentResources`（含三条 SQLite 通道 + `CompositeBackend`）与一个 `HarnessRuntime`，存于 `app.state`，所有请求复用同一实例。SSE 用 `StreamingResponse` 手写 `event:` / `data:` 格式，**未引入** `sse-starlette`。

数据与持久化有三条独立通道，全部落在本地 **SQLite** 与文件系统，且都固定在 `backend/data/` 下（`resources.py` 用 `_BACKEND_DIR = Path(__file__).resolve().parent` 锁定，与 CWD 无关）：`backend/session.py` 用标准库 `sqlite3` 自建 `backend/data/dsagents_sessions.db`（append-only 会话事件 + run 事件，超 256KiB 的 payload 溢出到 `backend/data/artifacts/session-events/*.json` 或 `.../run-events/*.json`）；LangGraph 的 `SqliteStore` / `SqliteSaver` 分别落到 `backend/data/dsagents_store.db` 与 `backend/data/dsagents_checkpoints.db`。网络侧只通过 **requests**（同步、`2.34.2`）调用 MinerU 的异步任务 API；依赖清单中未声明 `httpx`，项目自身代码亦无 `httpx`、`asyncio`、`async def`，故当前 Milestone 为**同步模型**（MinerU"异步任务"指服务端异步、客户端为阻塞轮询，并非 Python 协程）。

`backend/` 不是常规 Python 包（无 `__init__.py`），而是通过 `pyproject.toml` 的 `[tool.setuptools] package-dir = {"" = "."}` 与 `py-modules = ["api","hands","harness","resources","session","tools","self_check"]` 声明为**扁平顶层模块**，模块间用绝对导入（`from session import ...`、`from api import app`）。配置统一由 `.env` + `python-dotenv` 注入：`session.py:14` 在导入期即 `load_dotenv(Path(__file__).with_name(".env"))`，读取 `backend/.env`。模型默认走 **MiniMax** Anthropic 兼容端点：`DeepAgentsBrainFactory.__init__` **直接**以 `init_chat_model(f"anthropic:{os.getenv('MINIMAX_MODEL')}", api_key=os.getenv("MINIMAX_API_KEY"), base_url=os.getenv("MINIMAX_BASE_URL"), thinking={"type": "adaptive"})` 构造 `ChatAnthropic`——**只读** `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL`，**无默认模型名、无默认 base url、无任何 fallback**（提交 `9c78cf2` 已移除全部回退逻辑），并固定启用 `adaptive` thinking。`.env.example` 另预留了 DeepSeek / Oracle / LangSmith / CORS_ORIGINS 等键；其中 DeepSeek / Oracle / LangSmith / CORS_ORIGINS 均属**需确认**边界（源码零引用）。

项目以 `backend/pyproject.toml`（可安装项目 `dsagents`，版本 `0.1.0`，描述 "Agent runtime for DeepAgents with pluggable document parsing."，`requires-python = ">=3.11,<4.0"`，build-system `setuptools>=68`）+ 锁文件 `backend/uv.lock` 管理依赖，包管理器为 **uv**（安装：`cd backend && uv sync`；运行：`uv run uvicorn api:app`）。依赖清单中无 pytest/ruff/mypy/black 等独立测试/lint/类型工具；唯一的自检入口是 `backend/self_check.py`（用 `fastapi.testclient.TestClient` + `unittest.mock.patch` 的内置断言脚本，`python -m self_check` 或 `uv run python self_check.py` 运行）。
