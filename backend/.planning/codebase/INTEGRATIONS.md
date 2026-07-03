# 集成边界 (Integrations)

> 事实来源：backend/ 源码 + pyproject.toml/uv.lock（2026-07-03 刷新）

本文描述 `backend/` 与外部系统/服务的集成边界。证据强度以"已确认 / 需确认"区分：**已确认** = 直接见于 `backend/` 源码或 `backend/pyproject.toml`；**需确认** = 仅见于 `.env.example` 或规划文档、源码无直接引用。

## 1. FastAPI HTTP API（当前 transport，已确认）

- **边界位置**：`backend/api.py`，公开导出 `create_app(*, resource_config=None, harness_factory=create_harness)` 与模块级 `app = create_app()`。
- **生命周期**：`lifespan` 启动期创建一次 `AgentResources`（`__enter__` 初始化三条 SQLite 通道 + `CompositeBackend`），调用 `sessions.fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` 把启动前残留的 `queued/running` run 标记为 `failed`（`startup_interrupted`），随后存于 `app.state.resources` / `app.state.harness`；并初始化 `app.state.session_locks`（`dict[str, threading.Lock]`）、`app.state.active_runs`（`dict[str, str]`）、`app.state.registry_lock`。所有 HTTP 请求复用同一 `AgentResources` / `HarnessRuntime`。
- **并发单飞**：每个端点经 `_acquire_session_run(app, session_id, run_id)` 拿到 per-session 的 `threading.Lock`（非阻塞 `acquire(blocking=False)`）；若拿不到锁则返回 `409`（见下文），从而保证**同一 session 至多一个 run 同时执行**。
- **六个端点**：

### 1.1 `POST /sessions/messages`（阻塞 run，已确认）
- 请求 JSON：`{"message": "...", "session_id": null | "..."}`（`MessageRequest`，`session_id` 可空）。
- 行为：`session_id` 为空则服务端生成 `uuid.uuid4().hex`；`run_id` 始终服务端生成。拿锁失败 → `409`。拿锁成功 → `sessions.create_run(session_id, run_id)`（初始 `queued`）→ `_run_blocking` 同步迭代 `harness.execute_run(...)` 直至结束 → 释放锁。
- 成功响应（`200`，`_blocking_response`）：
  - 成功：`{"session_id":"...","run_id":"...","status":"succeeded","reply":"..."}`。
  - 失败：`{"session_id":"...","run_id":"...","status":"failed","error":"..."}`。

### 1.2 `POST /sessions/messages/stream`（SSE 流式 run，已确认）
- 请求 JSON 同上。
- 行为：拿锁失败 → `409`。拿锁成功 → `sessions.create_run(...)`（`create_run` 抛错则先释放锁再上抛）→ 返回 `StreamingResponse`（`media_type="text/event-stream"`、`Cache-Control: no-cache`）。`event_stream()` 生成器先发 `session` 事件，然后逐条 `yield` `harness.execute_run(...)` 产出的 `run_event`，异常经 `_ensure_failed_run` 兜底，`finally` 释放锁，末尾发 `done`。
- SSE 事件（`_sse_event`，格式 `event: <name>\ndata: <json>\n\n`）：
  - `session`：`{"session_id":"...","run_id":"...","status":"queued"}`。
  - `run_event`（多条）：data 为 `_run_event_body(event)`，即 `{"event_id":int,"run_id":"...","type":"...","created_at":"...","payload":...,"raw":...}`；`type` 序列经 `execute_run`：首条 `status(running)`，中段 `thinking` / `text_delta` / `tool_status` / `values`，末条 `status(succeeded|failed)`。
  - `done`：`{"session_id":"...","run_id":"..."}`（无论成败都发）。

### 1.3 `POST /sessions/messages/runs`（后台 run，已确认）
- 请求 JSON 同上。
- 行为：拿锁失败 → `409`。拿锁成功 → `sessions.create_run(...)`（`create_run` 抛错则先释放锁再上抛）→ 起 `threading.Thread(target=_run_background, daemon=True)`；`_run_background` 在后台迭代 `harness.execute_run(...)`，`finally` 释放锁。`start()` 抛错则 `_ensure_failed_run` 兜底并释放锁，返回当前 run 快照。
- 成功响应（`200`）：`{"session_id":"...","run_id":"...","status":"queued"}`。客户端随后用 `GET /runs/{run_id}` 轮询结果。

