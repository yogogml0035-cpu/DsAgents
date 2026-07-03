# 接口边界 (INTERFACES)

> 事实来源：backend/.planning/codebase/ 与 coding_maps/SYSTEM_MAP.md（2026-07-03 生成，本轮刷新）

本文件是 DsAgents 仓库的**接口与集成边界**文档，描述已确认的接口边界、未证实的跨系统关系、任务排查建议与可扩展集成入口。系统级架构见 `ARCHITECTURE.md`；全局原则与入口见 `AGENTS.md`。

证据强度区分：**已确认** = 直接见于 `backend/` 源码或 `backend/pyproject.toml`（依赖版本由 `backend/uv.lock` 锁定，包管理器为 uv）；**需确认** = 仅见于 `.env.example` 或规划文档、源码无直接引用。本文不写入任何密钥 / token / 连接串。

---

## 1. 已确认接口边界

### 1.1 FastAPI HTTP API（对外 transport）

- **边界位置**：`backend/api.py`，公开导出 `create_app(...)` 与模块级 `app`。HTTP 层本身不持有独立 service/manager；它在 FastAPI lifespan 启动时装配一次共享 `AgentResources` / `HarnessRuntime`，并把 HTTP 三种 Agent POST 统一收口到 `HarnessRuntime.execute_run(...)`。
- **阻塞消息接口**：`POST /sessions/messages`
  - 请求 JSON：`{"message": "...", "session_id": null | "..."}`。
  - 行为：若 `session_id` 为空则服务端生成 `uuid.uuid4().hex`；请求被接受时统一创建 `run_id`，同一 `session_id` 若已有运行中的 Agent run 则返回 HTTP 409。
  - 响应 JSON：`{"session_id":"...","run_id":"...","status":"succeeded","reply":"..."}` 或 `{"session_id":"...","run_id":"...","status":"failed","error":"..."}`。
- **SSE 流式接口**：`POST /sessions/messages/stream`
  - 请求 JSON 同上。
  - 行为：同样先创建 `run_id`，随后统一走 `brain.stream(..., stream_mode=["messages","custom","values"], version="v2")`。
  - SSE 事件：首条 `session`（`{"session_id":"...","run_id":"...","status":"queued"}`）→ 零到多条 `run_event`（事件体稳定为 `event_id/run_id/type/created_at/payload/raw`）→ `done`。
- **后台接口**：`POST /sessions/messages/runs`
  - 请求 JSON 同上。
  - 行为：创建 `run_id` 后立即返回 queued；服务端进程内后台线程继续执行，进程重启不可恢复。
  - 响应 JSON：`{"session_id":"...","run_id":"...","status":"queued"}`。
- **run 查询接口**：`GET /runs/{run_id}`
  - 查询参数：可选 `after_event_id=<int>`。
  - 响应 JSON：`{"run": {...}, "events": [...]}`；不带 cursor 返回累计全量事件，带 cursor 只返回新增事件。
- **session run 列表接口**：`GET /sessions/{session_id}/runs`
  - 响应 JSON：按创建时间倒序返回 `run_id/status/created_at/updated_at/reply_preview/error_preview`。
- **上传接口**：`POST /files`
  - 请求：`multipart/form-data` 字段 `file`。
  - 保存：`backend/data/artifacts/uploads/<uuid>_<clean_filename>`；文件名只取 basename，空名回退 `upload`。
  - 响应：`{"file_path":"/artifacts/uploads/<uuid>_<clean_filename>"}`。
- **显式不做**：源码未见鉴权、中间租户层、上传大小限制、CORS middleware、`/health` 健康检查端点、Redis/外部队列、取消/自动重试。

### 1.2 MinerU 异步任务 API（当前文档解析 provider）

- **边界位置**：公开入口是 `backend/tools.py::parse_document`（经 `default_tool_catalog()` 注册为工具）；实际 HTTP 调用留在私有 helper `_submit_mineru_task` / `_wait_for_mineru_result`。
- **服务地址**：`parse_document` 在调用时读取 `MINERU_BASE_URL`；`.env.example` 当前示例值为 `http://10.11.0.110:6006`。
- **三步调用流程**（均为同步阻塞的 `requests`）：
  1. **提交任务** `POST /tasks`：multipart 上传文件，`backend` / `effort` 分别来自 `MINERU_BACKEND` / `MINERU_EFFORT`，其余表单字段仍固定 `return_md=true`、`response_format_zip=false`；`timeout=60`。从响应递归查找 `task_id / taskId / id` 得到 `task_id`。
  2. **轮询状态** `GET /tasks/{task_id}`：`timeout=30`，读 `status / state`；命中失败态抛错，命中成功态进入取结果，否则 `time.sleep(2.0)` 继续，直到 `MINERU_TIMEOUT_SECONDS`（经 `int(...)` 转换）超时抛 `TimeoutError`。
  3. **取结果** `GET /tasks/{task_id}/result`：`timeout=120`；递归查找 `md / markdown / md_content / markdown_content`，写出本地 Markdown。
