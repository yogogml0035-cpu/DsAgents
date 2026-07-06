# ARCHITECTURE

> 事实来源：当前 `backend/` 源码（run-first runtime）
> 最新变更：commit `8890292 refactor: 迁移到 run-first 架构，移除 session 相关代码`

## 1. 架构定位

`backend/` 是一个 **Harness 级 agent runtime 底座**。它的核心定位是“能力可插拔的运行时壳”，而不是某个具体 runner / 容器 / 模型 / 工作流的实现：

- **Brain 可插拔**：`Brain` 是 `Protocol`（`harness.py`），任何实现了 `stream(payload, config, **kwargs)` 的对象都可作 Brain；默认实现 `DeepAgentsBrainFactory` 用 `deepagents.create_deep_agent` + MiniMax（伪装成 Anthropic）模型。
- **执行器（Hands）可插拔**：`Hands` 是 `Protocol`，`Hands.middleware()` 返回一组 `AgentMiddleware`；默认 `ToolStatusHands` 只挂 `ToolStatusMiddleware`（发 `tool_status` custom event）。
- **工具可插拔**：`ToolCatalog` 是一组 `ToolHandler`（普通 callable），不是 `Protocol`；`default_tool_catalog()` 当前只含 `parse_document`。
- 模型 / 后端存储 / 持久化通道都被收口在 `AgentResources` 中，由调用方注入。

> 运行时不绑定特定 runner、特定容器、特定模型、特定工作流。

## 2. run-first 架构核心

run 是唯一的执行单位与查询单位。本次重构（`8890292`）已完成：

- 删除 `session.py`（旧 session 模块）。
- 重命名为 `run_ledger.py`，所有 run 元数据 / 事件落库走它。
- 移除旧的 session 端点、`tests/test_stream_typing.py`、`hands.py` 中 trace 相关代码。
- API 层改为以 run 为中心的新接口。

### 两个等价入口

1. **HTTP 入口**（`api.py`）：`POST /runs` 创建 run 并立即返回 `queued`，run 在后台线程执行。
2. **程序内入口**：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(message, session_id, run_id)`，返回 `Iterator[RunEvent]`。`self_check.py` 走这条路。

### `session_id` 的现状（需澄清，易误读）

`session.py` 模块与 session 持久化层**已移除**，但 `session_id` 这个**标识符仍保留**，用途已收窄为：

- LangGraph checkpointer 的 `thread_id`（短期上下文键）——`config={"configurable":{"thread_id": session_id}}`。
- 进程内“同 session 串行”并发保护键——`app.state.session_locks[session_id]` + `app.state.active_runs[session_id]`。

> 已确认事实：本地 SQLite 不再有 session 表；`session_id` 不再有事件流、不再落库为 session 事实源。
> 结论：session 不再是一等持久化对象，run 才是。

## 3. 分层与数据流

```text
HTTP 层 (api.py)
  POST /runs(message, session_id?)
    -> create_run(run_id, session_id, input_message)        # run_ledger
    -> threading.Thread -> _run_background
       -> HarnessRuntime.execute_run(message, session_id, run_id)
  GET  /runs/{run_id}?after_event_id=N                      # 增量拉 events[]，同时返回 latest_content_event
  POST /files                                                # 上传 -> /artifacts/uploads/

Harness 层 (harness.py execute_run)
  -> emit status=running
  -> brain_factory.create(resources, middleware, tools)     # 装配 Brain
  -> brain.stream(
       {"messages":[{"role":"user","content":message}]},
       config={"configurable":{"thread_id": session_id}},
       stream_mode=["messages","custom","values"],
       version="v2",
     )
  -> chunk[type=messages] => thinking / text_delta
  -> chunk[type=custom]   => tool_status
  -> chunk[type=values]   => values（取末位 assistant 文本作 reply）
  -> 结束 => status=succeeded(reply=...)  /  异常 => status=failed(error=...)

能力层
  Brain / Hands（Protocol）+ Tools（callable catalog）

持久化层
  run_ledger.py (SqliteRunLedger, dsagents_runs.db)
    + LangGraph SqliteSaver (dsagents_checkpoints.db, thread_id=session_id)
    + LangGraph SqliteStore  (dsagents_store.db, namespace=("dsagents",))
    + CompositeBackend (StateBackend default + StoreBackend/FilesystemBackend 路由)
