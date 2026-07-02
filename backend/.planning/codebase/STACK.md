# 技术栈 (Tech)

> 事实来源：backend/ 源码与 backend/pyproject.toml + uv.lock（2026-07-02 生成）

## 技术栈清单

| 技术 | 用途 | 关键事实（版本 / 来源） |
| --- | --- | --- |
| Python | 运行时 / 实现语言 | 项目自身代码使用现代类型注解（`list[Any]`、`dict[str, Any]`、`X \| None`）；所有 `.py` 第 1 行统一 `from __future__ import annotations`。`backend/pyproject.toml`：`requires-python = ">=3.11,<4.0"`，包管理器为 **uv**（锁文件 `backend/uv.lock`）。 |
| DeepAgents | 可插拔 Brain / 子 Harness；提供 agent 工厂与文件系统后端 | `backend/pyproject.toml` [project.dependencies]：`deepagents>=0.6.12`。`backend/harness.py` 通过 `from deepagents import create_deep_agent` 构建代理；`backend/resources.py` 使用 `from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend`。 |
| LangChain | Agent 中间件与消息/工具类型 | `backend/pyproject.toml` [project.dependencies]：`langchain>=1.3.11`。`backend/hands.py` 用 `from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse`、`from langchain.messages import ToolMessage`、`from langchain.tools.tool_node import ToolCallRequest`。 |
| langchain-core | LangChain 核心抽象（消息/工具/可运行对象基类） | `backend/pyproject.toml` [project.dependencies]：`langchain-core>=1.4.8`。`backend/harness.py` 用 `from langchain_core.messages import RemoveMessage`。项目代码多经 langchain/langgraph 间接使用。 |
| langchain-anthropic | Anthropic 协议客户端（用于接入 MiniMax Anthropic 兼容端点） | `backend/pyproject.toml` [project.dependencies]：`langchain-anthropic>=1.4.8`。`backend/harness.py` 通过 `langchain.chat_models.init_chat_model("anthropic:...")` 构造 `ChatAnthropic`，默认 base url `https://api.minimaxi.com/anthropic`。 |
| LangGraph | 图运行时 / checkpointer / store / Command | `backend/pyproject.toml` [project.dependencies]：`langgraph>=1.2.7`。`backend/harness.py` 用 `from langgraph.graph.message import REMOVE_ALL_MESSAGES`、`backend/hands.py` 用 `from langgraph.types import Command`、`backend/resources.py` 用 `from langgraph.checkpoint.sqlite import SqliteSaver` 与 `from langgraph.store.sqlite import SqliteStore`。 |
| langgraph-checkpoint-sqlite | 基于 SQLite 的 checkpointer | `backend/pyproject.toml` [project.dependencies]：`langgraph-checkpoint-sqlite>=3.1.0`。`backend/resources.py` 用 `SqliteSaver.from_conn_string(...)` 创建，写入 `backend/data/dsagents_checkpoints.db`。 |
| python-dotenv | 加载 `.env` 配置 | `backend/pyproject.toml` [project.dependencies]：`python-dotenv>=1.2.2`。`backend/session.py:15` 在 import 时 `load_dotenv(Path(__file__).with_name(".env"))`。 |
| requests | MinerU 异步任务 HTTP 调用（同步阻塞轮询） | `backend/pyproject.toml` [project.dependencies]：`requests>=2.34.2`。`backend/tools.py` 全量使用 `requests.post` / `requests.get`，含 `timeout` 参数。注：依赖清单列出 requests，但未列出 httpx；项目自身代码无 httpx。 |
| sqlite3（标准库） | 会话事件存储（Session） | `backend/session.py` 直接使用 `import sqlite3`，自建表 `sessions`、`session_events`，文件 `backend/data/dsagents_sessions.db`。 |
| SqliteStore / SqliteSaver（LangGraph 提供） | 持久记忆/历史 Store 与 LangGraph 检查点 | `backend/resources.py`：`SqliteStore.from_conn_string("backend/data/dsagents_store.db")` 并 `.setup()`；`SqliteSaver.from_conn_string("backend/data/dsagents_checkpoints.db")` 并 `.setup()`。 |
| FilesystemBackend（DeepAgents） | 大产物落盘 | `backend/resources.py`：`FilesystemBackend(root_dir=backend/data/artifacts, virtual_mode=True)`，挂载到 `/artifacts/`、`/large_tool_results/`。 |
| CompositeBackend（DeepAgents） | 按路径路由的多后端组合 | `backend/resources.py`：`CompositeBackend(default=StateBackend(), routes={...})`，按前缀路由到 `StoreBackend`（`/memories/`、`/conversation_history/`、`/logs/`）或 `FilesystemBackend`（`/artifacts/`、`/large_tool_results/`）。 |
| StateBackend（DeepAgents） | 默认内存态 / 图状态后端 | `backend/resources.py`：作为 `CompositeBackend` 的 `default`。 |
| MiniMax（Anthropic 兼容）模型 | 默认 LLM 提供方 | `backend/harness.py`：默认 `model="anthropic:{MINIMAX_MODEL or MiniMax-M3}"`，默认 base url `https://api.minimaxi.com/anthropic`；通过 `init_chat_model(..., api_key=..., base_url=...)` 构造 LangChain `ChatAnthropic`，优先读取 `MINIMAX_*` 配置，并兼容 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` fallback。 |
| LangSmith（可选） | 链路追踪 / 可观测 | 仅见于 `backend/.env.example`（`LANGSMITH_TRACING=false`、`LANGSMITH_ENDPOINT`、`LANGSMITH_PROJECT=DsAgents`）；项目自身 `.py` 代码无直接引用，通过 LangChain/LangGraph 运行时间接生效。需确认是否启用。 |

## 技术栈说明

`backend/` 是一个 Harness 级 agent 运行时底座，技术选型围绕"薄运行时 + 可插拔 Brain"展开。核心实现依赖 **DeepAgents**（`>=0.6.12`）作为可插拔的 Brain/子 Harness，借助其 `create_deep_agent` 工厂与 `CompositeBackend / StateBackend / StoreBackend / FilesystemBackend` 后端组合实现"图状态默认走内存、持久历史/记忆走 SQLite Store、大产物走文件系统"的路由策略。图的运行时、checkpoint 与 store 能力来自 **LangGraph**（`>=1.2.7`）与 **LangChain**（`>=1.3.11`，核心抽象 `langchain-core>=1.4.8`），项目通过 `AgentMiddleware`（`TraceMiddleware`）拦截模型/工具调用并产出可审计事件。LLM 接入用 **langchain-anthropic**（`>=1.4.8`），走 MiniMax 的 Anthropic 兼容协议。

数据与持久化有三条独立通道，全部落在本地 **SQLite** 与文件系统，且都固定在 `backend/data/` 下（`resources.py` 用 `_BACKEND_DIR = Path(__file__).resolve().parent` 锁定，与 CWD 无关）：`backend/session.py` 用标准库 `sqlite3` 自建 `backend/data/dsagents_sessions.db`（append-only 会话事件，超 256KiB 的 payload 溢出到 `backend/data/artifacts/session-events/*.json`）；LangGraph 的 `SqliteStore` / `SqliteSaver` 分别落到 `backend/data/dsagents_store.db` 与 `backend/data/dsagents_checkpoints.db`。网络侧只通过 **requests**（同步、`>=2.34.2`）调用 MinerU 的异步任务 API；依赖清单中未声明 `httpx`，项目自身代码亦无 `httpx`、`asyncio`、`async def`，故当前 Milestone 为**同步模型**（MinerU"异步任务"指服务端异步、客户端为阻塞轮询，并非 Python 协程）。

`backend/` 不是常规 Python 包（无 `__init__.py`），而是通过 `pyproject.toml` 的 `[tool.setuptools] package-dir = {"" = "."}` 与 `py-modules = [...]` 声明为**扁平顶层模块**，模块间用绝对导入（`from session import ...`）。配置统一由 `.env` + `python-dotenv` 注入：`session.py:15` 在导入期即 `load_dotenv(Path(__file__).with_name(".env"))`，读取 `backend/.env`。模型默认走 **MiniMax** Anthropic 兼容端点（`MiniMax-M3`，由 LangChain `ChatAnthropic` 承接 Anthropic 兼容协议），项目保留既有 `MINIMAX_*` 配置键并兼容 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` fallback。`.env.example` 另预留了 DeepSeek 与 Oracle、LangSmith、CORS 等键，但 `backend/` 自身源码未引用 DeepSeek / Oracle / CORS / FastAPI，这些属于**规划中或前端边界**，标注需确认。项目以 `backend/pyproject.toml`（可安装项目 `dsagents`，版本 `0.1.0`，`requires-python = ">=3.11,<4.0"`，build-system `setuptools>=68`）+ 锁文件 `backend/uv.lock` 管理依赖，包管理器为 **uv**。
