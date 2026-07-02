# 集成边界 (Integrations)

> 事实来源：backend/ 源码与 backend/pyproject.toml + uv.lock（2026-07-02 生成；本轮据提交 a30bb99 / 9c78cf2 刷新 MiniMax 模型装配事实）

本文描述 `backend/` 与外部系统/服务的集成边界。证据强度以"已确认 / 需确认"区分：**已确认** = 直接见于 `backend/` 源码或 `backend/pyproject.toml`；**需确认** = 仅见于 `.env.example` 或规划文档、源码无直接引用。

## 1. MinerU 异步任务 API（当前文档解析 provider，已确认）

- **边界位置**：`backend/tools.py` 中的公开工具 `parse_document(file_path, output_path=None)`（被 `default_tool_catalog()` 注册）；实际 HTTP 调用留在私有 helper `_submit_mineru_task` / `_wait_for_mineru_result`。
- **服务地址**：`parse_document` 在调用时经 `_required_env` 读取 `MINERU_BASE_URL`；`.env.example` 当前示例值是 `http://10.11.0.110:6006`（仅内网）。`MINERU_BACKEND`、`MINERU_EFFORT`、`MINERU_TIMEOUT_SECONDS`（经 `int(...)` 转换）也在同一路径读取，任一缺失则 `_required_env` 抛 `RuntimeError("Missing required environment variable: <NAME>")`。
- **三步调用流程**（均为同步阻塞的 `requests`）：
  1. **提交任务** `POST /tasks`（`_submit_mineru_task`）：multipart 上传文件 `files=[("files", (source.name, handle, mime))]`，表单字段里的 `backend` / `effort` 来自 `MINERU_BACKEND` / `MINERU_EFFORT`，`return_md=true`、`response_format_zip=false` 仍固定；`timeout=60`。从响应里经 `_find_value` 递归查找键 `task_id / taskId / id` 得到 `task_id`。
  2. **轮询状态** `GET /tasks/{task_id}`（`_wait_for_mineru_result`）：`timeout=30`，读取 `status / state`；命中 `FAILURE_STATES`（`failed/error/cancelled/...`）抛错，命中 `SUCCESS_STATES`（`completed/done/success/...`）进入取结果，否则 `time.sleep(2.0)` 继续轮询，直到 `MINERU_TIMEOUT_SECONDS` 超时抛 `TimeoutError`。
  3. **取结果** `GET /tasks/{task_id}/result`（`_wait_for_mineru_result`）：`timeout=120`；`_extract_markdown` 从结果里经 `_find_value` 递归查找键 `md / markdown / md_content / markdown_content`，写出为本地 UTF-8 Markdown 文件。
- **公开参数**：工具只暴露 `file_path` 与可选 `output_path`；provider 参数全部走 `MINERU_*` 环境变量。
- **产出落点**：默认输出 `backend/data/document_outputs/{stem}.md`（`_default_output_path`，`Path(__file__).resolve().parent/"data"/"document_outputs"`），可经 `output_path` 覆盖。返回值为 JSON 字符串 `{task_id, source, output_path, markdown_bytes}`。
- **认证**：源码未携带任何鉴权头/token；**需确认**该内网端点是否需要鉴权。

## 2. DeepAgents（可插拔 Brain / 子 Harness）（已确认）

