# 集成边界 (Integrations)

> 事实来源：backend/ 源码与 backend/pyproject.toml + uv.lock（2026-07-02 生成）

本文描述 `backend/` 与外部系统/服务的集成边界。证据强度以"已确认 / 需确认"区分：**已确认** = 直接见于 `backend/` 源码或 `backend/pyproject.toml`；**需确认** = 仅见于 `.env.example` 或规划文档、源码无直接引用。

## 1. MinerU 异步任务 API（已确认）

- **边界位置**：`backend/tools.py` 中的 `parse_document_with_mineru`（被 `default_tool_catalog()` 注册为工具）。
- **服务地址**：`MINERU_BASE_URL = "http://10.11.0.110:6006"`（`backend/tools.py:12`，源码硬编码）。注：`.env.example` 有 `MINERU_BASE_URL=`、`MINERU_BACKEND=`、`MINERU_TIMEOUT_SECONDS=` 三个键，但 `backend/tools.py` 当前**未读取**这些环境变量——base url 为常量、`backend`/`effort` 为写死、timeout 为函数参数。**需确认**：环境变量键是否在后续接入。
- **三步调用流程**（均为同步阻塞的 `requests`）：
  1. **提交任务** `POST /tasks`（`_submit_task`）：multipart 上传文件 `files=[("files", (source.name, handle, mime))]`，表单字段固定为 `backend=hybrid-engine`、`effort=high`、`return_md=true`、`response_format_zip=false`；`timeout=60`。从响应里递归查找键 `task_id / taskId / id` 得到 `task_id`。
  2. **轮询状态** `GET /tasks/{task_id}`（`_wait_for_result`）：`timeout=30`，读取 `status / state`；命中 `FAILURE_STATES`（`failed/error/cancelled/...`）抛错，命中 `SUCCESS_STATES`（`completed/done/success/...`）进入取结果，否则 `time.sleep(poll_interval_seconds)`（默认 2.0s）继续轮询，直到 `timeout_seconds`（默认 900s）超时抛 `TimeoutError`。
  3. **取结果** `GET /tasks/{task_id}/result`（`_wait_for_result`）：`timeout=120`；`_extract_markdown` 从结果里递归查找键 `md / markdown / md_content / markdown_content`，写出为本地 Markdown 文件。
- **固定参数**：`backend=hybrid-engine`、`effort=high` 不可由调用方更改（Milestone 约束）。
- **产出落点**：默认输出 `backend/data/mineru_outputs/{stem}.md`（`_default_output_path`，`Path(__file__).resolve().parent/"data"/"mineru_outputs"`），可经 `output_path` 覆盖。
- **认证**：源码未携带任何鉴权头/token；**需确认**该内网端点是否需要鉴权。

## 2. DeepAgents（可插拔 Brain / 子 Harness）（已确认）