### 1.4 `GET /runs/{run_id}`（run 详情 + 事件游标，已确认）
- 查询参数：`after_event_id: int | None`（事件游标，仅返回 `event_id > after_event_id` 的事件）。
- 行为：`sessions.get_run(run_id)` + `sessions.get_run_events(run_id, after_event_id=...)`；`KeyError` → `404` `{"error":"Unknown run: {run_id}"}`。
- 成功响应（`200`）：
  ```json
  {
    "run": {
      "run_id": "...", "session_id": "...", "status": "queued|running|succeeded|failed",
      "created_at": "...", "updated_at": "...",
      "reply": null | "...", "error": null | "...",
      "reply_preview": null | "...", "error_preview": null | "..."
    },
    "events": [ {"event_id":int,"run_id":"...","type":"...","created_at":"...","payload":...,"raw":...}, ... ]
  }
  ```
- `status` 由 `run_events` 中 `type="status"` 的事件回放投影得出（`_project_run`）；`reply`/`error` 取最后一条带 `reply`/`error` 的 status 事件。

### 1.5 `GET /sessions/{session_id}/runs`（会话 run 列表，已确认）
- 行为：`sessions.list_runs(session_id)`，按 `created_at desc` 排序。
- 成功响应（`200`，`list[dict]`，`_run_list_item`）：
  ```json
  [ {"run_id":"...","status":"...","created_at":"...","updated_at":"...","reply_preview":null|"...","error_preview":null|"..."}, ... ]
  ```

### 1.6 `POST /files`（文件上传，已确认）
- 请求：`multipart/form-data` 字段 `file`（`UploadFile = File(...)`）。
- 行为：`_clean_filename` 取 basename（`\\`/`/` 都处理，空名回退 `upload`）；存到 `config.artifacts_dir/uploads/{uuid.uuid4().hex}_{filename}`（`shutil.copyfileobj`）。
- 成功响应（`200`）：`{"file_path":"/artifacts/uploads/{stored_name}"}`（`_virtual_upload_path`）。返回虚拟路径供后续消息引用。

### 1.7 通用语义
- **`409` 冲突**（`_conflict_response`）：当某 session 已有 run 在执行（`session_locks[session_id]` 拿不到锁）时返回：
  ```json
  {"error": "该会话正在运行", "active_run_id": "<当前 run_id 或 null>"}
  ```
  `active_run_id` 来自 `app.state.active_runs`（拿锁时写入，释放时移除）。
- **状态码集合**：成功 `200`，run 不存在 `404`（仅 `GET /runs/{run_id}`），并发冲突 `409`。
- **后台 run 单飞**：`POST /sessions/messages/runs` 起 `threading.Thread` 后立即返回 `queued`；线程内 `execute_run` 把进度写入 `run_events`，客户端用 `GET /runs/{run_id}` + `after_event_id` 游标轮询增量。
- **显式不做**：源码未见鉴权、中间租户层、上传大小限制、CORS middleware、`/health` 健康检查端点。

## 2. MinerU 异步任务 API（当前文档解析 provider，已确认）