- **公开参数**：工具只暴露 `file_path` 与可选 `output_path`；provider 参数全部走 `MINERU_*` 环境变量。
- **产出落点**：默认 `backend/data/document_outputs/{stem}.md`，可经 `output_path` 覆盖。
- **认证**：源码未携带任何鉴权头/token；需确认该内网端点是否需要鉴权。
- **传输**：明文 HTTP 无 TLS。

> `.env.example` 当前提供 `MINERU_BASE_URL`、`MINERU_BACKEND`、`MINERU_EFFORT`、`MINERU_TIMEOUT_SECONDS` 示例值；`parse_document` 在调用路径读取它们。缺失会抛 `RuntimeError`，非法 `MINERU_TIMEOUT_SECONDS` 直接暴露原生 `ValueError`。

### 1.3 DeepAgents BrainFactory Protocol（可插拔 Brain）

- **边界位置**：`backend/harness.py` 的 `DeepAgentsBrainFactory`（实现 `BrainFactory` Protocol）。
- **集成方式**：`from deepagents import create_deep_agent` 构建 Brain，传入 `model`、`tools`、`system_prompt`、`middleware`、`backend`、`checkpointer`、`store`。Brain 暴露 `invoke(payload, config)` 接口（`Brain` Protocol）。
- **调用约定**：`HarnessRuntime.run_turn` 以 `{"messages": _reset_messages(context)}`、`config={"configurable": {"thread_id": session_id}}` 调用 `brain.invoke`。`_reset_messages` 在上下文前插入 `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 实现重置后回放。
- **后端注入**：Brain 复用 `AgentResources` 提供的 `CompositeBackend` / `checkpointer` / `store`。DeepAgents 内置虚拟文件系统通过该 `backend` 暴露给模型。
- **可替换性**：`BrainFactory` 是 Protocol，`backend/self_check.py` 用 `_FakeBrainFactory` 证明 Brain 可被替换——DeepAgents 并非硬绑定。

### 1.4 DeepAgents CompositeBackend 虚拟文件系统路由

- **边界位置**：`backend/resources.py::AgentResources.__enter__`。
- **路由规则**（`CompositeBackend`，`default=StateBackend()`）：
  - `/memories/`、`/conversation_history/`、`/logs/` → `StoreBackend(store=SqliteStore, namespace=("dsagents",))`（持久，落 SQLite）。
  - `/artifacts/`、`/large_tool_results/` → `FilesystemBackend(root_dir=backend/data/artifacts, virtual_mode=True)`（落盘）。
  - 其余路径 → `StateBackend()`（图状态/内存，默认）。
- **作用**：模型写"记忆/历史/日志"落 SQLite Store，写"大产物/大工具结果"落本地磁盘，写一般内容随图状态保存。遵循根 `AGENTS.md`"使用 DeepAgents 内置虚拟文件系统，不另加包装"。

### 1.5 Python 导入 API（对外主接口）

`backend/` **不是常规 Python 包**（没有 `__init__.py` / `__main__.py`），而是扁平顶层模块（`pyproject.toml` 的 `py-modules`）。模块之间用绝对导入（`from session import ...`），因此对外 Python API 也是**扁平顶层**导入（**不带** `backend.` 前缀），与上面的 FastAPI HTTP API 并存：

| API | 位置 | 用途 |
|-----|------|------|
| `run_session(message, session_id=None)` | `backend/session.py` | 最小 session runner：装配资源 → 单轮执行 → 返回 `result` |
| `create_harness(resources)` | `backend/harness.py` | 由 resources 构造通用文档解析工具 + DeepAgents Brain 的 `HarnessRuntime` |
| `parse_document(file_path, output_path=None)` | `backend/tools.py` | 通用文档解析工具（当前 provider 为 MinerU，亦可直接调用） |

典型用法：`from session import run_session; run_session("帮我解析 xxx.pdf")`（需在 `backend/` 目录下或把 `backend/` 加入 `PYTHONPATH`）。**没有** `python -m backend` / `python -m backend.self_check` / `python -m backend.session`（无 `backend` 包）。可用命令入口为 `python backend/self_check.py`（自检）与 `python backend/session.py`（冒烟）。

### 1.6 三条独立 SQLite 持久化通道

| 用途 | 路径 | 谁建/写 |
|------|------|---------|
| 会话/Run 事件库（append-only） | `backend/data/dsagents_sessions.db` | `SqliteSessionStore`（标准库 `sqlite3`，`backend/session.py`） |
| LangGraph Store（持久记忆/历史/日志） | `backend/data/dsagents_store.db` | `SqliteStore.from_conn_string` + `.setup()`（`backend/resources.py`） |
| LangGraph Checkpoint（线程状态检查点） | `backend/data/dsagents_checkpoints.db` | `SqliteSaver.from_conn_string` + `.setup()`（`backend/resources.py`） |

三库相互独立，均由 `AgentResources.__enter__` 创建 + `.setup()`，`__exit__` 经 `ExitStack` 关闭。均为本地文件 SQLite，无连接串/网络、无远程 DB。会话/Run 事件库内同时维护 `sessions` / `session_events` / `runs` / `run_events` 四张表；两类事件都保持 append-only，超大 payload 会外溢到 `backend/data/artifacts/session-events/<uuid>.json` 或 `backend/data/artifacts/run-events/<uuid>.json`，DB 仅存 `{artifact_path, bytes}` 指针。

### 1.7 MiniMax LLM（Anthropic 兼容，默认 LLM 提供方）

- **边界位置**：`backend/harness.py::DeepAgentsBrainFactory.__init__`。
- **初始化方式**：当 `model is None` 时执行 `init_chat_model(f"anthropic:{os.getenv('MINIMAX_MODEL')}", api_key=os.getenv("MINIMAX_API_KEY"), base_url=os.getenv("MINIMAX_BASE_URL"), thinking={"type": "adaptive"})`，构造 LangChain `ChatAnthropic` 模型对象（经 MiniMax 的 Anthropic 兼容端点、走 Anthropic 协议），再交给 `create_deep_agent(...)`。复用 LangChain 的 Anthropic provider 适配，**不**自行包装 `anthropic` SDK，也**不**再手工覆写 `OPENAI_*` 环境变量。
- **Thinking 输出**：默认模型固定传 `thinking={"type": "adaptive"}`。流式 HTTP 接口从 LangChain `messages` chunk 里提取 Anthropic/MiniMax `thinking` 内容块或标准 `reasoning` 内容块，并作为 `thinking_delta` SSE 事件发给前端。
- **配置来源（单一、无 fallback）**：**仅**读取 `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 三个 env（commit `a30bb99` 切换 Anthropic 兼容协议、`9c78cf2` 移除 fallback）。**无默认值、无 fallback**：既无 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 回退，也无 `MINIMAX_*` → `OPENAI_*` 复制；env 未设置时 `os.getenv` 返回 `None` 直传 provider，行为由 provider 决定（fail-forward，缺失配置的诊断推迟到首次模型调用）。
- **配置加载**：`.env` 由 `backend/session.py:14` 在导入时 `load_dotenv` 加载（`tools.py:16` 也在导入时同样加载；不是 `__init__.py`，因为没有 `__init__.py`）。