- **边界位置**：`backend/harness.py` 的 `DeepAgentsBrainFactory`（实现 `BrainFactory` Protocol）。
- **集成方式**：通过 `from deepagents import create_deep_agent` 创建 Brain，传入 `model`、`tools`、`system_prompt`、`middleware`、`backend`、`checkpointer`、`store`。Brain 暴露 `invoke(payload, config)` 接口（`Brain` Protocol）。
- **调用约定**：`HarnessRuntime.run_turn` 以 `{"messages": _reset_messages(context)}`、`config={"configurable": {"thread_id": session_id}}` 调用 `brain.invoke`。`_reset_messages` 在上下文前插入 `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 实现上下文重置后再回放。
- **后端注入**：Brain 复用 `AgentResources` 提供的 `CompositeBackend` / `checkpointer` / `store`（见下文），DeepAgents 内置虚拟文件系统通过该 `backend` 暴露给模型（`/memories/`、`/artifacts/` 等路径）。
- **可替换性**：`BrainFactory` 是 Protocol，`backend/self_check.py` 用 `FakeBrain`-based `_FakeBrainFactory` 证明 Brain 可被替换（DeepAgents 并非硬绑定）。

## 3. DeepAgents 内置虚拟文件系统 / CompositeBackend（已确认）

- **边界位置**：`backend/resources.py` 的 `AgentResources.__enter__`。
- **路由规则**（`CompositeBackend`，`default=StateBackend()`）：
  - `/memories/`、`/conversation_history/`、`/logs/` → `StoreBackend(store=SqliteStore, namespace=("dsagents",))`（持久）。
  - `/artifacts/`、`/large_tool_results/` → `FilesystemBackend(root_dir=backend/data/artifacts.resolve(), virtual_mode=True)`（落盘）。
  - 其余路径 → `StateBackend()`（图状态/内存，默认）。
- **作用**：模型经 DeepAgents 虚拟文件系统写"记忆/历史/日志"时落到 SQLite Store，写"大产物/大工具结果"时落到本地磁盘，写一般内容时随图状态保存。

## 4. SQLite StoreBackend / Checkpointer（已确认）

- **边界位置**：`backend/resources.py`。三个独立的 SQLite 数据库文件，全部锁定在 `backend/data/` 下（`_BACKEND_DIR = Path(__file__).resolve().parent`，与 CWD 无关）：
  - **会话事件库**：`backend/data/dsagents_sessions.db`，由 `backend/session.py` 的 `SqliteSessionStore` 用标准库 `sqlite3` 自建表 `sessions`、`session_events`（append-only 事件流，`event_id` 自增主键，含索引 `idx_session_events_session_order` on `(session_id, event_id)`）。
  - **Store 库**：`backend/data/dsagents_store.db`，`SqliteStore.from_conn_string(...)` + `.setup()`，供 `StoreBackend` 持久化记忆/历史/日志。
  - **Checkpoint 库**：`backend/data/dsagents_checkpoints.db`，`SqliteSaver.from_conn_string(...)` + `.setup()`，供 LangGraph 线程状态检查点（`thread_id=session_id`）。
- **生命周期**：`AgentResources` 实现 context manager，`__enter__` 创建目录与库表（`mkdir` data_dir+artifacts_dir），`__exit__` 经 `ExitStack` 关闭 store+checkpointer 句柄。
- **无远程 DB**：均为本地文件 SQLite，无连接串/网络；未发现其它数据库客户端依赖。

## 5. 本地文件系统产物目录（已确认）

- **边界位置**：`backend/resources.py`（`ResourceConfig`，frozen dataclass）与 `backend/session.py`、`backend/tools.py`。
- **目录**：根目录 `backend/data/`（与 CWD 无关），其中：
  - `backend/data/artifacts/` —— DeepAgents `FilesystemBackend` 根目录（大产物、大工具结果）。
  - `backend/data/artifacts/session-events/` —— 大事件 payload 溢出 JSON（`SqliteSessionStore`，阈值 `max_inline_bytes=262144` 即 256KiB，超阈值把原始 payload 写成 `{uuid}.json`，DB 仅存 `{artifact_path, bytes}`）。
  - `backend/data/document_outputs/` —— 文档解析结果 Markdown（`backend/tools.py::_default_output_path` 默认输出）。
- **创建时机**：`AgentResources.__enter__` 与 `SqliteSessionStore.__init__` 均 `mkdir(parents=True, exist_ok=True)`，目录不存在时自动建立。

## 6. LLM 提供方（MiniMax Anthropic 兼容）（已确认）

- **边界位置**：`backend/harness.py` 的 `DeepAgentsBrainFactory.__init__`（当传入 `model is None` 时构造模型）。
- **初始化方式**：直接以 `init_chat_model(f"anthropic:{os.getenv('MINIMAX_MODEL')}", api_key=os.getenv("MINIMAX_API_KEY"), base_url=os.getenv("MINIMAX_BASE_URL"))` 构造 LangChain `ChatAnthropic` 模型对象（经 `langchain.chat_models.init_chat_model`，复用 LangChain 的 Anthropic provider 适配，**不是**自行包装 `anthropic` SDK），再把该对象传给 `create_deep_agent(...)`。
- **配置来源**：**仅**读取 `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 三个环境变量，**无默认值、无任何 fallback**：
  - 提交 `9c78cf2`（"fix(backend/harness): remove fallback logic for minimax model config"）已**显式移除**全部回退逻辑。
  - **不存在** `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 回退。
  - **不存在**把 `MINIMAX_API_KEY` 复制到 `OPENAI_API_KEY`、把 `MINIMAX_BASE_URL` 复制到 `OPENAI_API_BASE` 的逻辑（旧文档中的此类"直接赋值"描述已**过时**）。
  - **不存在**硬编码默认模型名 `MiniMax-M3`，也**不存在**默认 base url `https://api.minimaxi.com/anthropic`。env 未设置时 `os.getenv` 返回 `None`，由 provider 决定行为（fail-forward；None 的具体行为 provider 相关）。