- **边界位置**：`backend/tools.py` 中的公开工具 `parse_document(file_path, output_path=None)`（被 `default_tool_catalog()` 注册为唯一 handler）；实际 HTTP 调用留在私有 helper `_submit_mineru_task` / `_wait_for_mineru_result`。
- **配置**（`parse_document` 在调用时经 `_required_env` 读取，任一缺失则 `_required_env` 抛 `RuntimeError("Missing required environment variable: <NAME>")`）：`MINERU_BASE_URL`、`MINERU_BACKEND`、`MINERU_EFFORT`、`MINERU_TIMEOUT_SECONDS`（经 `int(...)` 转换）。`.env.example` 示例值 `MINERU_BASE_URL=http://10.11.0.110:6006`（仅内网）、`MINERU_BACKEND=hybrid-engine`、`MINERU_EFFORT=high`、`MINERU_TIMEOUT_SECONDS=900`。
- **三步调用流程**（均为同步阻塞的 `requests`）：
  1. **提交任务** `POST {MINERU_BASE_URL}/tasks`（`_submit_mineru_task`，`timeout=60`）：multipart 上传 `files=[("files", (source.name, handle, mime))]`（mime 由 `mimetypes.guess_type` 推断，缺省 `application/octet-stream`），表单字段 `backend`/`effort` 来自 `MINERU_BACKEND`/`MINERU_EFFORT`，`return_md=true`、`response_format_zip=false` 固定。从响应里经 `_find_value` 递归查找键 `task_id / taskId / id`（字段模糊匹配）得到 `task_id`；找不到抛 `RuntimeError`。
  2. **轮询状态** `GET {MINERU_BASE_URL}/tasks/{task_id}`（`_wait_for_mineru_result`，`timeout=30`）：经 `_find_value` 递归查找键 `status / state`。命中 `FAILURE_STATES`（`failed/failure/error/errored/cancelled/canceled`）抛 `RuntimeError`；命中 `SUCCESS_STATES`（`completed/complete/done/finished/success/succeeded`）进入取结果；否则 `time.sleep(2.0)` 继续轮询，直到 `MINERU_TIMEOUT_SECONDS` 超时抛 `TimeoutError`。
  3. **取结果** `GET {MINERU_BASE_URL}/tasks/{task_id}/result`（`_wait_for_mineru_result`，`timeout=120`）：`_extract_markdown` 经 `_find_value` 递归查找键 `md / markdown / md_content / markdown_content`，写出为本地 UTF-8 Markdown 文件。
- **公开参数**：工具只暴露 `file_path` 与可选 `output_path`；provider 参数全部走 `MINERU_*` 环境变量。
- **路径解析**（`_resolve_document_path`）：`/artifacts` 或 `/artifacts/...` 视为虚拟路径，解析到 `ResourceConfig().artifacts_dir`（拒绝含 `..` 的越权路径，抛 `ValueError("Invalid /artifacts path: ...")`）；其余按宿主路径 `Path(raw).expanduser().resolve()` 处理。
- **产出落点**：默认输出 `backend/data/document_outputs/{stem}.md`（`_default_output_path`，`Path(__file__).resolve().parent/"data"/"document_outputs"`），可经 `output_path` 覆盖（同样支持 `/artifacts/...` 虚拟路径）。返回值为 JSON 字符串 `{task_id, source, output_path, markdown_bytes}`。
- **认证**：源码未携带任何鉴权头/token；**需确认**该内网端点是否需要鉴权。

## 3. DeepAgents（可插拔 Brain / BrainFactory Protocol）（已确认）

- **边界位置**：`backend/harness.py` 的 `Brain` / `BrainFactory` Protocol 与 `DeepAgentsBrainFactory`（实现 `BrainFactory`）。
- **Protocol 契约**：
  - `Brain`：需提供 `invoke(payload, config=None) -> dict` 与 `stream(payload, config=None, **kwargs) -> Iterator`。
  - `BrainFactory`：需提供 `create(*, resources, middleware, tools, session_id) -> Brain`。
- **集成方式**：`DeepAgentsBrainFactory.create` 通过 `from deepagents import create_deep_agent` 创建 Brain，传入 `model`、`tools`、`system_prompt`、`middleware`、`backend`、`checkpointer`、`store`（全部来自 `AgentResources`）。
- **调用约定**：
  - `HarnessRuntime.run_turn`（阻塞，被 Python 导入 API / 旧路径用）以 `{"messages": _reset_messages(context)}`、`config={"configurable": {"thread_id": session_id}}` 调用 `brain.invoke`。
  - `HarnessRuntime.execute_run`（被 HTTP `run` 中心端点用）以同样 payload/config 调用 `brain.stream(stream_mode=["messages","custom","values"], version="v2")`，逐 chunk 产出 `RunEvent`。
