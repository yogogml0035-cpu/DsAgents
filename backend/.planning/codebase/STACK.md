# 技术栈 (STACK)

本文件基于 `backend/` 源码与 `requirements.txt` 归纳 DsAgents Agent Harness 后端的技术栈。所有事实均来自代码，未确认处显式标注。

## 语言与运行时
- 实现语言：Python。源码统一使用 `from __future__ import annotations`，并广泛使用 PEP 604 联合类型注解（如 `str | None`、`dict[str, Any]`、`tuple[int, ...]`）与 `@dataclass`（见 `backend/session.py`、`backend/harness.py`、`backend/tools.py`）。
- Python 版本：当前代码未确认。`requirements.txt` 未声明 `python_requires`；依据依赖下限 `langchain>=1.3.11`、`langgraph>=1.2.6`、`langgraph-checkpoint-sqlite>=3.1.0` 推断需 Python 3.10+。
- 进程入口：`python -m backend` 经 `backend/__main__.py` 调用 `backend/session.py::main()`，用 `argparse` 解析用户消息后执行 `run_session()`。另有 `backend/self_check.py` 作为内置自检入口（`python -m backend.self_check`）。

## 核心依赖与其作用

| 依赖（`requirements.txt`） | 角色 |
| --- | --- |
| `deepagents>=0.6.12` | Agent "Brain" 内核。`harness.py` 通过 `create_deep_agent` 组装模型/工具/中间件/后端；`resources.py` 引入 `CompositeBackend`、`FilesystemBackend`、`StateBackend`、`StoreBackend`。可插拔子 Harness。 |
| `langchain>=1.3.11` | 提供 `AgentMiddleware`、`ToolCallRequest`、`ToolMessage`、`langchain.messages` 等。`hands.py` 的 `TraceMiddleware` 继承 `AgentMiddleware`。 |
| `langgraph>=1.2.6` | 图与状态原语。`harness.py` 用 `langgraph.graph.message.REMOVE_ALL_MESSAGES` 与 `RemoveMessage`；`hands.py` 用 `langgraph.types.Command`；`resources.py` 用 `langgraph.store.sqlite.SqliteStore`、`langgraph.checkpoint.sqlite.SqliteSaver`。 |
| `langgraph-checkpoint-sqlite>=3.1.0` | `SqliteSaver` 的实现来源，支撑 LangGraph checkpointer 的持久化。 |
| `requests>=2.34.0` | MinerU HTTP 调用（`backend/tools.py`：POST 上传、轮询状态、取结果）。 |

## DeepAgents 后端组合
`backend/resources.py::AgentResources.__enter__` 构造 `CompositeBackend`：
- `default=StateBackend()`：默认（模型可见的瞬时状态）。
- `StoreBackend(store=SqliteStore, namespace=("dsagents",))`：路由 `/memories/`、`/conversation_history/`、`/logs/` 到持久化 store（AGENTS.md 要求历史/记忆走 StoreBackend）。
- `FilesystemBackend(root_dir=artifacts_dir, virtual_mode=True)`：路由 `/artifacts/`、`/large_tool_results/` 到磁盘（复用 DeepAgents 内建虚拟文件系统）。

## SQLite 持久化
三处本地 `.db` 文件由 `ResourceConfig` 定义（根目录 `data/`）：
- `data/dsagents_sessions.db` — 自建 `SqliteSessionStore`（append-only 事件表 `sessions` / `session_events`）。
- `data/dsagents_store.db` — `SqliteStore`，DeepAgents 记忆/历史/日志。
- `data/dsagents_checkpoints.db` — `SqliteSaver`，LangGraph 线程检查点。

## MinerU 与模型配置
- MinerU 服务地址固定 `MINERU_BASE_URL = "http://10.11.0.110:6006"`（`backend/tools.py`）。
- 模型选择：`DeepAgentsBrainFactory` 读取环境变量 `DSAGENTS_MODEL`，缺省 `openai:gpt-5.5`（`backend/harness.py`）；是否实际可达当前代码未确认。

## 依赖声明方式
- 单一根级文件 `requirements.txt`，全部以 `>=` 下限声明，无锁定版本、无 extras、无 `pyproject.toml`（当前代码未确认）。
