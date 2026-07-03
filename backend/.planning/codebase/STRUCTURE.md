# STRUCTURE

> 事实来源：backend/ 源码、backend/pyproject.toml + uv.lock（2026-07-03 生成；本轮刷新：新增 FastAPI lifespan + run API + `run-events/` 产物目录）

## 1. backend/ 目录树

仅列源码与运行时相关条目，排除 `.venv/`（依赖产物）与 `__pycache__/`（编译缓存）。

```
backend/
├── api.py               # 薄 HTTP 适配层：FastAPI lifespan、run API、文件上传
├── session.py           # Session 边界：SQLite session 事件 + runs/run_events + 上下文窗口派生 + run_session + main
├── harness.py           # Harness 边界：Brain 工厂 + 单轮运行时 + HTTP run streaming 执行
├── hands.py             # Hands 边界：TraceMiddleware 暴露执行 trace，并可同步写 run 事件
├── resources.py         # Resources 边界：SQLite store/checkpointer + CompositeBackend 装配
├── tools.py             # Tools 边界：通用文档解析工具 + ToolCatalog 注册
├── self_check.py        # 端到端自检（FakeBrain），验证五大边界与约束
├── pyproject.toml       # 可安装项目 dsagents：打包/依赖/扁平顶层模块声明
├── uv.lock              # uv 锁文件（依赖版本锁定）
├── .env                 # 运行时密钥/配置（运行时产物，已被 .gitignore 忽略）
├── .env.example         # 配置键模板（DEEPSEEK/MINIMAX/MINERU/ORACLE/LANGSMITH/CORS）
├── instantclient/       # Oracle Instant Client 19.31（Oracle 二进制依赖产物，非 Python 源码）
└── .planning/           # 规划/文档输出目录（本文档所在）
    └── codebase/
```

> 注：**没有 `backend/__init__.py`，也没有 `backend/__main__.py`**——`backend/` 不是常规 Python 包。它在 `pyproject.toml` 中以扁平顶层模块（`[tool.setuptools] package-dir = {"" = "."}` + `py-modules = [...]`）声明，模块间绝对导入。`backend/data/` 在仓库中**不存在**，由 `AgentResources.__enter__` 与 `SqliteSessionStore.__init__` 在首次运行时创建。

## 2. 模块清单

| 文件 | 主要导出（类/函数） | 一句话职责 |
|------|----------------------|------------|
| `session.py` | `SessionStore`、`SqliteSessionStore`、`SessionRecord`、`SessionEvent`、`RunRecord`、`RunEvent`、`RunSnapshot`、`ContextWindow`、`run_session`、`main` | append-only session 事件 + immutable runs + append-only run_events + 上下文窗口派生 |
| `api.py` | `MessageRequest`、`INTERRUPTED_RUN_ERROR`、`create_app`、`app` | 薄 HTTP 适配层：用 lifespan 持有共享 resources/harness，暴露阻塞 run、SSE run、后台 run、run 查询和文件上传 |
| `harness.py` | `Brain`、`BrainFactory`、`DeepAgentsBrainFactory`、`HarnessRuntime`、`HarnessTurn`、`create_harness` | 读历史→派生上下文→请求执行→写回事件；HTTP run 统一走 streaming 执行路径 |
| `hands.py` | `Hands`、`TraceHands`、`TraceMiddleware` | 用 middleware 暴露 model/tool trace 并透传真实错误；可按 run_id 同步追加 run 事件 |
| `resources.py` | `ResourceConfig`、`AgentResources` | 持有 SQLite store/checkpointer + CompositeBackend 路由；`data_dir` 锁定在 `backend/data/` |
| `tools.py` | `ToolCatalog`、`ToolHandler`、`parse_document`、`default_tool_catalog` | 通用文档解析工具与工具注册 |
| `self_check.py` | `main`（+ 内部 `_FakeBrain`/`_FakeBrainFactory`） | 端到端自检五大边界与约束 |