- **上下文重置**：`_reset_messages` 在上下文前插入 `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 实现先清空再回放（确保 checkpointer 与 session store 的一致语义）。
- **可替换性**：`BrainFactory` 是 Protocol；`backend/self_check.py` 用 `_FakeBrain` / `_FakeBrainFactory` 证明 Brain 可被替换（DeepAgents 并非硬绑定）。`create_app` 的 `harness_factory` 形参也允许整体替换 `HarnessRuntime`。

## 4. CompositeBackend 虚拟文件系统路由（DeepAgents，已确认）

- **边界位置**：`backend/resources.py` 的 `AgentResources.__enter__`。
- **路由规则**（`CompositeBackend`，`default=StateBackend()`）：
  - `/memories/`、`/conversation_history/`、`/logs/` → `StoreBackend(store=SqliteStore, namespace=("dsagents",))`（持久，落 `backend/data/dsagents_store.db`）。
  - `/artifacts/`、`/large_tool_results/` → `FilesystemBackend(root_dir=backend/data/artifacts.resolve(), virtual_mode=True)`（落盘）。
  - 其余路径 → `StateBackend()`（图状态/内存，默认）。
- **作用**：模型经 DeepAgents 虚拟文件系统写"记忆/历史/日志"时落到 SQLite Store，写"大产物/大工具结果"时落到本地磁盘，写一般内容时随图状态保存。`backend/tools.py` 的 `parse_document` 也通过 `/artifacts/...` 虚拟路径与 `FilesystemBackend` 根目录互通。

## 5. SQLite 持久化（三条独立通道，已确认）

- **边界位置**：`backend/resources.py`（装配）+ `backend/session.py`（自建库）。三个独立的 SQLite 数据库文件，全部锁定在 `backend/data/` 下（`_BACKEND_DIR = Path(__file__).resolve().parent`，与 CWD 无关）。
- **会话事件库**：`backend/data/dsagents_sessions.db`，由 `backend/session.py` 的 `SqliteSessionStore` 用标准库 `sqlite3` 自建。表：
  - `sessions(session_id text primary key, created_at text)`。
  - `session_events(event_id integer pk autoincrement, session_id, event_type, created_at, payload_json text not null, artifact_path text)`，索引 `idx_session_events_session_order on (session_id, event_id)`。**append-only**：只插入，不更新/删除。
  - `runs(run_id text primary key, session_id, created_at)`，索引 `idx_runs_session_created on (session_id, created_at desc)`。
  - `run_events(event_id integer pk autoincrement, run_id, event_type, created_at, payload_json, payload_artifact_path, raw_json, raw_artifact_path)`，索引 `idx_run_events_run_order on (run_id, event_id)`。`run_events` 同时存 `payload`（结构化）与 `raw`（原始 chunk）两份 JSON。
- **Store 库**：`backend/data/dsagents_store.db`，`SqliteStore.from_conn_string(...)` + `.setup()`，供 `StoreBackend` 持久化记忆/历史/日志。
- **Checkpoint 库**：`backend/data/dsagents_checkpoints.db`，`SqliteSaver.from_conn_string(...)` + `.setup()`，供 LangGraph 线程状态检查点（`thread_id=session_id`）。
- **超大 payload 外溢**（`SqliteSessionStore._store_blob`，`max_inline_bytes=262144` 即 256KiB）：payload 的 UTF-8 字节数 `<= max_inline_bytes` 时直接存 `payload_json`、`artifact_path=NULL`；超阈值则把完整 payload 写成 `backend/data/artifacts/session-events/{uuid}.json`（session 事件）或 `.../run-events/{uuid}.json`（run 事件），DB 仅存 `{"artifact_path","bytes"}` 占位 JSON 与 `artifact_path`。读取时 `_load_blob` 优先从 `artifact_path` 文件还原。
- **生命周期**：`AgentResources` 实现 context manager，`__enter__` 创建目录与库表（`mkdir` data_dir+artifacts_dir；`_setup` 建表/索引 `if not exists`），`__exit__` 经 `ExitStack` 关闭 store+checkpointer 句柄。
- **无远程 DB**：均为本地文件 SQLite，无连接串/网络；未发现其它数据库客户端依赖。

## 6. Python 导入 API（扁平顶层，已确认）

- **导入形态**：`backend/` 是扁平顶层模块（无 `__init__.py`，`py-modules` 声明 `api/hands/harness/resources/session/tools/self_check`）。模块内绝对导入**不带 `backend.` 前缀**（如 `from harness import HarnessRuntime`、`from api import app`）。
- **进程内入口**：
  - `backend/session.py::run_session(message, session_id=None) -> dict`：`with AgentResources(ResourceConfig()) as resources: return create_harness(resources).run_turn(message, session_id).result`（每次调用重建资源）。
  - `backend/session.py::main()`：示例（`message="你是谁"`，`session_id=uuid4().hex`），打印 `result["messages"][-1].content`。
  - `backend/self_check.py::main()`：内置断言自检脚本（`TestClient` + `patch`），验证模型构造、parse_document fail-fast、`AgentResources`、`TraceHands`、`HarnessRuntime`、`SqliteSessionStore` 外溢、6 个 HTTP 端点、run 清理与 `/artifacts/...` 虚拟路径解析。

## 7. MiniMax LLM（Anthropic 兼容协议，已确认）

- **边界位置**：`backend/harness.py` 的 `DeepAgentsBrainFactory.__init__`（当传入 `model is None` 时构造模型）。
- **初始化方式**：直接以 `init_chat_model(f"anthropic:{os.getenv('MINIMAX_MODEL')}", api_key=os.getenv("MINIMAX_API_KEY"), base_url=os.getenv("MINIMAX_BASE_URL"), thinking={"type": "adaptive"})` 构造 LangChain `ChatAnthropic` 模型对象（经 `langchain.chat_models.init_chat_model`，复用 LangChain 的 Anthropic provider 适配，**不是**自行包装 `anthropic` SDK），再把该对象传给 `create_deep_agent(...)`。提交 `a30bb99`（"切换MiniMax适配为Anthropic兼容协议"）确立此协议路径。
- **Thinking 输出**：默认模型固定传 `thinking={"type": "adaptive"}`。`HarnessRuntime.execute_run` / `stream_turn` 从 `stream_mode=["messages"]` 的 chunk 中经 `_thinking_delta` 识别 Anthropic/MiniMax `thinking` 内容块（`type=thinking` → `thinking`/`text` 字段）、标准 `reasoning` 块（`type=reasoning`）、或 `non_standard` 包装，通过 `thinking_delta` 事件（HTTP SSE）/ yield 元组（Python API）发送给前端；普通文本走 `text_delta`。
- **配置来源**：**仅**读取 `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 三个环境变量，**无默认值、无任何 fallback**：
  - 提交 `9c78cf2`（"fix(backend/harness): remove fallback logic for minimax model config"）已**显式移除**全部回退逻辑（commit body 明确："移除了MiniMax模型配置中的回退逻辑，不再使用ANTHROPIC相关环境变量作为默认备选值"）。
  - **不存在** `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 回退。
  - **不存在**把 `MINIMAX_*` 复制到 `OPENAI_*` 的逻辑（旧文档中的此类"直接赋值"描述已**过时**）。
  - **不存在**硬编码默认模型名或默认 base url。env 未设置时 `os.getenv` 返回 `None`，由 provider 决定行为（fail-forward）。
- **凭据**：来自 `.env`（`backend/.env.example` 占位 `MINIMAX_API_KEY=`、`MINIMAX_BASE_URL=https://api.minimaxi.com/anthropic`、`MINIMAX_MODEL=MiniMax-M3`），由 `session.py:14` 的 `load_dotenv` 加载；本文不写入真实密钥。

