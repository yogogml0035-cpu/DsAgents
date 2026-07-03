# 系统地图 (SYSTEM_MAP)

> 系统地图 · 事实来源：根 AGENTS.md 与 backend/.planning/codebase/（2026-07-03 生成，本轮刷新）

本文件是 DsAgents 仓库的**系统层**导航，综合根级与子项目事实文档，描述多个子项目如何组成一个整体。底层实现细节请直接查阅 `backend/.planning/codebase/` 下的对应文档，本文不复制。证据不足处以"当前源文档未确认"或"需确认"标注，不编造依赖。

---

## 1. 系统目的和仓库形态

DsAgents 是一个 **Harness 级 agent 运行时底座**，目标是把 `Session` / `Harness` / `Hands` / `Resources` / `Tools` 固化为五个稳定模块边界，使能力（Brain、执行器、工具）可插拔，而**不被硬编码到某个 runner、容器、模型或工作流**。DeepAgents 在这里只是可插拔的 Brain / 子 Harness（经 `BrainFactory` Protocol 注入，`self_check.py` 用 `_FakeBrainFactory` 证明可替换）；文档解析作为可插拔工具（`ToolCatalog`）由 Harness 注入；项目自身拥有 Session、事件、资源、工具路由与运行时状态。

- **仓库形态**：单子项目仓库，当前只有一个 Python 子项目 `backend/`，五大模块边界全部落在该子项目内。
- **里程碑**：交付最小可运行的 DeepAgents 解析演示——一个通用文档解析工具 + 一个 DeepAgents 工厂 + 一个 `CompositeBackend` 配置 + 一个最小 session runner + 一个薄 HTTP run/upload 适配层。刻意不引入账号体系、鉴权、复杂 service 框架或工作流引擎。
- **根级原则**（见 `AGENTS.md`）：稳定接口而非实现；Session 存 append-only 完整持久任务事实、不是上下文窗口；真实错误透传；保持 Harness 薄；每个新抽象必须保护五大边界之一否则移除。

---

## 2. 子项目职责表

| 子项目 | 路径 | 主要职责 | 关键依赖 | 可独立运行 |
|--------|------|----------|----------|------------|
| backend | `backend/` | Harness 级 agent 运行时底座：五大模块边界（Session/Harness/Hands/Resources/Tools）全部在此；DeepAgents 作为可插拔 Brain；通用文档解析工具；薄 FastAPI HTTP 层 | `deepagents>=0.6.12`、`fastapi>=0.116.1`、`langchain>=1.3.11`、`langchain-anthropic>=1.4.8`、`langchain-core>=1.4.8`、`langgraph>=1.2.7`、`langgraph-checkpoint-sqlite>=3.1.0`、`python-multipart>=0.0.20`、`python-dotenv>=1.2.2`、`requests>=2.34.2`、`uvicorn>=0.35.0`（来源 `backend/pyproject.toml`，uv 管理） | 是：`python backend/self_check.py`（自检）/ `python backend/session.py`（冒烟）/ `cd backend && uv run uvicorn api:app --host 0.0.0.0 --port 8000`（HTTP）/ `from session import run_session`（导入调用）；**没有** `python -m backend.*`（`backend/` 不是包，无 `__init__.py` / `__main__.py`） |

> **模块形态**：`backend/` **不是常规 Python 包**，没有 `__init__.py` / `__main__.py`。它在 `pyproject.toml` 中以**扁平顶层模块**（`[tool.setuptools] package-dir = {"" = "."}` + `py-modules = ["api","hands","harness","resources","session","tools","self_check"]`）声明，模块间用绝对导入（`from session import ...`），导入入口为 `from session import run_session`（**不带** `backend.` 前缀）。
>
> `scripts/ralph/`、`backend/instantclient/`、`.agents/`、`.codex/`、`.review-push/` 等目录**不属于产品知识边界**：前者是被 `.gitignore` 忽略的自动化脚本，后者是 Oracle 依赖产物 / 本地 agent 工具配置，详见 §6。

---

## 3. 跨子项目调用链与数据流

当前为**单子项目**，以下描述 `backend/` 内部从用户输入到产物落盘的完整调用链（证据：`backend/.planning/codebase/ARCHITECTURE.md` §4、`INTEGRATIONS.md` 集成调用链）：

