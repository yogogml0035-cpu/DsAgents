# STRUCTURE

> 事实来源：backend/ 源码、backend/pyproject.toml + uv.lock、根 AGENTS.md（2026-07-02 生成；原仓库根 requirements.txt 已废弃）

## 1. backend/ 目录树

仅列源码与运行时相关条目，排除 `.venv/`（依赖产物）与 `__pycache__/`（编译缓存）。

```
backend/
├── __init__.py          # 模块入口：加载 .env，re-export 顶层 API
├── session.py           # Session 边界：SQLite 事件存储 + 上下文窗口派生 + run_session
├── harness.py           # Harness 边界：Brain 工厂 + 单轮运行时 HarnessRuntime
├── hands.py             # Hands 边界：TraceMiddleware 暴露执行 trace 并透传错误
├── resources.py         # Resources 边界：SQLite store/checkpointer + CompositeBackend 装配
├── tools.py             # Tools 边界：MinerU 解析工具 + ToolCatalog 注册
├── self_check.py        # 端到端自检（FakeBrain），验证五大边界与约束
├── .env                 # 运行时密钥/配置（运行时产物，已被 .gitignore 忽略）
├── .env.example         # 配置键模板（DEEPSEEK/MINIMAX/MINERU/ORACLE/LANGSMITH/CORS）
├── instantclient/       # Oracle Instant Client 19.31（Oracle 依赖产物，非 Python 源码）
├── .planning/           # 规划/文档输出目录（本文档所在）
│   └── codebase/
│       ├── ARCHITECTURE.md
│       └── STRUCTURE.md
├── .venv/               # Python 虚拟环境（依赖产物，已被 .gitignore 忽略）
└── __pycache__/         # 编译缓存（自动产物，忽略）
```

> 注：`backend/data/` 在源码中由 `ResourceConfig(data_dir=Path("data"))` 在运行时创建，当前仓库中**不存在**（首次运行时由 `AgentResources.__enter__` 创建），故不在树中列出。

## 2. 模块清单

| 文件 | 行数 | 主要导出（类/函数） | 一句话职责 |
|------|------|----------------------|------------|
| `backend/__init__.py` | 11 | `create_mineru_agent`、`create_mineru_harness`、`parse_document_with_mineru`、`run_session`（re-export） | 加载 `.env` 并暴露顶层装配 API |
| `backend/session.py` | 253 | `SessionStore`、`SqliteSessionStore`、`SessionRecord`、`SessionEvent`、`ContextWindow`、`run_session`、`main` | append-only 事件存储 + 上下文窗口派生 + 最小 runner |
| `backend/harness.py` | 146 | `Brain`、`BrainFactory`、`DeepAgentsBrainFactory`、`HarnessRuntime`、`HarnessTurn`、`create_mineru_harness`、`create_mineru_agent` | 读历史→派生上下文→请求执行→写回事件 |
| `backend/hands.py` | 92 | `Hands`、`TraceHands`、`TraceMiddleware` | 用 middleware 暴露 model/tool trace 并透传真实错误 |
| `backend/resources.py` | 67 | `ResourceConfig`、`AgentResources` | 持有 SQLite store/checkpointer + CompositeBackend 路由 |
| `backend/tools.py` | 134 | `ToolCatalog`、`ToolHandler`、`parse_document_with_mineru`、`default_tool_catalog` | MinerU 解析工具与工具注册 |
| `backend/self_check.py` | 127 | `main`（+ 内部 `_FakeBrain`/`_FakeBrainFactory`） | 端到端自检五大边界与约束 |

## 3. 入口与运行方式

- **导入入口**：`import backend` → `backend.__init__` 先 `load_dotenv(backend/.env)`，再 re-export 四个顶层 API。调用方典型用法：
  ```python
  from backend import run_session
  result = run_session("帮我解析 xxx.pdf", session_id="可选")
  ```
  调用链：`run_session` → `with AgentResources(ResourceConfig())` → `create_mineru_harness(resources)` → `HarnessRuntime.run_turn(message, session_id)` → 返回 `result`（含 `messages`）。

- **自检入口**：`python -m backend.self_check` → `self_check.main()`，用 FakeBrain 端到端跑 Harness、验证 trace 事件与错误透传，打印 `self-check passed`。

- **`python -m backend`（需确认）**：仓库**无** `backend/__main__.py`，直接 `python -m backend` 当前不可用。`session.py::main()` 引用了未定义的 `args`，判断为未完工/计划补全，需确认是否计划补 `__main__.py` 以支持 `python -m backend --message ... --session-id ...`。

