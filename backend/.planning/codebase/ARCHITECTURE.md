# ARCHITECTURE — backend 五大稳定模块边界

> 对应 `AGENTS.md` 的 Core Goal：把 `Session`、`Harness`、`Hands`、`Resources`、`Tools` 稳定为五个模块边界，能力不硬编码进单一 runner / container / model / workflow。原则："Stabilize interfaces, not implementations"、"Keep the Harness thin"。

## 总体数据/控制流（文档解析请求）

入口链 `__main__.py` → `session.main` → `session.run_session`（`backend/__main__.py:1`、`backend/session.py:218`、`backend/session.py:209`）。
`run_session` 在 `AgentResources` 上下文内构造 `HarnessRuntime` 并调用 `run_turn`（`backend/session.py:209-215`）。
`HarnessRuntime.run_turn` 即 read-derive-request-write 循环（`backend/harness.py:84`）：
1. read/ensure：`sessions.ensure_session`（`backend/harness.py:85`）；
2. write 事件：`sessions.emit_event(session_id, "user_message", ...)`（`backend/harness.py:86-90`）；
3. derive：`sessions.context_window(session_id)` 得 `ContextWindow`（`backend/harness.py:91`）；
4. request：`brain_factory.create(...)` 产出 `Brain`，再 `brain.invoke({"messages": ...})`（`backend/harness.py:93-102`）；
5. write 回：`sessions.emit_event(session_id, "assistant_message", ...)`（`backend/harness.py:104-108`）。

`Brain` 来自 `DeepAgentsBrainFactory`，其内部 `create_deep_agent(..., backend=resources.backend, checkpointer=resources.checkpointer, store=resources.store)`（`backend/harness.py:52-60`）。
工具 `parse_document_with_mineru` 经 `default_tool_catalog()` 注入 Brain（`backend/tools.py:133`），内部经 MinerU `POST /tasks` → 轮询 `GET /tasks/{task_id}` → 取 `GET /tasks/{task_id}/result`（`backend/tools.py:60-97`，`MINERU_BASE_URL` 见 `backend/tools.py:12`）。
Hands 通过 middleware 注入 Brain（`backend/harness.py:95`、`backend/hands.py:22`）；Resources 的 `CompositeBackend` 见 `backend/resources.py:54-63`。

## 1. Session（事件源真相）

- 职责：以 append-only events 存储完整持久任务事实。Session **不是** context window（`AGENTS.md` 第 19 行）。
- 公共面：`SessionStore` Protocol（`backend/session.py:37-46`）含 `ensure_session` / `get_session` / `get_events` / `emit_event` / `context_window`；数据类 `SessionRecord`（`:14`）、`SessionEvent`（`:20`，带 `artifact_path`）、`ContextWindow`（`:30`，含 `source_event_ids`）。实现 `SqliteSessionStore`（`:49`）。
- 拥有：表 `sessions`、`session_events` + 索引 `idx_session_events_session_order`（`:177-205`）。
- 稳定方式：`emit_event` 仅追加（`:110`），永不改写历史；超大 payload（`>max_inline_bytes` 默认 262144）落盘到 `artifacts/session-events/*.json`，表中只留 stub（`:117-124`），保证原始事件不被裁剪/摘要取代。`context_window` 是派生视图——取最后 `CONTEXT_MESSAGE_LIMIT=20` 条消息事件且截到首个 user（`:146-159`），不写回真相。

## 2. Harness（读-派生-请求-写 循环，保持 thin）

- 职责：读 Session 历史 → 派生 context window → 请求执行 → 写回结果事件（`AGENTS.md` 第 13-14 行）。
- 公共面：`Brain` Protocol（`backend/harness.py:24`，仅 `invoke`）、`BrainFactory` Protocol（`:28`）、`DeepAgentsBrainFactory`（`:39`）、`HarnessRuntime`（`:70`）、`HarnessTurn`（`:63`）、工厂 `create_mineru_harness`（`:112`）、`create_mineru_agent`（`:121`）。`HarnessRuntime.run_turn`（`:84`）是唯一编排入口。
- 拥有：单次 turn 的 context、result、session_id；持有 `resources/hands/tools/brain_factory` 引用。
- 稳定方式：Harness 不持有业务逻辑——模型由 `Brain` Protocol 抽象、执行由 Tools 抽象、存储由 Resources 抽象。`run_turn` 不 catch 异常（真实错误透传），每次 turn 重置消息用 `_reset_messages`（`:138`，`RemoveMessage(id=REMOVE_ALL_MESSAGES)` 前置）。

