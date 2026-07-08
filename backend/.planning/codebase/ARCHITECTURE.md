# ARCHITECTURE

> 事实来源：当前 `backend/` 源码（run-first runtime）。
> 本轮刷新（2026-07-08）已核对当前 HEAD：`349357b`（`assistant_message` 附带最终 AIMessage 的最后一个 `thinking` 文本）、`2206b1a`（values snapshot 派生 `tool_call` / `tool_result` / `assistant_message`）、`c8cc563`（run-ledger 时区统一与 schema 迁移）、`bc383ac`（测试端口配置）。

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
2. **程序内入口**：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(messages, session_id, run_id)`，返回 `Iterator[RunEvent]`。本地测试脚本中的 harness 测试也走这条路。

### `session_id` 的现状（需澄清，易误读）

`session.py` 模块与 session 持久化层**已移除**，但 `session_id` 这个**标识符仍保留**，用途已收窄为：

- LangGraph checkpointer 的 `thread_id`（短期上下文键）——`config={"configurable":{"thread_id": session_id}}`。
- 进程内“同 session 串行”并发保护键——`app.state.session_locks[session_id]` + `app.state.active_runs[session_id]`。

> 已确认事实：本地 SQLite 不再有 session 表；`session_id` 不再有事件流、不再落库为 session 事实源。
> 结论：session 不再是一等持久化对象，run 才是。

## 3. 分层与数据流

```text
HTTP 层 (api.py)
  POST /upload(files[])
    -> 保存到 /artifacts/uploads/<uuid>_<filename>
  POST /runs(messages, session_id?)
    -> create_run(run_id, session_id, input_messages_json)  # run_ledger
    -> threading.Thread -> _run_background
       -> HarnessRuntime.execute_run(messages, session_id, run_id)
  GET  /runs/{run_id}?after_event_id=N                      # 增量拉 events[]，同时返回 latest_content_event