## 4. 资源目录约定（从代码确认）

所有资源路径由 `resources.py::ResourceConfig` 集中定义，根目录默认为 `data/`（相对当前工作目录解析）：

| 用途 | 实际路径 | 来源 |
|------|----------|------|
| 数据根目录 | `data/` | `ResourceConfig.data_dir`，`AgentResources.__enter__` 创建 |
| Session 事件库 | `data/dsagents_sessions.db` | `ResourceConfig.session_db` |
| Store 库（持久历史/记忆） | `data/dsagents_store.db` | `ResourceConfig.store_db` → `SqliteStore` |
| Checkpointer 库 | `data/dsagents_checkpoints.db` | `ResourceConfig.checkpoint_db` → `SqliteSaver` |
| 大型产物根目录 | `data/artifacts/` | `ResourceConfig.artifacts_dir`，`AgentResources` 创建 |
| 超大事件外溢文件 | `data/artifacts/session-events/<uuid>.json` | `SqliteSessionStore(artifacts_dir)`，payload > 256KB 时外溢 |
| MinerU 输出 | `data/mineru_outputs/<stem>.md` | `tools.py::_default_output_path` |

**虚拟文件系统（DeepAgents CompositeBackend）**：`AgentResources` 构建的 `CompositeBackend` 路由如下（`FilesystemBackend` 根指向 `data/artifacts/`，`virtual_mode=True`）：
- `default = StateBackend()`（默认，进程内存态）
- `/memories/`、`/conversation_history/`、`/logs/` → `StoreBackend`（SQLite 持久，namespace 固定 `("dsagents",)`）
- `/artifacts/`、`/large_tool_results/` → `FilesystemBackend`（落 `data/artifacts/` 磁盘）

> 即 AGENTS.md 所述"使用 DeepAgents 内置虚拟文件系统，不另加包装"的直接体现：Brain 写 `/memories/xxx` 会落到 SQLite store，写 `/artifacts/xxx` 会落到磁盘。

## 5. 与仓库根目录的关系

```
DsAgents/                      # 仓库根
├── AGENTS.md                  # 根级 harness 原则（五大边界、运行时规则、简洁约束）
├── backend/                   # 本项目 Python 包（五大边界全部在此，含 pyproject.toml + uv.lock）
│   ├── pyproject.toml         # 可安装包 dsagents 的打包/依赖配置（[project.dependencies]）
│   └── uv.lock                # uv 锁文件（依赖版本锁定）
├── scripts/ralph/             # 自动化脚本（被 .gitignore 忽略，本文档不展开）
├── .agents/ .codex/ .review-push/  # agent/工具配置目录（运行时元数据，忽略）
└── .gitignore                 # 忽略 .env、data/、.venv/、__pycache__/、scripts/ralph/ 等
（仓库根的 requirements.txt 已废弃删除，依赖改由 backend/pyproject.toml + uv 管理）
```

- **依赖（运行时）**：`backend/pyproject.toml` 的 `[project.dependencies]` 声明 `deepagents>=0.6.12`、`langchain>=1.3.11`、`langchain-core>=1.4.8`、`langchain-openai>=0.3.0`、`langgraph>=1.2.7`、`langgraph-checkpoint-sqlite>=3.1.0`、`python-dotenv>=1.2.2`、`requests>=2.34.2`，版本由 `backend/uv.lock` 锁定，包管理器为 **uv**（安装：`cd backend && uv sync`）。`backend/` 是**可安装包** `dsagents`（version `0.1.0`，`requires-python = ">=3.11,<4.0"`，build-system `setuptools>=68`）。仓库根的 `requirements.txt` 已废弃删除，不再使用。
- **`backend/instantclient/`**：Oracle Instant Client 19.31（依赖产物，供可能的 Oracle 连接用，`.env.example` 含 `ORACLE_*` 键），但当前五大模块源码**未引用** Oracle，判断为预留/依赖产物，非运行时必经路径。
- **`backend/.venv/`**：虚拟环境（依赖产物），`.gitignore` 忽略 `.venv/`。
- **`backend/.env`**：运行时密钥与配置（`.gitignore` 忽略 `.env`），`__init__.py` 在导入时加载；`.env.example` 是模板（保留在仓库）。
- **`data/`**：运行时产物（SQLite 库 + artifacts），`.gitignore` 忽略 `data/`，首次运行自动创建，不入库。