## 3. 入口与运行方式

`backend/` 不是包，没有 `__init__.py` / `__main__.py`，因此**不能**用 `from backend import ...` 或 `python -m backend.<x>`。模块之间用绝对导入（`from session import ...`），脚本所在目录会自动加入 `sys.path`，所以直接运行 `backend/` 内脚本即可让这些绝对导入正确解析。

- **HTTP 服务入口**：
  ```bash
  cd backend && uv run uvicorn api:app --host 0.0.0.0 --port 8000
  ```
  `api.py` 暴露 `app` / `create_app()`，当前提供六个端点：`POST /sessions/messages`（阻塞 run）、`POST /sessions/messages/stream`（SSE run 流）、`POST /sessions/messages/runs`（后台 run）、`GET /runs/{run_id}`（run + 事件查询）、`GET /sessions/{session_id}/runs`（session run 列表）、`POST /files`（multipart 上传到 `backend/data/artifacts/uploads/`，响应虚拟路径 `/artifacts/uploads/<uuid>_<filename>`）。

- **导入入口**：需在 `backend/` 目录下（或把 `backend/` 加入 `PYTHONPATH`）：
  ```python
  from session import run_session
  result = run_session("帮我解析 xxx.pdf", session_id="可选")
  ```
  调用链：`run_session` → `with AgentResources(ResourceConfig())` → `create_harness(resources)` → `HarnessRuntime.run_turn(message, session_id)` → 返回 `result`（含 `messages`）。

- **自检入口**（推荐，不需要真实 LLM / 当前文档解析 provider 可达）：
  ```bash
  python backend/self_check.py        # 或 cd backend && uv run python self_check.py
  ```
  `self_check.main()` 用 FakeBrain + FastAPI `TestClient` 端到端跑 Harness / HTTP / SSE / upload / `/artifacts/...` 映射，结尾打印 `self-check passed`。

- **冒烟入口**（需真实 `MINIMAX_API_KEY` / `MINIMAX_MODEL` / `MINIMAX_BASE_URL` 与网络）：
  ```bash
  python backend/session.py           # 或 cd backend && python -m session
  ```
  `session.main()` 硬编码 `message = "你好"` + 随机 `session_id`，调用 `run_session` 后打印最后一条消息内容。

> 没有 `python -m backend`（无 `__main__.py`）；也没有 `python -m backend.self_check` / `python -m backend.session`（无 `backend` 包）。命令请用上述扁平形式。

## 4. 资源目录约定（从代码确认）

所有资源路径由 `resources.py::ResourceConfig` 集中定义。`data_dir` 默认为 `_BACKEND_DIR / "data"`（`resources.py:14,19`），其中 `_BACKEND_DIR = Path(__file__).resolve().parent` 固定指向 `backend/`，因此**所有数据/产物都落在 `backend/data/` 下，与运行时 CWD 无关**：

| 用途 | 实际路径 | 来源 |
|------|----------|------|
| 数据根目录 | `backend/data/` | `ResourceConfig.data_dir`（`_BACKEND_DIR/"data"`），`AgentResources.__enter__` 创建 |
| Session/Run 事件库 | `backend/data/dsagents_sessions.db` | `ResourceConfig.session_db`（包含 `sessions` / `session_events` / `runs` / `run_events` 四张表） |
| Store 库（持久历史/记忆） | `backend/data/dsagents_store.db` | `ResourceConfig.store_db` → `SqliteStore` |
| Checkpointer 库 | `backend/data/dsagents_checkpoints.db` | `ResourceConfig.checkpoint_db` → `SqliteSaver` |
| 大型产物根目录 | `backend/data/artifacts/` | `ResourceConfig.artifacts_dir`，`AgentResources` 创建 |
| HTTP 上传落点 | `backend/data/artifacts/uploads/<uuid>_<filename>` | `api.py::post_file` |
| 超大 session 事件外溢文件 | `backend/data/artifacts/session-events/<uuid>.json` | `SqliteSessionStore(artifacts_dir)`，payload > 256KiB 时外溢 |
| 超大 run 事件外溢文件 | `backend/data/artifacts/run-events/<uuid>.json` | `SqliteSessionStore(artifacts_dir)`，payload/raw > 256KiB 时外溢 |
| 文档解析输出 | `backend/data/document_outputs/<stem>.md` | `tools.py::_default_output_path`（`Path(__file__).resolve().parent/"data"/"document_outputs"`） |