## 8. 预留 / 规划中集成（需确认）

以下键仅出现在 `backend/.env.example`，`backend/` 自身 `.py` 源码**无任何引用**，视为预留或前端边界：

- **DeepSeek**：`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL=https://api.deepseek.com`、`DEEPSEEK_MODEL=deepseek-v4-flash`。需确认是否作为可切换 LLM 提供方。
- **Oracle**：`ORACLE_DSN`、`ORACLE_USERNAME`、`ORACLE_PASSWORD`、`ORACLE_CLIENT_LIB_DIR`、`ORACLE_TIMEOUT_SECONDS`（仓库已提交 `backend/instantclient/` Oracle Instant Client 二进制，但无任何 Python import）。`backend/pyproject.toml` 的 `[project.dependencies]` 未列出 `oracledb`/`cx_Oracle`，需确认。
- **LangSmith**：`LANGSMITH_TRACING=false`、`LANGSMITH_ENDPOINT=https://api.smith.langchain.com`、`LANGSMITH_PROJECT=DsAgents`、`LANGSMITH_API_KEY`，经 LangChain/LangGraph 运行时间接生效。
- **CORS**：`CORS_ORIGINS=http://localhost:8500,http://127.0.0.1:8500`（端口 8500 暗示前端，疑似 Streamlit 风格）；`backend/api.py` **未读取**该配置、也**未安装** CORS middleware，需确认是否继续保持内网直连。

