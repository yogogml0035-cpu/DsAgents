# 系统地图 (SYSTEM_MAP)

> 系统地图 · 事实来源：根 AGENTS.md 与 backend/.planning/codebase/（2026-07-02 生成）

本文件是 DsAgents 仓库的**系统层**导航，综合根级与子项目事实文档，描述多个子项目如何组成一个整体。底层实现细节请直接查阅 `backend/.planning/codebase/` 下的对应文档，本文不复制。证据不足处以"当前源文档未确认"或"需确认"标注，不编造依赖。

---

## 1. 系统目的和仓库形态

DsAgents 是一个 **Harness 级 agent 运行时底座**，目标是把 `Session` / `Harness` / `Hands` / `Resources` / `Tools` 固化为五个稳定模块边界，使能力（Brain、执行器、工具）可插拔，而**不被硬编码到某个 runner、容器、模型或工作流**。DeepAgents 在这里只是可插拔的 Brain / 子 Harness，本地确定性分析器是可插拔执行器；项目自身拥有 Session、事件、资源、工具路由与运行时状态。

- **仓库形态**：单子项目仓库，当前只有一个 Python 子项目 `backend/`，五大模块边界全部落在该包内。
- **里程碑**：交付最小可运行的 DeepAgents 解析演示——一个 MinerU 解析工具 + 一个 DeepAgents 工厂 + 一个 `CompositeBackend` 配置 + 一个最小 session runner。刻意不引入服务层、容器、鉴权、策略框架或工作流引擎。
- **根级原则**（见 `AGENTS.md`）：稳定接口而非实现；Session 存 append-only 完整持久任务事实、不是上下文窗口；真实错误透传；保持 Harness 薄；每个新抽象必须保护五大边界之一否则移除。

---

## 2. 子项目职责表

| 子项目 | 路径 | 主要职责 | 关键依赖 | 可独立运行 |
|--------|------|----------|----------|------------|
| backend | `backend/` | Harness 级 agent 运行时底座：五大模块边界（Session/Harness/Hands/Resources/Tools）全部在此；DeepAgents 作为可插拔 Brain；MinerU 解析工具 | `deepagents>=0.6.12`、`langchain>=1.3.11`、`langchain-core>=1.4.8`、`langchain-openai>=0.3.0`、`langgraph>=1.2.7`、`langgraph-checkpoint-sqlite>=3.1.0`、`python-dotenv>=1.2.2`、`requests>=2.34.2`（来源 `backend/pyproject.toml`，uv 管理） | 是：`python -m backend.self_check`（自检）/ `run_session(...)`（导入调用）；`python -m backend` 当前不可用（无 `backend/__main__.py`，需确认） |

> `scripts/ralph/`、`backend/instantclient/`、`.agents/`、`.codex/`、`.review-push/` 等目录**不属于产品知识边界**：前者是被 `.gitignore` 忽略的自动化脚本，后者是 Oracle 依赖产物 / 本地 agent 工具配置，详见 §6。

---

## 3. 跨子项目调用链与数据流

当前为**单子项目**，以下描述 `backend/` 内部从用户输入到产物落盘的完整调用链（证据：`backend/.planning/codebase/ARCHITECTURE.md` §3、`INTEGRATIONS.md` 集成调用链）：

```
用户输入
  │
  ▼
run_session(message, session_id)              [backend/session.py]
  │
  ▼
with AgentResources() 装配资源                 [backend/resources.py]
  │  · 建 data/dsagents_sessions.db / dsagents_store.db / dsagents_checkpoints.db
  │  · 装配 CompositeBackend（StateBackend + StoreBackend + FilesystemBackend）
  │
  ▼
create_mineru_harness(resources).run_turn()    [backend/harness.py]
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
  │     └─ 若模型决定解析文档 → Tools.parse_document_with_mineru  [backend/tools.py]
  │           POST http://10.11.0.110:6006/tasks (固定 backend=hybrid-engine, effort=high)
  │             → 轮询 GET /tasks/{task_id}
  │             → 取 GET /tasks/{task_id}/result
  │             → 写 data/mineru_outputs/{stem}.md
  │
  │     └─ 大产物经 CompositeBackend 路由：/artifacts/、/large_tool_results/ → data/artifacts/；
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

**当前无前端子项目。** 本仓库仅含 `backend/`，backend 暴露的是 **Python API**（`from backend import run_session`、`create_mineru_agent`、`create_mineru_harness`、`parse_document_with_mineru`），**而非 HTTP 服务**：

- 无 FastAPI / uvicorn / Flask 等 web 框架依赖（`backend/pyproject.toml` 的 `[project.dependencies]` 未声明，源码无引用）。
- 无 HTTP server、无健康检查端点、无 SSE/WebSocket 通道。
- `.env.example` 中的 `CORS_ORIGINS=http://localhost:8500,8500`（端口 8500 暗示 Streamlit）属**预留 / 前端边界**，backend 自身源码未引用，需确认服务层归属。