- **边界位置**：`backend/harness.py` 的 `DeepAgentsBrainFactory`（实现 `BrainFactory` Protocol）。
- **集成方式**：通过 `from deepagents import create_deep_agent` 创建 Brain，传入 `model`、`tools`、`system_prompt`、`middleware`、`backend`、`checkpointer`、`store`。Brain 暴露 `invoke(payload, config)` 接口（`Brain` Protocol）。
- **调用约定**：`HarnessRuntime.run_turn` 以 `{"messages": _reset_messages(context)}`、`config={"configurable": {"thread_id": session_id}}` 调用 `brain.invoke`。`_reset_messages` 在上下文前插入 `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 实现上下文重置后再回放。
- **后端注入**：Brain 复用 `AgentResources` 提供的 `CompositeBackend` / `checkpointer` / `store`（见下文），DeepAgents 内置虚拟文件系统通过该 `backend` 暴露给模型（`/memories/`、`/artifacts/` 等路径）。
- **可替换性**：`BrainFactory` 是 Protocol，`backend/self_check.py` 用 `_FakeBrainFactory` 证明 Brain 可被替换（DeepAgents 并非硬绑定）。

## 3. DeepAgents 内置虚拟文件系统 / CompositeBackend（已确认）

- **边界位置**：`backend/resources.py` 的 `AgentResources.__enter__`。
- **路由规则**（`CompositeBackend`，`default=StateBackend()`）：
  - `/memories/`、`/conversation_history/`、`/logs/` → `StoreBackend(store=SqliteStore, namespace=("dsagents",))`（持久）。
  - `/artifacts/`、`/large_tool_results/` → `FilesystemBackend(root_dir=backend/data/artifacts, virtual_mode=True)`（落盘）。
  - 其余路径 → `StateBackend()`（图状态/内存，默认）。
- **作用**：模型经 DeepAgents 虚拟文件系统写"记忆/历史/日志"时落到 SQLite Store，写"大产物/大工具结果"时落到本地磁盘，写一般内容时随图状态保存。

## 4. SQLite StoreBackend / Checkpointer（已确认）

- **边界位置**：`backend/resources.py`。三个独立的 SQLite 数据库文件，全部锁定在 `backend/data/` 下（`_BACKEND_DIR = Path(__file__).resolve().parent`，与 CWD 无关）：
  - **会话事件库**：`backend/data/dsagents_sessions.db`，由 `backend/session.py` 的 `SqliteSessionStore` 用标准库 `sqlite3` 自建表 `sessions`、`session_events`（append-only 事件流，含索引 `idx_session_events_session_order`）。
  - **Store 库**：`backend/data/dsagents_store.db`，`SqliteStore.from_conn_string(...)` + `.setup()`，供 `StoreBackend` 持久化记忆/历史/日志。
  - **Checkpoint 库**：`backend/data/dsagents_checkpoints.db`，`SqliteSaver.from_conn_string(...)` + `.setup()`，供 LangGraph 线程状态检查点（`thread_id=session_id`）。
- **生命周期**：`AgentResources` 实现 context manager，`__enter__` 创建库表与目录，`__exit__` 经 `ExitStack` 关闭 store/checkpointer 句柄。
- **无远程 DB**：均为本地文件 SQLite，无连接串/网络；未发现其它数据库客户端依赖。

## 5. 本地文件系统产物目录（已确认）

- **边界位置**：`backend/resources.py`（`ResourceConfig`）与 `backend/session.py`、`backend/tools.py`。
- **目录**：根目录 `backend/data/`（`ResourceConfig.data_dir = _BACKEND_DIR / "data"`，与 CWD 无关），其中：
  - `backend/data/artifacts/` —— DeepAgents `FilesystemBackend` 根目录（大产物、大工具结果）。
  - `backend/data/artifacts/session-events/` —— 大事件 payload 溢出 JSON（`SqliteSessionStore`，阈值 `max_inline_bytes=262_144` 即 256KiB，超阈值把原始 payload 写成 `{uuid}.json` 并在 DB 仅存 `artifact_path`）。
  - `backend/data/mineru_outputs/` —— MinerU 解析结果 Markdown（`backend/tools.py::_default_output_path` 默认输出）。
- **创建时机**：`AgentResources.__enter__` 与 `SqliteSessionStore.__init__` 均 `mkdir(parents=True, exist_ok=True)`，目录不存在时自动建立。

## 6. LLM 提供方（MiniMax Anthropic 兼容）（已确认）

- **边界位置**：`backend/harness.py` 的 `DeepAgentsBrainFactory.__init__`。
- **默认模型**：`anthropic:{MINIMAX_MODEL or "MiniMax-M3"}`，默认 base url `https://api.minimaxi.com/anthropic`。
- **初始化方式**：`DeepAgentsBrainFactory` 通过 `langchain.chat_models.init_chat_model(...)` 直接构造 LangChain `ChatAnthropic` 模型对象，再把该对象传给 `create_deep_agent(...)`。这条路径复用 LangChain 的 Anthropic provider 适配，而不是自行包装 `anthropic` SDK。
- **配置来源**：优先读取现有 `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` / `MINIMAX_MODEL`；若存在 `ANTHROPIC_API_KEY` 或 `ANTHROPIC_BASE_URL`，也会作为 fallback 使用。即对外仍保留项目既有 `MINIMAX_*` 键名，但传输协议切到 Anthropic 兼容接口。
- **凭据**：来自 `.env`（`backend/.env.example` 占位 `MINIMAX_API_KEY=`），由 `session.py:15` 的 `load_dotenv` 加载；本文不写入真实密钥。