## 集成调用链

HTTP 入口（6 端点）与 Python 导入入口（`run_session`）共享同一 `HarnessRuntime` 与 `AgentResources`。以一次用户输入触发 DeepAgents 解析文档为例的端到端数据流：

`POST /sessions/messages`（或 `/runs`、`/stream`，或 `run_session`）→
1. `_acquire_session_run` 拿 per-session 锁（失败 → `409`）；
2. `sessions.create_run(session_id, run_id)` → 写 `backend/data/dsagents_sessions.db`（`runs` 表 + 初始 `run_events(status=queued)`）；
3. `harness.execute_run(message, session_id, run_id)` → `_prepare_turn`：
   - `sessions.ensure_session` + `sessions.emit_event("user_message")`（写 `session_events`）；
   - `sessions.context_window` → 读取最近 `CONTEXT_MESSAGE_LIMIT=20` 条消息（首条须为 user）；
   - `DeepAgentsBrainFactory.create` → `create_deep_agent(...)`，注入 `middleware=[TraceMiddleware]`、`tools=[parse_document]`、`backend=CompositeBackend`、`checkpointer=SqliteSaver`、`store=SqliteStore`；
4. `emit_run_status("running")` → 写 `run_events`；
5. `brain.stream({"messages": _reset_messages(context)}, config={"configurable": {"thread_id": session_id}}, stream_mode=["messages","custom","values"], version="v2")`（`_reset_messages` 前置 `RemoveMessage(REMOVE_ALL_MESSAGES)`）→ DeepAgents 运行图；
6. 模型调用经 `TraceMiddleware.wrap_model_call` 拦截 → emit 会话事件 `model_request`/`model_response`（出错 emit `model_error` 并上抛）+ run 事件 → 经 LangChain `ChatAnthropic`（`base_url=MINIMAX_BASE_URL`，`thinking={"type":"adaptive"}`）调用 MiniMax Anthropic 兼容端点完成 LLM 推理；流式 chunk 经 `_thinking_delta`/`_message_delta` 拆分为 `thinking`/`text_delta` run 事件；
7. 若模型决定解析文档 → `TraceMiddleware.wrap_tool_call` 拦截 + `get_stream_writer` 推 `tool_status(started)` → `parse_document` (`backend/tools.py`)：
   `POST ${MINERU_BASE_URL}/tasks`（`backend`/`effort` 来自 `MINERU_*`，缺失则 `_required_env` 抛 `RuntimeError`）→ 轮询 `GET /tasks/{task_id}` → `GET /tasks/{task_id}/result` → 写 `backend/data/document_outputs/{stem}.md` → emit 会话事件 `tool_request`/`tool_response`（出错 emit `tool_error` 并上抛）+ `tool_status(completed|error)`；
8. Brain 将大产物经 `CompositeBackend` 路由：`/artifacts/`、`/large_tool_results/` → `backend/data/artifacts/`（`FilesystemBackend`）；`/memories/`、`/conversation_history/`、`/logs/` → `SqliteStore`（`backend/data/dsagents_store.db`）；线程状态检查点写 `backend/data/dsagents_checkpoints.db`；
9. `execute_run` 收集 `text_delta` 与 `values` chunk → 拼 `assistant_text` → emit 会话事件 `assistant_message` → `emit_run_status("succeeded", reply=...)` → 释放 per-session 锁；
10. 客户端可经 `GET /runs/{run_id}` + `after_event_id` 游标轮询上述全部 `run_events`（含 `thinking`/`text_delta`/`tool_status`/`status`）。

MinerU 解析 → 产物落盘的精简链：

`parse_document(file_path)` → `_resolve_document_path`（支持 `/artifacts/...` 虚拟路径）→ `_submit_mineru_task`（`POST /tasks`）→ `_wait_for_mineru_result`（轮询 `GET /tasks/{id}` 直到 `SUCCESS_STATES`）→ `GET /tasks/{id}/result` → `_extract_markdown` → `target.write_text(markdown)`（默认 `backend/data/document_outputs/{stem}.md`，或 `output_path` 指定的 `/artifacts/...` 路径）→ 返回 `{task_id, source, output_path, markdown_bytes}` JSON。
