# backend 编码约定（CONVENTIONS）

本文件记录在 backend 源码中可直接观测到的稳定编码约定。每条约定均可追溯到具体路径与代码行。

## 1. 五大模块边界为稳定接口，实现可插拔

依据 AGENTS.md 第 5-18 行，五个模块边界为稳定契约：

- `Session`：以 append-only 事件形式持久化完整任务事实。实现为 `backend/session.py` 中的 `SqliteSessionStore`，但对外契约是 `SessionStore` Protocol（`backend/session.py:37-46`），定义 `ensure_session` / `get_session` / `get_events` / `emit_event` / `context_window`。
- `Harness`：读取 Session 历史、派生 context window、请求执行、回写事件。`backend/harness.py` 中 `HarnessRuntime.run_turn`（第 84 行）即此流程。
- `Hands`：暴露模型/工具执行 trace 并透传真实错误。`backend/hands.py` 中 `Hands` Protocol（第 14 行）+ `TraceHands` 实现（第 18 行）。
- `Resources`：拥有持久化 store、checkpointer、artifact 路径。`backend/resources.py` 中 `AgentResources`（第 35 行）。
- `Tools`：暴露可调用能力，不绑定具体 runner。`backend/tools.py` 中 `ToolCatalog`（第 18 行）+ `ToolHandler = Callable[..., Any]`（第 15 行）。

关键模式：每个边界用 `typing.Protocol` 定义接口，用具体类实现，两者解耦——例如 `Brain` / `BrainFactory` 是 Protocol（`backend/harness.py:24-36`），`DeepAgentsBrainFactory` 是其实现（第 39 行），`HarnessRuntime` 依赖 `BrainFactory` Protocol 而非具体类（第 77 行）。

## 2. Simplicity Constraint（优先删减作用域）

AGENTS.md 第 21、32、45-46 行：保持 Harness 纤薄，在真实调用方需要之前，不添加 service layer、container、auth、policy framework、workflow engine、宽泛的安全/配置系统。"Prefer deleting scope over adding knobs. Every new abstraction must protect one of the five module boundaries above or be removed." 当前代码确未出现上述任何基础设施层。

## 3. 错误透传，不吞没

`backend/hands.py` 的 `TraceMiddleware.wrap_model_call`（第 32-45 行）与 `wrap_tool_call`（第 47-73 行）在 `try` 中调用 handler，`except Exception as exc` 先 `emit_event` 记录 `model_error` / `tool_error`，随后 `raise` 重新抛出——不捕获、不转换、不吞没。`backend/tools.py` 中 `_submit_task`（`response.raise_for_status()`，第 74 行）、`_wait_for_result`（第 87、94 行）同样直接 `raise_for_status`，失败状态抛 `RuntimeError`，超时抛 `TimeoutError`。

## 4. 中间件/日志：仅记录模型可见内容

AGENTS.md 第 42 行明确规定。`backend/hands.py` 中 `TraceMiddleware` 只 `emit_event` 这些事件类型：`model_request`、`model_response`、`tool_request`、`tool_response`、`model_error`、`tool_error`；`print` 仅输出 `[model] {content}`（第 44 行）与 `[tool] {name} completed`（第 72 行）。绝不打印或持久化隐藏的 chain-of-thought。

## 5. 命名约定

- 模块即边界：`session.py` / `harness.py` / `hands.py` / `resources.py` / `tools.py` 一一对应五大边界。
- snake_case：函数与变量，如 `ensure_session`、`run_turn`、`emit_event`、`context_window`。
- PascalCase 仅用于类与 Protocol：`SessionStore`、`HarnessRuntime`、`ToolCatalog`、`BrainFactory`。
- 私有辅助以下划线前缀：`_safe`、`_utcnow`、`_submit_task`、`_extract_markdown`、`_find_value`、`_assistant_content`、`_reset_messages`。
- 常量大写：`MINERU_BASE_URL`、`SUCCESS_STATES`、`FAILURE_STATES`、`DEFAULT_SYSTEM_PROMPT`、`CONTEXT_MESSAGE_LIMIT`。

## 6. MinerU 固定参数（不可用户配置）

AGENTS.md 第 36-37 行。`backend/tools.py:12` 固定 `MINERU_BASE_URL = "http://10.11.0.110:6006"`；`_submit_task`（第 60-73 行）硬编码 `data={"backend": "hybrid-engine", "effort": "high", "return_md": "true", "response_format_zip": "false"}`，无参数暴露给调用方。

## 7. DeepAgents 默认 StateBackend vs StoreBackend 路由规则

`backend/resources.py:52-63` 的 `CompositeBackend` 配置：
- `default=StateBackend()` —— 文件系统默认。
- `StoreBackend`（持久）路由：`/memories/`、`/conversation_history/`、`/logs/`。
- `FilesystemBackend`（磁盘，`virtual_mode=True`）路由：`/artifacts/`、`/large_tool_results/`。
依据 AGENTS.md 第 38-41 行：默认保持 `StateBackend`，durable history/memory 走 `StoreBackend`（本地 SQLite `.db`），使用内置虚拟文件系统、不另加 wrapper。

## 8. Durable SQLite + 文件系统 artifact 分离

`backend/resources.py:18-32` 中 `ResourceConfig`：`session_db` / `store_db` / `checkpoint_db` 三个 `.db` 文件，加 `artifacts_dir`（`data/artifacts/`）。`backend/session.py:52` 中 `SqliteSessionStore` 默认 `max_inline_bytes=262_144`（256KB）：超过阈值（第 117-124 行）将 payload 落盘到 `artifacts_dir/session-events/{uuid}.json`，DB 仅存 `{"artifact_path", "bytes"}` 引用（`_read_event` 第 161-175 行按需回读）。