> 不编造任何前端 API 契约。若未来新增前端子项目，应在本节补"后端→前端接口边界"小节（详见 §7 阅读指南）。

---

## 5. 共享状态 / 存储 / 事件 / 产物 / provider 边界

证据来自 `backend/.planning/codebase/INTEGRATIONS.md`、`STRUCTURE.md`、`STACK.md`。以下均为**本地文件 / 内网**，无远程 DB、无网络存储。

### 5.1 三条独立的 SQLite 持久化通道

| 用途 | 路径 | 谁建/写 | 来源 |
|------|------|---------|------|
| 会话事件库（append-only） | `data/dsagents_sessions.db` | `SqliteSessionStore`（标准库 `sqlite3`，`backend/session.py`） | `ResourceConfig.session_db` |
| LangGraph Store（持久记忆/历史/日志） | `data/dsagents_store.db` | `SqliteStore.from_conn_string` + `.setup()`（`backend/resources.py`） | `ResourceConfig.store_db` |
| LangGraph Checkpoint（线程状态检查点） | `data/dsagents_checkpoints.db` | `SqliteSaver.from_conn_string` + `.setup()`（`backend/resources.py`） | `ResourceConfig.checkpoint_db` |

- 三库相互独立，均由 `AgentResources.__enter__` 创建 + `.setup()`，`__exit__` 经 `ExitStack` 关闭。
- 会话事件库为 append-only，超大 payload（> `max_inline_bytes=262144` 即 256KiB）外溢到 `data/artifacts/session-events/<uuid>.json`，DB 仅存 `{artifact_path, bytes}` 指针。

### 5.2 文件系统产物目录

| 用途 | 路径 | 来源 |
|------|------|------|
| 数据根目录 | `data/` | `ResourceConfig.data_dir`，运行时创建，`.gitignore` 忽略 |
| 大产物根目录 | `data/artifacts/` | `ResourceConfig.artifacts_dir`；DeepAgents `FilesystemBackend` 根 |
| 超大事件外溢 JSON | `data/artifacts/session-events/<uuid>.json` | `SqliteSessionStore` |
| MinerU 解析输出 | `data/mineru_outputs/<stem>.md` | `backend/tools.py::_default_output_path` |

### 5.3 DeepAgents `CompositeBackend` 路由（`backend/resources.py`）

模型经 DeepAgents 内置虚拟文件系统写入时按路径前缀路由（不另加包装，遵循 `AGENTS.md`）：

- `default = StateBackend()`（图状态/内存，默认）
- `/memories/`、`/conversation_history/`、`/logs/` → `StoreBackend(store=SqliteStore, namespace=("dsagents",))`（持久，落 `data/dsagents_store.db`）
- `/artifacts/`、`/large_tool_results/` → `FilesystemBackend(root_dir=data/artifacts, virtual_mode=True)`（落盘）

### 5.4 Provider 边界