## 7. 预留 / 规划中集成（需确认）

以下键仅出现在 `backend/.env.example`，`backend/` 自身 `.py` 源码**无任何引用**，视为预留或前端边界：

- **DeepSeek**：`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL=https://api.deepseek.com`、`DEEPSEEK_MODEL=deepseek-v4-flash`。需确认是否作为可切换 LLM 提供方。
- **Oracle**：`ORACLE_DSN`、`ORACLE_USERNAME`、`ORACLE_PASSWORD`、`ORACLE_CLIENT_LIB_DIR`、`ORACLE_TIMEOUT_SECONDS`（仓库已提交 `backend/instantclient/` Oracle Instant Client 19.31 二进制）。`backend/pyproject.toml` 的 `[project.dependencies]` 未列出 `oracledb`/`cx_Oracle`，需确认。
- **LangSmith**：`LANGSMITH_TRACING=false`、`LANGSMITH_ENDPOINT`、`LANGSMITH_PROJECT=DsAgents`，经 LangChain/LangGraph 运行时间接生效。
- **CORS**：`CORS_ORIGINS=http://localhost:8500,http://127.0.0.1:8500` 端口 8500 暗示前端（疑似 Streamlit）；`backend/` 无 FastAPI/uvicorn 等 web 框架，需确认服务层归属。

## 集成调用链

以一次用户输入触发 DeepAgents 解析文档为例的数据流（文字 + 箭头）：

`run_session` (`backend/session.py`) → 构造 `AgentResources` (`backend/resources.py`，初始化 `backend/data/` 下三个 SQLite 库 + `CompositeBackend`) → `create_mineru_harness` (`backend/harness.py`) → `HarnessRuntime.run_turn(message, session_id)`：

1. `Sessions.ensure_session` → 写 `backend/data/dsagents_sessions.db`；
2. `Sessions.emit_event("user_message")` → 写会话事件库；
3. `Sessions.context_window` → 读取最近 `CONTEXT_MESSAGE_LIMIT=20` 条消息（首条须为 user）；
4. `DeepAgentsBrainFactory.create` → `create_deep_agent(...)`，注入 `middleware=TraceHands.middleware`、`tools=[parse_document_with_mineru]`、`backend=CompositeBackend`、`checkpointer=SqliteSaver`、`store=SqliteStore`；
5. `brain.invoke({"messages": [RemoveMessage(REMOVE_ALL_MESSAGES), *ctx]}, config={"configurable": {"thread_id": session_id}})` → DeepAgents 运行图；
6. 模型调用经 `TraceMiddleware.wrap_model_call` 拦截 → emit `model_request/model_response`（出错 emit `model_error` 并上抛）→ 经 LangChain `ChatAnthropic` 调用 MiniMax Anthropic 兼容端点完成 LLM 推理；
7. 若模型决定解析文档 → `TraceMiddleware.wrap_tool_call` 拦截 → `parse_document_with_mineru` (`backend/tools.py`)：
   `POST http://10.11.0.110:6006/tasks`（固定 `backend=hybrid-engine`/`effort=high`）→ 轮询 `GET /tasks/{task_id}` → `GET /tasks/{task_id}/result` → 写 `backend/data/mineru_outputs/{stem}.md` → emit `tool_request/tool_response`；
8. Brain 将大产物经 `CompositeBackend` 路由：`/artifacts/`、`/large_tool_results/` → `backend/data/artifacts/`（`FilesystemBackend`），`/memories/`、`/conversation_history/`、`/logs/` → `SqliteStore`（`backend/data/dsagents_store.db`），线程状态检查点写 `backend/data/dsagents_checkpoints.db`；
9. `run_turn` 取 `result["messages"][-1].content` → emit `assistant_message` → 返回 `HarnessTurn`。