- **凭据**：来自 `.env`（`backend/.env.example` 占位 `MINIMAX_API_KEY=`），由 `session.py:15` 的 `load_dotenv` 加载；本文不写入真实密钥。

## 7. 预留 / 规划中集成（需确认）

以下键仅出现在 `backend/.env.example`，`backend/` 自身 `.py` 源码**无任何引用**，视为预留或前端边界：

- **DeepSeek**：`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL=https://api.deepseek.com`、`DEEPSEEK_MODEL=deepseek-v4-flash`。需确认是否作为可切换 LLM 提供方。
- **Oracle**：`ORACLE_DSN`、`ORACLE_USERNAME`、`ORACLE_PASSWORD`、`ORACLE_CLIENT_LIB_DIR`、`ORACLE_TIMEOUT_SECONDS`（仓库已提交 `backend/instantclient/` Oracle Instant Client 二进制，但无任何 Python import）。`backend/pyproject.toml` 的 `[project.dependencies]` 未列出 `oracledb`/`cx_Oracle`，需确认。
- **LangSmith**：`LANGSMITH_TRACING=false`、`LANGSMITH_ENDPOINT`、`LANGSMITH_PROJECT=DsAgents`、`LANGSMITH_API_KEY`，经 LangChain/LangGraph 运行时间接生效。
- **CORS**：`CORS_ORIGINS=http://localhost:8500,http://127.0.0.1:8500` 端口 8500 暗示前端（疑似 Streamlit 风格）；`backend/` 无 FastAPI/uvicorn 等 web 框架，需确认服务层归属。

## 集成调用链

以一次用户输入触发 DeepAgents 解析文档为例的数据流（文字 + 箭头）：

`run_session` (`backend/session.py`) → 构造 `AgentResources` (`backend/resources.py`，初始化 `backend/data/` 下三个 SQLite 库 + `CompositeBackend`) → `create_harness` (`backend/harness.py`，装配 `TraceHands` + `default_tool_catalog` + `DeepAgentsBrainFactory`) → `HarnessRuntime.run_turn(message, session_id)`：

1. `Sessions.ensure_session` → 写 `backend/data/dsagents_sessions.db`；
2. `Sessions.emit_event("user_message")` → 写会话事件库（emit `user_message`）；
3. `Sessions.context_window` → 读取最近 `CONTEXT_MESSAGE_LIMIT=20` 条消息（首条须为 user）；
4. `DeepAgentsBrainFactory.create` → `create_deep_agent(...)`，注入 `middleware=TraceHands.middleware`、`tools=[parse_document]`、`backend=CompositeBackend`、`checkpointer=SqliteSaver`、`store=SqliteStore`；其中模型由 `init_chat_model(f"anthropic:{MINIMAX_MODEL}", api_key=MINIMAX_API_KEY, base_url=MINIMAX_BASE_URL)` 构造的 `ChatAnthropic`（无默认值/无 fallback）；
5. `brain.invoke({"messages": _reset_messages(context)}, config={"configurable": {"thread_id": session_id}})`（`_reset_messages` 前置 `RemoveMessage(REMOVE_ALL_MESSAGES)`）→ DeepAgents 运行图；
6. 模型调用经 `TraceMiddleware.wrap_model_call` 拦截 → emit `model_request/model_response`（出错 emit `model_error` 并上抛）→ 经 LangChain `ChatAnthropic`（`base_url=MINIMAX_BASE_URL`，无默认值）调用 MiniMax Anthropic 兼容端点完成 LLM 推理；
7. 若模型决定解析文档 → `TraceMiddleware.wrap_tool_call` 拦截 → `parse_document` (`backend/tools.py`)：
   `POST ${MINERU_BASE_URL}/tasks`（`backend`/`effort` 来自 `MINERU_*`，缺失则 `_required_env` 抛 `RuntimeError`）→ 轮询 `GET /tasks/{task_id}` → `GET /tasks/{task_id}/result` → 写 `backend/data/document_outputs/{stem}.md` → emit `tool_request/tool_response`（出错 emit `tool_error` 并上抛）；
8. Brain 将大产物经 `CompositeBackend` 路由：`/artifacts/`、`/large_tool_results/` → `backend/data/artifacts/`（`FilesystemBackend`），`/memories/`、`/conversation_history/`、`/logs/` → `SqliteStore`（`backend/data/dsagents_store.db`），线程状态检查点写 `backend/data/dsagents_checkpoints.db`；
9. `run_turn` 取 `result["messages"][-1].content` → emit `assistant_message` → 返回 `HarnessTurn`。