| Provider | 状态 | 边界位置 | 关键事实 |
|----------|------|----------|----------|
| **MinerU**（文档解析） | 已确认 | `backend/tools.py::parse_document_with_mineru` | 内网 `http://10.11.0.110:6006`，**源码硬编码**；三步同步任务 API：`POST /tasks` → 轮询 `GET /tasks/{task_id}` → `GET /tasks/{task_id}/result`；`backend=hybrid-engine`、`effort=high` **固定且不可配置**（里程碑约束）；源码未携带鉴权头，需确认内网是否需鉴权；明文 HTTP 无 TLS |
| **MiniMax**（默认 LLM） | 已确认 | `backend/harness.py::DeepAgentsBrainFactory` | 默认模型 `openai:MiniMax-M3`，base url `https://api.minimaxi.com/v1`，OpenAI 兼容；`MINIMAX_API_KEY` 存在时回填到 `OPENAI_API_KEY`/`OPENAI_API_BASE`（`setdefault` 不覆盖已显式设置值） |
| **DeepSeek** | 仅 `.env.example`，需确认 | — | `.env.example` 有 `DEEPSEEK_API_KEY`/`DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL`，但 backend 源码**零引用**，归属需确认（疑似可切换 LLM 提供方） |
| **LangSmith** | 仅 `.env.example`，需确认 | — | `LANGSMITH_TRACING=false` 默认关闭，backend 源码无直接引用，经 LangChain/LangGraph 运行时间接生效；若误开启会上传 trace 到外部服务 |
| **Oracle** | 仅 `.env.example`，需确认 | — | `ORACLE_DSN` 等键已进 `.env.example`，`backend/instantclient/` 已入库，但 backend 源码**零引用**，`backend/pyproject.toml` 的 `[project.dependencies]` 也未列 `oracledb`/`cx_Oracle`（疑似范围蔓延前兆，见 §8） |

> 安全边界：本文件不写入任何密钥 / token / 连接串。`.env` 与 `.venv` 已被 `.gitignore` 正确忽略，未发现真实凭据被跟踪。

---

## 6. 子项目间依赖与归属规则

当前单子项目，规则简化如下：

- **依赖清单归属 `backend/`**：依赖声明在 `backend/pyproject.toml` 的 `[project.dependencies]`，版本由 `backend/uv.lock` 锁定，包管理器为 **uv**（安装：`cd backend && uv sync`）。`backend/` 是**可安装包** `dsagents`（version `0.1.0`，`requires-python = ">=3.11,<4.0"`，build-system `setuptools>=68`）。仓库根的 `requirements.txt` **已废弃删除**，不再使用。
- **打包配置**：`backend/pyproject.toml` 即打包/依赖配置（build-system `setuptools>=68`）。
- **非产品知识目录**（不纳入系统层理解，修改时不必联动地图）：
  - `scripts/ralph/` —— 被 `.gitignore` 忽略的自动化脚本。
  - `backend/instantclient/` —— Oracle Instant Client 19.31（Windows `.dll/.exe/.jar`），属**已提交进 git 的依赖产物**，无任何 Python 代码 import 它，与 MinerU 里程碑无关。
  - `.agents/`、`.codex/`、`.review-push/` —— 本地 agent / 工具配置元数据。
- **数据/产物不入库**：`data/`、`.venv/`、`.env`、`__pycache__/` 均被 `.gitignore` 忽略，首次运行自动创建。

---

## 7. 按任务分类的阅读指南

每个任务类别应**先读**下列事实文档（路径相对仓库根），再到本地图理解跨系统位置。

### 7.1 后端业务 / API / 存储 / runner 修改
- 先读：`backend/.planning/codebase/ARCHITECTURE.md`（五大边界 + 运行时数据流 + 关键设计决策）、`STRUCTURE.md`（模块清单 + 入口 + 资源目录约定）、`CONVENTIONS.md`（命名 / 类型 / 持久化约定）。
- 系统层提醒：Session 是 append-only 单一事实源，不是上下文窗口；改动持久化须保持"事件不 update/delete"；新增抽象必须保护五大边界之一。

### 7.2 MinerU 工具或 DeepAgents Brain 修改
- 先读：`backend/.planning/codebase/INTEGRATIONS.md`（MinerU 三步 API、固定参数、CompositeBackend 路由）、`STACK.md`（DeepAgents/LangChain/LangGraph 版本与用法）。
- 系统层提醒：MinerU `backend=hybrid-engine`/`effort=high` 固定不可配置；Brain 经 `BrainFactory` Protocol 可替换（`self_check.py` 用 `_FakeBrain` 证明）；改 MinerU 协议字段会冲击 `_find_value` 模糊匹配。