**虚拟文件系统（DeepAgents CompositeBackend）**：`AgentResources` 构建的 `CompositeBackend` 路由如下（`FilesystemBackend` 根指向 `backend/data/artifacts/`，`virtual_mode=True`）：
- `default = StateBackend()`（默认，进程内存态）
- `/memories/`、`/conversation_history/`、`/logs/` → `StoreBackend`（SQLite 持久，namespace 固定 `("dsagents",)`）
- `/artifacts/`、`/large_tool_results/` → `FilesystemBackend`（落 `backend/data/artifacts/` 磁盘）

> 即根 AGENTS.md 所述"使用 DeepAgents 内置虚拟文件系统，不另加包装"的直接体现：Brain 写 `/memories/xxx` 会落到 SQLite store，写 `/artifacts/xxx` 会落到磁盘。

## 5. 与仓库根目录的关系

```
DsAgents/                      # 仓库根
├── AGENTS.md                  # 根级 harness 原则（五大边界、运行时规则、简洁约束）
├── ARCHITECTURE.md            # 根级系统架构总览
├── INTERFACES.md              # 根级接口/集成边界
├── coding_maps/SYSTEM_MAP.md  # 跨项目系统地图
├── backend/                   # 本项目（五大边界全部在此，含 pyproject.toml + uv.lock）
│   ├── pyproject.toml         # 可安装项目 dsagents（扁平顶层模块，[project.dependencies]）
│   └── uv.lock                # uv 锁文件（依赖版本锁定）
├── scripts/ralph/             # 自动化脚本（被 .gitignore 忽略，本文档不展开）
├── .agents/ .codex/ .review-push/  # agent/工具配置目录（运行时元数据，忽略）
└── .gitignore                 # 忽略 .env、data/、.venv/、__pycache__/、scripts/ralph/ 等
```

- **依赖（运行时）**：`backend/pyproject.toml` 的 `[project.dependencies]` 声明 `deepagents>=0.6.12`、`fastapi>=0.116.1`、`langchain>=1.3.11`、`langchain-anthropic>=1.4.8`、`langchain-core>=1.4.8`、`langgraph>=1.2.7`、`langgraph-checkpoint-sqlite>=3.1.0`、`python-multipart>=0.0.20`、`python-dotenv>=1.2.2`、`requests>=2.34.2`、`uvicorn>=0.35.0`，版本由 `backend/uv.lock` 锁定，包管理器为 **uv**（安装：`cd backend && uv sync`）。`backend/` 是可安装项目 `dsagents`（version `0.1.0`，`requires-python = ">=3.11,<4.0"`，build-system `setuptools>=68`）。
- **`backend/instantclient/`**：Oracle Instant Client 19.31（依赖产物，供可能的 Oracle 连接用，`.env.example` 含 `ORACLE_*` 键），但当前五大模块源码**未引用** Oracle，判断为预留/依赖产物，非运行时必经路径。
- **`backend/.venv/`**：虚拟环境（依赖产物），`.gitignore` 忽略 `.venv/`。
- **`backend/.env`**：运行时密钥与配置（`.gitignore` 忽略 `.env`），由 `session.py` 在导入时 `load_dotenv` 加载；`.env.example` 是模板（保留在仓库）。
- **`backend/data/`**：运行时产物（SQLite 库 + artifacts + `document_outputs/`），`.gitignore` 忽略 `data/`，首次运行自动创建，不入库。