```

装配关系（`resources.py`）：`CompositeBackend` 路由：

- `/memories/` → `StoreBackend`（显式长期记忆，持久化到 store）
- `/artifacts/`、`/large_tool_results/` → `FilesystemBackend`（落 `data/artifacts/`，virtual_mode=True）
- 其它（含 DeepAgents 内部 `/conversation_history/` 与未使用的 `/logs/`）→ `StateBackend`（同 `thread_id` 图状态，不进跨 session store）

## 4. 事件源模型（run 是事件源）

每个 run 的进展以**事件**形式不可变追加到 `run_events` 表，并由 `event_id` 单调递增。`GET /runs/{run_id}?after_event_id=N` 仅靠事件表增量回放，无需额外会话状态；同时可按 `run_id + type != 'status' + event_id desc limit 1` 取 `latest_content_event`。

事件类型序列（典型成功 run）：

```text
status(queued) -> status(running) -> values/thinking/text_delta/tool_status/... -> status(succeeded)
```

`status` 事件同时驱动 `runs` 表的 `status`/`reply`/`error`/`updated_at` 列更新（即 run 状态是事件投影）。`emit_run_status` 校验 status 必须在 `RUN_STATUSES = {queued, running, succeeded, failed}` 内。

## 5. 核心运行时原则

- **能力可插拔**：仅运行时注入边界 `Brain` / `BrainFactory` / `Hands` 使用 `Protocol`；Tools 用普通 callable + `ToolCatalog`，Resources 用具体类。默认实现从 `create_harness(...)` 进入，运行时本身不写死具体模型实现。
- **run 是事件源**：状态是事件流的投影；查询靠事件表增量。
- **保持运行时薄**：`HarnessRuntime.execute_run` 只做 chunk 规范化与事件转发，不做业务逻辑。
- **真实错误透传**：异常 → `status=failed` + `error` 文本 + `raw={"status":"failed","error":repr(exc)}`；不吞错。
- **优先删减范围**：本次重构删除 `session.py`、`tests/test_stream_typing.py`、`hands.py` trace 等即为该原则的体现。

## 6. 关键抽象

| 抽象 | 定义处 | 作用 |
|------|--------|------|
| `AgentResources` | `resources.py` | 资源装配器（context manager）：run ledger + store + checkpointer + CompositeBackend；`ResourceConfig` 给出固定路径 |
| `create_harness(resources)` | `harness.py` | 默认 Harness 工厂：`HarnessRuntime(resources, ToolStatusHands(), default_tool_catalog(), DeepAgentsBrainFactory())` |
| `HarnessRuntime.execute_run(message, session_id, run_id)` | `harness.py` | run 执行核心，产出 `Iterator[RunEvent]` |
| `Brain` / `BrainFactory` | `harness.py` | 模型/Agent 抽象（Protocol） |
| `Hands` / `ToolStatusHands` | `hands.py` | 中间件装配抽象（Protocol）+ 默认实现 |
| `SqliteRunLedger` | `run_ledger.py` | run 元数据 + 事件 + 大 payload 外溢 |
| `ToolCatalog` / `ToolHandler` | `tools.py` | 工具集合抽象；`default_tool_catalog()` 默认装配 |
| `_FakeBrain` / `_FakeBrainFactory` | `self_check.py` | 自检用的 Brain 替身（流式产出固定 chunk） |

## 7. 存储边界

`backend/data/` 固定三条**活跃**持久化通道（路径由 `ResourceConfig` 决定，与 CWD 无关）：

| 文件 | 通道 | 写入方 |
|------|------|--------|
| `dsagents_runs.db` | run ledger | `SqliteRunLedger` |
| `dsagents_checkpoints.db` | LangGraph checkpointer | `SqliteSaver`（`thread_id=session_id`） |
| `dsagents_store.db` | LangGraph store | `SqliteStore`（`namespace=("dsagents",)`） |

`dsagents_runs.db` 表结构：

- `runs(run_id, session_id, input_message, status, created_at, updated_at, reply, error)` + `idx_runs_session_created(session_id, created_at desc)`
- `run_events(event_id, run_id, type, created_at, payload_json, payload_artifact_path, raw_json, raw_artifact_path)` + `idx_run_events_run_order(run_id, event_id)`

大 JSON 外溢到 `backend/data/artifacts/run-events/*.json`（阈值 `max_inline_bytes=262_144`，可配置）。

> 需确认（遗留物）：`backend/data/dsagents_sessions.db` 与 `backend/data/artifacts/session-events/` 在当前代码中**无任何引用**（旧 session 时代产物）。属孤儿文件，建议清理前确认无外部依赖。

## 8. 运行约束（已确认）

- `POST /runs` 立即返回 `{"run_id","session_id","status":"queued"}`。
- 同一 `session_id` 同时只允许一个活跃 run，靠进程内 `threading.Lock`（`session_locks`）+ `active_runs` 字典保护；冲突返回 `409 该会话正在运行`。
- 启动时 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` 把遗留 `queued`/`running` run 标记为 `failed("执行已中断，请重试")`。
- `GET /runs/{run_id}` 支持 `after_event_id` 增量；`after_event_id` 只影响 `events[]`，不影响 `latest_content_event`；未知 run 返回 `404`。
- `POST /files` 返回虚拟路径 `/artifacts/uploads/<uuid>_<原名>`，落地到 `data/artifacts/uploads/`。

## 9. 配置加载

`.env` 由两个模块在**导入时**加载（`load_dotenv(Path(__file__).with_name(".env"))`）：

- `harness.py`（MiniMax 模型相关：`MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL`）
- `tools.py`（MinerU 相关：`MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_EFFORT` / `MINERU_TIMEOUT_SECONDS`）

这样 `session.py` 删除后，相关环境变量仍在正常调用路径被读取。

## 10. 这里没有（已确认的范围边界）

- 没有 session 模块 / session 表 / session 事件回放。
- 没有 `context_window` 概念（短期上下文全交给 checkpointer + thread_id）。
- 没有 `RemoveMessage(REMOVE_ALL_MESSAGES)`、`run_turn` / `stream_turn`。
- 没有 model/tool trace 落库。
- 没有真正的 one-shot 单函数入口；程序内调用需显式组合 `AgentResources` + `create_harness(...)` + `execute_run(...)`。