---

## 2. 未证实的跨系统关系（需确认）

以下键仅出现在 `backend/.env.example`，`backend/` 自身 `.py` 源码**无任何直接引用**，视为预留、规划中或前端边界。改动前须先确认归属，不要当作已生效集成处理：

| 名称 | `.env.example` 键 | 现状与判断 |
|------|-------------------|------------|
| **DeepSeek** | `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL=https://api.deepseek.com`、`DEEPSEEK_MODEL=deepseek-v4-flash` | backend 源码零引用，疑似预留为可切换 LLM 提供方，需确认。 |
| **Oracle** | `ORACLE_DSN`、`ORACLE_USERNAME`、`ORACLE_PASSWORD`、`ORACLE_CLIENT_LIB_DIR`、`ORACLE_TIMEOUT_SECONDS` | backend 源码零引用；`backend/pyproject.toml` 的 `[project.dependencies]` 未列 `oracledb`/`cx_Oracle`；但 `backend/instantclient/` Oracle 二进制已提交进 git。配置先于实现进入仓库，疑似范围蔓延前兆，需确认是否有未列入里程碑的 Oracle 数据源需求。 |
| **LangSmith** | `LANGSMITH_TRACING=false`、`LANGSMITH_ENDPOINT`、`LANGSMITH_PROJECT=DsAgents` | 默认关闭，backend 源码无直接引用，经 LangChain/LangGraph 运行时间接生效。若误开启会把 trace 上传外部服务。需确认是否计划启用。 |
| **CORS / 前端** | `CORS_ORIGINS=http://localhost:8500,http://127.0.0.1:8500` | 端口 8500 暗示 Streamlit。backend 现已存在 `api.py` FastAPI HTTP 层，但源码仍未读取该配置、也未装配 CORS middleware。属预留 / 前端边界。 |