```
用户输入
  │
  ▼
run_session(message, session_id)              [backend/session.py]
  │
  ▼
with AgentResources() 装配资源                 [backend/resources.py]
  │  · 建 backend/data/dsagents_sessions.db / dsagents_store.db / dsagents_checkpoints.db
  │    （data_dir 由 _BACKEND_DIR 锁定在 backend/，与 CWD 无关）
  │  · 装配 CompositeBackend（StateBackend + StoreBackend + FilesystemBackend）
  │
  ▼
create_harness(resources).run_turn()           [backend/harness.py]
  │
  ├─① ensure_session(session_id)              写 sessions 表
  ├─② emit_event("user_message")              写会话事件库（append-only）
  ├─③ context_window(session_id)              从事件历史派生最近 20 条 user/assistant 消息
  │
  ├─④ DeepAgentsBrainFactory.create(...)      注入 middleware + tools + CompositeBackend + checkpointer + store
  │     └─ TraceHands.middleware() → [TraceMiddleware]
  │
  ├─⑤ brain.invoke({"messages":[RemoveMessage(REMOVE_ALL_MESSAGES), *ctx]},
  │                config={"configurable":{"thread_id": session_id}})
  │     │
  │     │  执行期间 Hands 透传 trace 并写回事件：
  │     │   · wrap_model_call → model_request/model_response（出错 model_error 并 raise）
  │     │   · wrap_tool_call  → tool_request/tool_response（出错 tool_error 并 raise）
  │     │
  │     └─ 若模型决定解析文档 → Tools.parse_document              [backend/tools.py]
  │           POST ${MINERU_BASE_URL}/tasks (backend/effort 来自 MINERU_* env)
  │             → 轮询 GET /tasks/{task_id}
  │             → 取 GET /tasks/{task_id}/result
  │             → 写 backend/data/document_outputs/{stem}.md
  │
  │     └─ 大产物经 CompositeBackend 路由：/artifacts/、/large_tool_results/ → backend/data/artifacts/；
  │        /memories/、/conversation_history/、/logs/ → SqliteStore (dsagents_store.db)；
  │        线程状态检查点 → dsagents_checkpoints.db
  │
  └─⑥ emit_event("assistant_message")          写回助手事件
  ▼
return HarnessTurn(session_id, context, result)
```

**要点**：上下文窗口是从 append-only 事件历史**派生**的视图（步骤③），派生前先写入用户事件（步骤②）；执行 trace 由 Hands 的 middleware 产生（步骤⑤内 emit）；`brain.invoke` 前用 `RemoveMessage(REMOVE_ALL_MESSAGES)` 重置 langgraph 内部消息再用 Session 派生上下文重建——Session 是"单一事实源"而非 langgraph thread 状态。

---

## 4. 后端到前端的接口边界

**当前仍无前端子项目**，但本仓库的 `backend/` 已暴露最小 HTTP API，供外部 UI / client 直接调用：

- `POST /sessions/messages`：阻塞 run，返回 `{"session_id","run_id","status","reply|error"}`。
- `POST /sessions/messages/stream`：SSE 流式 run，事件 `session` → `run_event*` → `done`。
- `POST /sessions/messages/runs`：后台 run，立即返回 `{"session_id","run_id","status":"queued"}`。
- `GET /runs/{run_id}`：查询 run 基础状态与事件流，支持 `after_event_id` 增量拉取。
- `GET /sessions/{session_id}/runs`：按创建时间倒序返回该 session 的 run 列表。
- `POST /files`：上传到 `backend/data/artifacts/uploads/`，返回虚拟路径 `/artifacts/uploads/...`，供后续消息引用。
- 仍无鉴权、无 CORS middleware、无独立 `/health`、无 WebSocket、无 Redis/外部队列。
- `.env.example` 中的 `CORS_ORIGINS=http://localhost:8500,http://127.0.0.1:8500`（端口 8500 暗示 Streamlit）仍未被源码读取，属预留前端边界。

> 不编造任何额外前端 API 契约。若未来新增前端子项目，应在本节补充真实使用到的请求/响应语义，而不是规划稿。

---

## 5. 共享状态 / 存储 / 事件 / 产物 / provider 边界

证据来自 `backend/.planning/codebase/INTEGRATIONS.md`、`STRUCTURE.md`、`STACK.md`。以下均为**本地文件 / 内网**，无远程 DB、无网络存储。所有路径都锁定在 `backend/data/` 下（`resources.py::_BACKEND_DIR = Path(__file__).resolve().parent`，与运行时 CWD 无关）。

### 5.1 三条独立的 SQLite 持久化通道