### 7.3 跨系统接口 / 集成修改
- 先读：`backend/.planning/codebase/INTEGRATIONS.md`（全部 provider 边界 + 集成调用链）、`STACK.md`（外部服务清单）。
- 系统层提醒：当前唯一外部网络依赖是 MinerU 内网端点；MiniMax 经 OpenAI 兼容协议；DeepSeek / Oracle / LangSmith / CORS 均为 `.env.example` 预留、源码未引用，改它们前先确认归属（见 §5.4 / §8）。

### 7.4 新增子项目（如未来加 frontend）时应注意的边界
- 先读：根 `AGENTS.md`（简洁约束、Harness 边界）、本地图 §4（当前无前端）、§6（依赖归属）。
- 系统层提醒：
  - backend 当前是 **Python API 而非 HTTP 服务**，新增前端须同时决定"是否引入服务层"——`AGENTS.md` 明确要求服务层只有在真实 caller 需要时才加，每个新抽象必须保护五大边界之一。
  - `.env.example` 的 `CORS_ORIGINS=http://localhost:8500` 已预留前端端口（疑似 Streamlit），属未实现边界。
  - 事件 / 产物 / 三 SQLite 库归属 backend，新子项目应通过明确接口访问而非直连 DB。
  - 新增子项目后须更新本地图 §2 子项目职责表、§3 调用链、§4 接口边界。

---

## 8. 集成风险检查清单

提炼自 `backend/.planning/codebase/CONCERNS.md`，每条附"验证入口"（如何在代码中复核）。

| # | 风险 | 验证入口 |
|---|------|----------|
| 1 | **MinerU 地址硬编码内网 IP，部署到其它网络即不可用**；明文 HTTP 无 TLS；服务不可用时硬失败、无重试/降级。 | `backend/tools.py:12`（`MINERU_BASE_URL` 常量）、`tools.py:64/86/93`（拼 URL 处）、`tools.py:74/87/94`（`raise_for_status`）、`tools.py:97`（`TimeoutError` 默认 900s） |
| 2 | **`.env.example` 键与实现脱节（死配置）**：`MINERU_BASE_URL`/`MINERU_BACKEND`/`MINERU_TIMEOUT_SECONDS` 已定义但 `tools.py` 全程不 `os.getenv` 读取，地址与参数均硬编码。 | `backend/tools.py` grep `os.getenv`（无 MinerU 相关命中）对照 `backend/.env.example` |
| 3 | **范围蔓延前兆：Oracle 预埋与 MinerU 里程碑无关**。`.env.example` 已含 5 个 `ORACLE_*` 键、`backend/instantclient/` 二进制已提交进 git，但 backend 源码零 Oracle 引用、`backend/pyproject.toml` 未列 `oracledb`/`cx_Oracle`。配置先于实现进入仓库。 | `backend/.env.example:15-19`、`git ls-files backend/instantclient/`、grep `oracle/cx_Oracle/oracledb` 在 `backend/*.py`（零命中） |
| 4 | **入口可用性回退**：未提交改动 `M backend/session.py` + `D backend/__main__.py`——`session.py::main()` 被改为硬编码 `message="你好"` + 随机 session_id，覆盖了原 `argparse` CLI；`__main__.py` 已删，`python -m backend` 入口失效；`argparse` 成遗留未使用 import。可用入口仅 `run_session(...)` 与 `python -m backend.self_check`。 | `git status` / `git diff backend/session.py`、`backend/session.py:3`（未使用 `argparse` import）、确认无 `backend/__main__.py` |
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
| `backend/.planning/codebase/INTEGRATIONS.md` | MinerU/DeepAgents/SQLite/MiniMax 等集成边界与集成调用链 |
| `backend/.planning/codebase/CONVENTIONS.md` | 命名/模块组织/类型/错误处理/日志/配置/持久化约定 |
| `backend/.planning/codebase/TESTING.md` | self_check 角色、运行命令、验证入口、覆盖缺口 |
| `backend/.planning/codebase/CONCERNS.md` | 外部依赖/安全/稳定性/可观测性/范围蔓延/跨平台风险 |

> 源文档由 `scripts/collect_map_sources.ps1` 发现（输出 JSON 已确认无 asset 知识包、无已有地图）。本地图保持系统层，不复制底层实现细节；如需细节请直接阅读上表对应文档。