---

## 3. 任务排查建议

按任务类型，应**先读**下列事实文档（路径相对仓库根），再到本文件定位接口边界：

- **改文档解析工具**：读 `backend/.planning/codebase/INTEGRATIONS.md` §2（三步 API、`MINERU_*` 配置、字段模糊匹配）、`CONCERNS.md` §1（硬失败、明文 HTTP、无重试/降级）。提醒：模型侧公开工具名是 `parse_document`；当前 provider 仍是 MinerU，协议字段变更会冲击 `tools.py::_find_value` 模糊匹配。
- **改存储 / 持久化**：读 `backend/.planning/codebase/STRUCTURE.md` §4（资源目录约定）、`INTEGRATIONS.md` §4-6（CompositeBackend 路由 + 三 SQLite 库 + 本地产物目录）。提醒：会话事件 append-only、不可 update/delete；三库相互独立；超大 payload 外溢机制需保持。
- **加 / 改 Provider（LLM 或外部服务）**：读本文件 §1.6 / §2、`backend/.planning/codebase/STACK.md`。提醒：当前唯一已接入的外部文档解析 provider 是 MinerU；MiniMax 默认经 Anthropic 兼容协议接入，优先走 `MINIMAX_*` 配置；DeepSeek/Oracle/LangSmith/CORS 均需先确认归属再动。
- **改 Brain / Harness 执行**：读 `backend/.planning/codebase/ARCHITECTURE.md` §4-5（运行时数据流、关键设计决策）、本文件 §1.3。提醒：保持 `BrainFactory` Protocol 可替换、Harness 薄、真实错误透传。
- **改可观测 / trace**：读 `backend/.planning/codebase/CONCERNS.md` §4。提醒：middleware 仅记录模型可见层，不触碰隐藏思维链；trace 写入 SQLite 的错误事件含 `repr(exc)`，当前无脱敏。

---

## 4. 可扩展集成入口

未来扩展时，应沿下列边界接入，避免破坏五大模块边界（详见 `AGENTS.md` Harness 原则与 `ARCHITECTURE.md` §5）：

- **扩展前端 / HTTP 层**：当前 backend 已有薄 FastAPI HTTP 服务，但仍只有三个最小端点，且未做鉴权、CORS、健康检查。若新增前端子项目或扩展 HTTP 契约，仍应保持 transport 薄，不新增第二套 service 框架；新增子项目后须在 `coding_maps/SYSTEM_MAP.md` §2-4 同步子项目职责表、调用链、接口边界。
- **新增可插拔 Brain / runner**：实现 `BrainFactory` Protocol 即可（参照 `DeepAgentsBrainFactory` 与 `self_check.py::_FakeBrainFactory`），不要把新 runner 硬绑到现有工具/资源。
- **新增工具**：实现 `ToolHandler` 并加入 `ToolCatalog`，由 Harness 注入；工具不绑定单一 runner。
- **新增 LLM Provider**：参照 `DeepAgentsBrainFactory` 的 LangChain provider 初始化方式（`init_chat_model("provider:model", ...)` 直接构造模型对象）；DeepSeek 等 `.env.example` 预留键的归属需先确认。
- **新增持久化通道**：当前三 SQLite 库 + 文件系统产物均由 `AgentResources` / `ResourceConfig` 集中管理。新增存储应经同一资源装配层，保持会话事件 append-only 语义。
- **新增子项目访问 backend 数据**：应通过明确接口访问而非直连 SQLite 库；事件 / 产物 / 三库归属 backend。

> 完整集成调用链（用户输入 → MinerU 解析 → 产物落盘的全链路）见 `coding_maps/SYSTEM_MAP.md` §3 与 `backend/.planning/codebase/INTEGRATIONS.md`「集成调用链」小节。