| 用途 | 路径 | 谁建/写 | 来源 |
|------|------|---------|------|
| 会话/Run 事件库（append-only） | `backend/data/dsagents_sessions.db` | `SqliteSessionStore`（标准库 `sqlite3`，`backend/session.py`） | `ResourceConfig.session_db` |
| LangGraph Store（持久记忆/历史/日志） | `backend/data/dsagents_store.db` | `SqliteStore.from_conn_string` + `.setup()`（`backend/resources.py`） | `ResourceConfig.store_db` |
| LangGraph Checkpoint（线程状态检查点） | `backend/data/dsagents_checkpoints.db` | `SqliteSaver.from_conn_string` + `.setup()`（`backend/resources.py`） | `ResourceConfig.checkpoint_db` |

- 三库相互独立，均由 `AgentResources.__enter__` 创建 + `.setup()`，`__exit__` 经 `ExitStack` 关闭。
- 会话/Run 事件库为 append-only，超大 payload（> `max_inline_bytes=262144` 即 256KiB）外溢到 `backend/data/artifacts/session-events/<uuid>.json` 或 `backend/data/artifacts/run-events/<uuid>.json`，DB 仅存 `{artifact_path, bytes}` 指针。

### 5.2 文件系统产物目录

| 用途 | 路径 | 来源 |
|------|------|------|
| 数据根目录 | `backend/data/` | `ResourceConfig.data_dir = _BACKEND_DIR/"data"`，运行时创建，`.gitignore` 忽略 |
| 大产物根目录 | `backend/data/artifacts/` | `ResourceConfig.artifacts_dir`；DeepAgents `FilesystemBackend` 根 |
| 超大 session 事件外溢 JSON | `backend/data/artifacts/session-events/<uuid>.json` | `SqliteSessionStore` |
| 超大 run 事件外溢 JSON | `backend/data/artifacts/run-events/<uuid>.json` | `SqliteSessionStore` |
| 文档解析输出 | `backend/data/document_outputs/<stem>.md` | `backend/tools.py::_default_output_path`（`Path(__file__).resolve().parent/"data"/"document_outputs"`） |

### 5.3 DeepAgents `CompositeBackend` 路由（`backend/resources.py`）

模型经 DeepAgents 内置虚拟文件系统写入时按路径前缀路由（不另加包装，遵循根 `AGENTS.md`）：

- `default = StateBackend()`（图状态/内存，默认）
- `/memories/`、`/conversation_history/`、`/logs/` → `StoreBackend(store=SqliteStore, namespace=("dsagents",))`（持久，落 `backend/data/dsagents_store.db`）
- `/artifacts/`、`/large_tool_results/` → `FilesystemBackend(root_dir=backend/data/artifacts, virtual_mode=True)`（落盘）

### 5.4 Provider 边界

| Provider | 状态 | 边界位置 | 关键事实 |
|----------|------|----------|----------|
| **MinerU**（当前文档解析 provider） | 已确认 | `backend/tools.py::parse_document`（私有 `_submit_mineru_task` / `_wait_for_mineru_result`） | `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_EFFORT` / `MINERU_TIMEOUT_SECONDS` 在调用时读取；`.env.example` 当前示例地址是内网 `http://10.11.0.110:6006`；三步同步任务 API：`POST /tasks` → 轮询 `GET /tasks/{task_id}` → `GET /tasks/{task_id}/result`；源码未携带鉴权头，需确认内网是否需鉴权；明文 HTTP 无 TLS |
| **MiniMax**（默认 LLM） | 已确认 | `backend/harness.py::DeepAgentsBrainFactory` | 经 **Anthropic 兼容协议**接入：`DeepAgentsBrainFactory.__init__`（当 `model is None` 时）执行 `init_chat_model(f"anthropic:{os.getenv('MINIMAX_MODEL')}", api_key=os.getenv("MINIMAX_API_KEY"), base_url=os.getenv("MINIMAX_BASE_URL"), thinking={"type": "adaptive"})` 落到 LangChain `ChatAnthropic`。**仅**读取 `MINIMAX_MODEL`/`MINIMAX_API_KEY`/`MINIMAX_BASE_URL`，**无默认值、无 fallback**（commit `a30bb99` 切换协议、`9c78cf2` 移除 fallback；env 未设置时 `os.getenv` 返回 `None`，行为由 provider 决定）；thinking 固定启用为 `adaptive`，流式接口输出 `thinking_delta`。**不再**复制到 `OPENAI_API_KEY`/`OPENAI_API_BASE`，也**无** `ANTHROPIC_*` 回退 |
| **DeepSeek** | 仅 `.env.example`，需确认 | — | `.env.example` 有 `DEEPSEEK_API_KEY`/`DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL`，但 backend 源码**零引用**，归属需确认（疑似可切换 LLM 提供方） |
| **LangSmith** | 仅 `.env.example`，需确认 | — | `LANGSMITH_TRACING=false` 默认关闭，backend 源码无直接引用，经 LangChain/LangGraph 运行时间接生效；若误开启会上传 trace 到外部服务 |
| **Oracle** | 仅 `.env.example`，需确认 | — | `ORACLE_DSN` 等键已进 `.env.example`，`backend/instantclient/` 已入库，但 backend 源码**零引用**，`backend/pyproject.toml` 的 `[project.dependencies]` 也未列 `oracledb`/`cx_Oracle`（疑似范围蔓延前兆，见 §8） |

