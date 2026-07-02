# 技术栈 (Tech)

> 事实来源：backend/ 源码与 backend/pyproject.toml + backend/uv.lock（2026-07-02 生成；原 requirements.txt 已废弃）

## 技术栈清单

| 技术 | 用途 | 关键事实（版本 / 来源） |
| --- | --- | --- |
| Python | 运行时 / 实现语言 | 项目自身代码使用现代类型注解（`list[Any]`、`dict[str, Any]`、`X \| None`）；`backend/hands.py`、`backend/resources.py` 使用 `from __future__ import annotations`。`backend/pyproject.toml`：`requires-python = ">=3.11,<4.0"`，包管理器为 **uv**（锁文件 `backend/uv.lock`）。 |
| DeepAgents | 可插拔 Brain / 子 Harness；提供 agent 工厂与文件系统后端 | `backend/pyproject.toml` [project.dependencies]：`deepagents>=0.6.12`。`backend/harness.py` 通过 `from deepagents import create_deep_agent` 构建代理；`backend/resources.py` 使用 `from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend`。 |
| LangChain | Agent 中间件与消息/工具类型 | `backend/pyproject.toml` [project.dependencies]：`langchain>=1.3.11`。`backend/hands.py` 用 `from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse`、`from langchain.messages import ToolMessage`、`from langchain.tools.tool_node import ToolCallRequest`。 |
| langchain-core | LangChain 核心抽象（消息/工具/可运行对象基类） | `backend/pyproject.toml` [project.dependencies]：`langchain-core>=1.4.8`。作为 langchain/langgraph 的基础依赖，提供 `Runnable`、消息与工具的核心类型；项目代码多经 langchain 间接使用。 |
| langchain-openai | OpenAI 兼容协议客户端（用于接入 MiniMax） | `backend/pyproject.toml` [project.dependencies]：`langchain-openai>=0.3.0`。供 Brain 以 OpenAI 兼容协议调用 MiniMax 端点（`harness.py` 默认模型 `openai:{MINIMAX_MODEL}`、base url `https://api.minimaxi.com/v1`）。 |
| LangGraph | 图运行时 / checkpointer / store / Command | `backend/pyproject.toml` [project.dependencies]：`langgraph>=1.2.7`。`backend/harness.py` 用 `from langgraph.graph.message import REMOVE_ALL_MESSAGES`、`backend/hands.py` 用 `from langgraph.types import Command`、`backend/resources.py` 用 `from langgraph.checkpoint.sqlite import SqliteSaver` 与 `from langgraph.store.sqlite import SqliteStore`。 |
| langgraph-checkpoint-sqlite | 基于 SQLite 的 checkpointer | `backend/pyproject.toml` [project.dependencies]：`langgraph-checkpoint-sqlite>=3.1.0`。`backend/resources.py` 用 `SqliteSaver.from_conn_string(...)` 创建，写入 `data/dsagents_checkpoints.db`。 |
| python-dotenv | 加载 `.env` 配置 | `backend/pyproject.toml` [project.dependencies]：`python-dotenv>=1.2.2`。`backend/__init__.py` 在 import 时 `load_dotenv(Path(__file__).with_name(".env"))`。 |
| requests | MinerU 异步任务 HTTP 调用（同步阻塞轮询） | `backend/pyproject.toml` [project.dependencies]：`requests>=2.34.2`。`backend/tools.py` 全量使用 `requests.post` / `requests.get`，含 `timeout` 参数。注：依赖清单列出 requests，但未列出 httpx；项目自身代码无 httpx。 |
| sqlite3（标准库） | 会话事件存储（Session） | `backend/session.py` 直接使用 `import sqlite3`，自建表 `sessions`、`session_events`，文件 `data/dsagents_sessions.db`。 |
| SqliteStore / SqliteSaver（LangGraph 提供） | 持久记忆/历史 Store 与 LangGraph 检查点 | `backend/resources.py`：`SqliteStore.from_conn_string("data/dsagents_store.db")` 并 `.setup()`；`SqliteSaver.from_conn_string("data/dsagents_checkpoints.db")` 并 `.setup()`。 |
| FilesystemBackend（DeepAgents） | 大产物落盘 | `backend/resources.py`：`FilesystemBackend(root_dir=data/artifacts, virtual_mode=True)`，挂载到 `/artifacts/`、`/large_tool_results/`。 |
| CompositeBackend（DeepAgents） | 按路径路由的多后端组合 | `backend/resources.py`：`CompositeBackend(default=StateBackend(), routes={...})`，按前缀路由到 `StoreBackend`（`/memories/`、`/conversation_history/`、`/logs/`）或 `FilesystemBackend`（`/artifacts/`、`/large_tool_results/`）。 |
| StateBackend（DeepAgents） | 默认内存态 / 图状态后端 | `backend/resources.py`：作为 `CompositeBackend` 的 `default`。 |
| MiniMax（OpenAI 兼容）模型 | 默认 LLM 提供方 | `backend/harness.py`：默认 `model="openai:{MINIMAX_MODEL or MiniMax-M3}"`，默认 base url `https://api.minimaxi.com/v1`；当 `MINIMAX_API_KEY` 存在时回填到 `OPENAI_API_KEY` / `OPENAI_API_BASE`。 |
| LangSmith（可选） | 链路追踪 / 可观测 | 仅见于 `backend/.env.example`（`LANGSMITH_TRACING=false`、`LANGSMITH_ENDPOINT`、`LANGSMITH_PROJECT=DsAgents`）；项目自身 `.py` 代码无直接引用，通过 LangChain/LangGraph 运行时间接生效。需确认是否启用。 |