## 3. Hands（暴露执行 trace + 透传真实错误）

- 职责：暴露 model/tool 执行 trace 并把真实错误穿过去（`AGENTS.md` 第 15 行）。
- 公共面：`Hands` Protocol（`backend/hands.py:14`，仅 `middleware(session_id)`）；`TraceHands`（`:18`）；`TraceMiddleware(AgentMiddleware)`（`:26`）实现 `wrap_model_call`（`:32`）与 `wrap_tool_call`（`:47`）。
- 拥有：trace 事件的产出（`model_request` / `model_response` / `model_error` / `tool_request` / `tool_response` / `tool_error`），均经 `sessions.emit_event` 写回 Session。
- 稳定方式：异常分支 `emit_event(..., "model_error"/"tool_error", {...})` 后 `raise`（`:40-42`、`:60-66`），绝不吞错误；只 `print` model/tool 可见信息，不持久化隐藏 CoT（契合 `AGENTS.md` 第 42 行）。

## 4. Resources（持久存储 / checkpointers / artifact 路径）

- 职责：拥有 durable stores、checkpointers、artifact 路径（`AGENTS.md` 第 16 行）。
- 公共面：`ResourceConfig`（`backend/resources.py:14`，属性 `session_db`/`store_db`/`checkpoint_db`/`artifacts_dir`）、`AgentResources`（`:35`，上下文管理器）。
- 拥有：`SqliteSessionStore`（`:43`）、`SqliteStore`（`:45`）、`SqliteSaver`（`:47`）、`CompositeBackend`（`:54`）。
- 稳定方式：单一 `__enter__` 装配所有持久后端，`ExitStack` 统一释放（`:38`、`:66-67`）。路由：default `StateBackend`，`/memories/`、`/conversation_history/`、`/logs/` → `StoreBackend`（持久）；`/artifacts/`、`/large_tool_results/` → `FilesystemBackend(virtual_mode=True)`（`:52-63`）。
- **CompositeBackend 概念**：确在代码中（`backend/resources.py:54`，`from deepagents.backends import CompositeBackend`），是第一里程碑要求的"one `CompositeBackend` configuration"（`AGENTS.md` 第 30 行）。

## 5. Tools（可调用能力，不绑定 runner）

- 职责：暴露 callable capabilities，不绑死到某个 runner（`AGENTS.md` 第 17 行）。
- 公共面：`ToolCatalog`（`backend/tools.py:18`，`as_list()`）、`ToolHandler = Callable[..., Any]`（`:15`）、`default_tool_catalog()`（`:133`，返回只含 `parse_document_with_mineru` 的目录）、`parse_document_with_mineru`（`:26`）。
- 拥有：MinerU 调用细节——固定 `backend=hybrid-engine`、`effort=high`（`:66-71`，与 `AGENTS.md` 第 37 行一致）、轮询状态集 `SUCCESS_STATES`/`FAILURE_STATES`（`:13-14`）、默认输出 `data/mineru_outputs/{stem}.md`（`:56-57`）。
- 稳定方式：Tool 仅作为普通 `Callable` 注册（`ToolHandler`），Harness 不关心其实现；新增能力即向 `ToolCatalog.handlers` 添加 callable，无需改 Harness/Session。

## "Stabilize interfaces, not implementations"

每个模块以 `Protocol` 暴露面（`SessionStore`、`Brain`/`BrainFactory`、`Hands`、`ToolHandler`）+ 默认实现（`SqliteSessionStore`、`DeepAgentsBrainFactory`、`TraceHands`、`default_tool_catalog`）。`AgentResources` 与 `HarnessRuntime` 只依赖 Protocol/数据类，不依赖具体后端——故可换实现而不动边界。