> 安全边界：本文件不写入任何密钥 / token / 连接串。`.env` 与 `.venv` 已被 `.gitignore` 正确忽略，未发现真实凭据被跟踪。

---

## 6. 子项目间依赖与归属规则

当前单子项目，规则简化如下：

- **依赖清单归属 `backend/`**：依赖声明在 `backend/pyproject.toml` 的 `[project.dependencies]`，版本由 `backend/uv.lock` 锁定，包管理器为 **uv**（安装：`cd backend && uv sync`）。`backend/` 是可安装项目 `dsagents`（version `0.1.0`，`requires-python = ">=3.11,<4.0"`，build-system `setuptools>=68`）。
- **打包配置**：`backend/pyproject.toml` 用 `[tool.setuptools] package-dir = {"" = "."}` + `py-modules = [...]` 声明扁平顶层模块（不是常规包，无 `__init__.py`）。
- **非产品知识目录**（不纳入系统层理解，修改时不必联动地图）：
  - `scripts/ralph/` —— 被 `.gitignore` 忽略的自动化脚本。
  - `backend/instantclient/` —— Oracle Instant Client 19.31（Windows `.dll/.exe/.jar`），属**已提交进 git 的依赖产物**，无任何 Python 代码 import 它，与当前里程碑无关。
  - `.agents/`、`.codex/`、`.review-push/` —— 本地 agent / 工具配置元数据。
- **数据/产物不入库**：`backend/data/`、`.venv/`、`.env`、`__pycache__/` 均被 `.gitignore` 忽略，首次运行自动创建。

---

## 7. 按任务分类的阅读指南

每个任务类别应**先读**下列事实文档（路径相对仓库根），再到本地图理解跨系统位置。

### 7.1 后端业务 / API / 存储 / runner 修改
- 先读：`backend/.planning/codebase/ARCHITECTURE.md`（五大边界 + 运行时数据流 + 关键设计决策）、`STRUCTURE.md`（模块清单 + 入口 + 资源目录约定）、`CONVENTIONS.md`（命名 / 类型 / 持久化约定）。
- 系统层提醒：Session 是 append-only 单一事实源，不是上下文窗口；改动持久化须保持"事件不 update/delete"；新增抽象必须保护五大边界之一。

### 7.2 文档解析工具或 DeepAgents Brain 修改
- 先读：`backend/.planning/codebase/INTEGRATIONS.md`（当前 provider 的三步 API、`MINERU_*` 配置、CompositeBackend 路由）、`STACK.md`（DeepAgents/LangChain/LangGraph 版本与用法）。
- 系统层提醒：模型侧公开工具名是 `parse_document`；当前 provider 仍是 MinerU；Brain 经 `BrainFactory` Protocol 可替换（`self_check.py` 用 `_FakeBrain` 证明）；改当前 provider 的协议字段会冲击 `_find_value` 模糊匹配。

### 7.3 跨系统接口 / 集成修改
- 先读：`backend/.planning/codebase/INTEGRATIONS.md`（全部 provider 边界 + 集成调用链）、`STACK.md`（外部服务清单）。
- 系统层提醒：当前唯一已接入的外部文档解析网络依赖是 provider 端点；MiniMax 经 Anthropic 兼容协议；DeepSeek / Oracle / LangSmith / CORS 均为 `.env.example` 预留、源码未引用，改它们前先确认归属（见 §5.4 / §8）。