Harness 层 (harness.py execute_run)
  -> emit status=running
  -> text block 原样保留；artifact block -> "Uploaded artifact: /artifacts/uploads/..."
  -> brain_factory.create(resources, middleware, tools)     # 装配 Brain
  -> brain.stream(
       {"messages": normalized_messages},
       config={"configurable":{"thread_id": session_id}},
       stream_mode=["messages","custom","values"],
       version="v2",
     )
  -> chunk[type=messages] => thinking / text_delta
  -> chunk[type=custom]   => tool_status
  -> chunk[type=values]   => 从 snapshot 派生 tool_call / tool_result / assistant_message（assistant_message 会保留同条 AIMessage 最后一个 thinking 文本；末位 assistant 文本仍作 reply 候选）
  -> 结束 => status=succeeded(reply=assistant_text 或拼接 text_parts)
                                              /  异常 => status=failed(error=...)

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
status(queued) -> status(running) -> thinking/text_delta/tool_call/tool_status/tool_result/assistant_message/... -> status(succeeded)
```

`status` 事件同时驱动 `runs` 表的 `status`/`reply`/`error`/`updated_at` 列更新（即 run 状态是事件投影）。`emit_run_status` 校验 status 必须在 `RUN_STATUSES = {queued, running, succeeded, failed}` 内。`latest_content_event` 继续用 `type != 'status'` 取末位内容事件，成功 run 的最终结果通常会落在 `assistant_message`。

`values` 不再作为公开业务事件写入 `run_events.type`；它只保留在 `raw` snapshot 中，业务层从 snapshot 中派生 `tool_call`、`tool_result` 和 `assistant_message`。当最终 AIMessage content 同时包含 `thinking` 与 `text` block 时，`assistant_message.payload` 保留最后一个 `thinking` 文本并继续保留最终 `text`；`tests/test_harness.py` 与 `tests/test_api.py` 已覆盖该载荷形状。

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
| `create_app(*, resource_config, harness_factory)` | `api.py` | FastAPI 工厂：在 lifespan 里装配 `AgentResources`、`fail_incomplete_runs`、harness、单飞锁注册表；模块级 `app = create_app()` |
| `create_harness(resources)` | `harness.py` | 默认 Harness 工厂：`HarnessRuntime(resources, ToolStatusHands(), default_tool_catalog(), DeepAgentsBrainFactory())` |
| `HarnessRuntime.execute_run(messages, session_id, run_id)` | `harness.py` | run 执行核心，产出 `Iterator[RunEvent]` |
| `Brain` / `BrainFactory` | `harness.py` | 模型/Agent 抽象（Protocol） |
| `Hands` / `ToolStatusHands` | `hands.py` | 中间件装配抽象（Protocol）+ 默认实现 |
| `SqliteRunLedger` | `run_ledger.py` | run 元数据 + 事件 + 大 payload 外溢 |
| `ToolCatalog` / `ToolHandler` | `tools.py` | 工具集合抽象；`default_tool_catalog()` 默认装配 |
| `FakeBrain` / `FakeBrainFactory` | `backend/tests/test_support.py` | 本地测试用的 Brain 替身（流式产出固定 chunk） |

## 7. 存储边界

`backend/data/` 固定三条**逻辑持久化通道**（路径由 `ResourceConfig` 决定，与 CWD 无关；文件会按需创建）：

| 文件 | 通道 | 写入方 |
|------|------|--------|
| `dsagents_runs.db` | run ledger | `SqliteRunLedger` |
| `dsagents_checkpoints.db` | LangGraph checkpointer | `SqliteSaver`（`thread_id=session_id`） |
| `dsagents_store.db` | LangGraph store | `SqliteStore`（`namespace=("dsagents",)`） |

其中 `dsagents_runs.db` 会在首次创建 run 或显式进入 `AgentResources` 时出现；`dsagents_checkpoints.db` / `dsagents_store.db` 同样由资源装配按需创建。

`dsagents_runs.db` 表结构：

- `runs(run_id, session_id, input_messages_json, status, created_at, updated_at, reply, error)` + `idx_runs_session_created(session_id, created_at desc)`
- `run_events(event_id, run_id, type, created_at, payload_json, payload_artifact_path, raw_json, raw_artifact_path)` + `idx_run_events_run_order(run_id, event_id)`
- run ledger 时间字段统一写成本机时区秒级文本 `YYYY-MM-DD HH:mm:ss`；首次迁移会把旧的 UTC ISO 8601/UTC 秒级文本归一化到本机时区，之后靠 `PRAGMA user_version` 避免重复平移。

### 时间戳迁移机制（`c8cc563`，已确认）

`SqliteRunLedger._setup` 末尾调用 `_migrate(conn)`：

- 读 `pragma user_version`；当前 `< 1` 时执行迁移，迁移后写 `pragma user_version = RUN_LEDGER_SCHEMA_VERSION`（`RUN_LEDGER_SCHEMA_VERSION = 1`）。
- 迁移体调用 `_normalize_existing_timestamps(conn, assume_naive_utc=True)`，遍历 `runs.created_at/updated_at` 与 `run_events.created_at`，逐行用 `_normalize_timestamp_text` 重写。
- `_normalize_timestamp_text`：先用 `datetime.fromisoformat(value.replace("Z","+00:00"))` 解析；解析失败原样返回；无时区信息时按 `assume_naive_utc=True` 视作 UTC（`replace(tzinfo=timezone.utc)`），再 `astimezone()` 转本机时区并 `strftime(TIMESTAMP_FORMAT)`。
- 迁移幂等：对本机时区文本再次解析会带上本地 tz，再 `astimezone()` 回到同一字符串（`test_run_ledger.py` 的 `normalized_again` 断言验证）。

大 JSON 外溢到 `backend/data/artifacts/run-events/*.json`（阈值 `max_inline_bytes=262_144`，可配置）。

## 8. 运行约束（已确认）

- `POST /runs` 立即返回 `{"run_id","session_id","status":"queued"}`。
- 同一 `session_id` 同时只允许一个活跃 run，靠进程内 `threading.Lock`（`session_locks`）+ `active_runs` 字典保护；冲突返回 `409 该会话正在运行`。
- 启动时 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` 把遗留 `queued`/`running` run 标记为 `failed("执行已中断，请重试")`。
- `GET /runs/{run_id}` 支持 `after_event_id` 增量；`after_event_id` 只影响 `events[]`，不影响 `latest_content_event`；未知 run 返回 `404`。
- `POST /upload` 返回 `{"files":[...]}`；每项含 `/artifacts/uploads/<uuid>_<原名>` 路径、原名、mime、size。

## 9. 配置加载

`.env` 由两个模块在**导入时**加载（`load_dotenv(Path(__file__).with_name(".env"))`）：

- `harness.py`（MiniMax 模型相关：`MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL`）
- `tools.py`（MinerU 相关：`MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_EFFORT` / `MINERU_TIMEOUT_SECONDS`）

这两个加载点覆盖了全部需要环境变量的调用路径。

## 10. 这里没有（已确认的范围边界）

- 没有 session 模块 / session 表 / session 事件回放。
- 没有 `context_window` 概念（短期上下文全交给 checkpointer + thread_id）。
- 没有 `RemoveMessage(REMOVE_ALL_MESSAGES)`、`run_turn` / `stream_turn`。
- 没有 model/tool trace 落库。
- 没有真正的 one-shot 单函数入口；程序内调用需显式组合 `AgentResources` + `create_harness(...)` + `execute_run(...)`。