## 技术栈说明

`backend/` 是一个 Harness 级 agent 运行时底座，技术选型围绕"薄运行时 + 可插拔 Brain"展开。核心实现依赖 **DeepAgents**（`>=0.6.12`）作为可插拔的 Brain/子 Harness，借助其 `create_deep_agent` 工厂与 `CompositeBackend / StateBackend / StoreBackend / FilesystemBackend` 后端组合实现"图状态默认走内存、持久历史/记忆走 SQLite Store、大产物走文件系统"的路由策略。图的运行时、checkpoint 与 store 能力来自 **LangGraph**（`>=1.2.7`）与 **LangChain**（`>=1.3.11`，核心抽象 `langchain-core>=1.4.8`），项目通过 `AgentMiddleware`（`TraceMiddleware`）拦截模型/工具调用并产出可审计事件。LLM 接入新增 **langchain-openai**（`>=0.3.0`），用于走 OpenAI 兼容协议调用 MiniMax。

数据与持久化有三条独立通道，全部落在本地 **SQLite** 与文件系统：`backend/session.py` 用标准库 `sqlite3` 自建 `data/dsagents_sessions.db`（append-only 会话事件，超 256KiB 的 payload 溢出到 `data/artifacts/session-events/*.json`）；LangGraph 的 `SqliteStore` / `SqliteSaver` 分别落到 `data/dsagents_store.db` 与 `data/dsagents_checkpoints.db`。网络侧只通过 **requests**（同步、`>=2.34.2`）调用 MinerU 的异步任务 API；依赖清单中未声明 `httpx`，项目自身代码亦无 `httpx`、`asyncio`、`async def`，故当前 Milestone 为**同步模型**（MinerU"异步任务"指服务端异步、客户端为阻塞轮询，并非 Python 协程）。

配置统一由 `.env` + `python-dotenv` 注入：`backend/__init__.py` 在导入期即 `load_dotenv`。模型默认走 **MiniMax** OpenAI 兼容端点（`MiniMax-M3`，由 langchain-openai 承接 OpenAI 兼容协议），`.env.example` 另预留了 DeepSeek 与 Oracle、LangSmith、CORS 等键，但 `backend/` 自身源码未引用 DeepSeek / Oracle / CORS / FastAPI，这些属于**规划中或前端边界**，标注需确认。项目以 `backend/pyproject.toml`（可安装包 `dsagents`，版本 `0.1.0`，`requires-python = ">=3.11,<4.0"`，build-system `setuptools>=68`）+ 锁文件 `backend/uv.lock` 管理依赖，包管理器为 **uv**；仓库根的 `requirements.txt` 已废弃删除。