### 7.4 新增子项目（如未来加 frontend）时应注意的边界
- 先读：根 `AGENTS.md`（简洁约束、Harness 边界）、本地图 §4（当前无前端）、§6（依赖归属）。
- 系统层提醒：
  - backend 当前同时提供 **Python API + 薄 FastAPI HTTP API**；若新增前端，优先复用现有 HTTP 层并保持它薄，不新增第二套 service 框架。
  - `.env.example` 的 `CORS_ORIGINS=http://localhost:8500` 已预留前端端口（疑似 Streamlit），属未实现边界。
  - 事件 / 产物 / 三 SQLite 库归属 backend，新子项目应通过明确接口访问而非直连 DB。
  - 新增子项目后须更新本地图 §2 子项目职责表、§3 调用链、§4 接口边界。

---

## 8. 集成风险检查清单

提炼自 `backend/.planning/codebase/CONCERNS.md`，每条附"验证入口"（如何在代码中复核）。

| # | 风险 | 验证入口 |
|---|------|----------|
| 1 | **当前文档解析 provider 仍依赖 MinerU 内网 HTTP**；若运行环境继续使用 `.env.example` 示例地址，则部署到其它网络即不可用；明文 HTTP 无 TLS；服务不可用时硬失败、无重试/降级。 | `backend/tools.py::_submit_mineru_task` / `_wait_for_mineru_result`、`backend/.env.example` |
| 2 | **`MINERU_*` 仅在工具调用时校验**：缺失会在 `parse_document(...)` 路径抛 `RuntimeError`，非法 `MINERU_TIMEOUT_SECONDS` 直接抛原生 `ValueError`；普通聊天和 harness 创建不会预检。 | `backend/tools.py::_required_env`、`int(_required_env("MINERU_TIMEOUT_SECONDS"))` |
| 3 | **范围蔓延前兆：Oracle 预埋与当前里程碑无关**。`.env.example` 已含 5 个 `ORACLE_*` 键、`backend/instantclient/` 二进制已提交进 git，但 backend 源码零 Oracle 引用、`backend/pyproject.toml` 未列 `oracledb`/`cx_Oracle`。配置先于实现进入仓库。 | `backend/.env.example`、`git ls-files backend/instantclient/`、grep `oracle/cx_Oracle/oracledb` 在 `backend/*.py`（零命中） |
| 4 | **后台 run 只做进程内单飞，不做跨进程恢复**：同一 `session_id` 运行锁只保存在 FastAPI app state；进程重启后 queued/running run 统一在 startup 追加 failed("执行已中断，请重试")。这是当前里程碑刻意接受的简化。 | `backend/api.py::_acquire_session_run/_release_session_run`、`backend/api.py::lifespan`、`backend/session.py::fail_incomplete_runs` |
| 5 | **错误事件可能携带敏感信息**：`hands.py` 把 `repr(exc)` 写入 `model_error`/`tool_error` 事件并持久化到 SQLite，`repr` 可能含 URL/请求头片段，当前无脱敏。 | `backend/hands.py:41,64`（`emit_event(..., repr(exc))`） |

> 其它已确认的低风险项（无 TODO/FIXME 残留、`.env`/`.venv` 正确忽略、纯同步一致性 OK、append-only 事件可恢复但非完整回放、LangSmith 默认关闭）详见 `backend/.planning/codebase/CONCERNS.md`。

---

## 9. 使用过的源文档索引

本地图引用以下事实来源（路径相对仓库根）：

| 文件 | 角色 |
|------|------|
| `AGENTS.md` | 根级 harness 原则（五大边界、运行时规则、简洁约束） |
| `backend/.planning/codebase/ARCHITECTURE.md` | backend 五大模块边界、运行时数据流、关键设计决策、里程碑实现状态 |
| `backend/.planning/codebase/STRUCTURE.md` | backend 目录树、模块清单、入口、资源目录约定、与仓库根关系 |
| `backend/.planning/codebase/STACK.md` | 技术栈清单与版本、DeepAgents/LangChain/LangGraph 用法 |
| `backend/.planning/codebase/INTEGRATIONS.md` | 文档解析 provider / DeepAgents / SQLite / MiniMax 等集成边界与集成调用链 |
| `backend/.planning/codebase/CONVENTIONS.md` | 命名/模块组织/类型/错误处理/日志/配置/持久化约定 |
| `backend/.planning/codebase/TESTING.md` | self_check 角色、运行命令、验证入口、覆盖缺口 |
| `backend/.planning/codebase/CONCERNS.md` | 外部依赖/安全/稳定性/可观测性/范围蔓延/跨平台风险 |

> 本地图保持系统层，不复制底层实现细节；如需细节请直接阅读上表对应文档。
