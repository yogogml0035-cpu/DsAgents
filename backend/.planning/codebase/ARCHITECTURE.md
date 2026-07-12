# ARCHITECTURE

> 事实来源：当前 `backend/` 源码（run-first runtime）。
> 本轮刷新（2026-07-10）已核对当前工作树：运行时表面和 run-first 投影未变；默认 DeepAgent 新增两个业务 Skill、四个临时 extraction subagent、八个确定性业务工具及不可覆盖的 JSON/Excel artifact。

## 1. 架构定位

`backend/` 是一个 **Harness 级 agent runtime 底座**。它的核心定位是“能力可插拔的运行时壳”，而不是某个具体 runner / 容器 / 模型 / 工作流的实现：

- **Brain 可插拔**：`Brain` 是 `Protocol`（`harness.py`），任何实现了 `stream(payload, config, **kwargs)` 的对象都可作 Brain；默认实现 `DeepAgentsBrainFactory` 用 `deepagents.create_deep_agent` + MiniMax（伪装成 Anthropic）模型。
- **执行器（Hands）可插拔**：`Hands` 是 `Protocol`，`Hands.middleware()` 返回一组 `AgentMiddleware`；默认 `ToolStatusHands` 只挂 `ToolStatusMiddleware`（发 `tool_status` custom event）。
- **工具可插拔**：`ToolCatalog` 是一组 `ToolHandler`（普通 callable），不是 `Protocol`；`default_tool_catalog()` 含 `parse_documents`、`extract_archives`，以及 Philips/Tecan 各四个 save/build/adjudicate/generate 工具。
- **业务能力按需加载**：`DeepAgentsBrainFactory` 从只读虚拟 `/skills/` 挂载 `philips-wgq-import` 与 `tecan-import`，并一次性注册四个声明式临时 extractor；A/B 抽取由 Skill 编排，确定性投票、canonical 与 Excel 生成留在业务模块。
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

### `session_id` 的现状（已收窄，易误读）

`session.py` 模块与 session 持久化层**已移除**，但 `session_id` 这个**标识符仍保留**，用途已收窄为：

- LangGraph checkpointer 的 `thread_id`（短期上下文键）——`config={"configurable":{"thread_id": session_id}}`。
- 进程内“同 session 串行”并发保护键——`app.state.session_locks[session_id]` + `app.state.active_runs[session_id]`。

> 已确认事实：本地 SQLite 不再有 session 表；`session_id` 不再有事件流、不再落库为 session 事实源。
> 结论：session 不再是一等持久化对象，run 才是。

## 3. 分层与数据流

```text
HTTP 层 (api.py)
  POST /upload(files[])
    -> 保存到 /artifacts/uploads/<cleaned-stem>_<upload-ts>(_n).ext
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
  -> chunk[type=messages] => 先在 subagent 过滤之前提取 model_usage（含 subagent 调用），再按 lc_agent_name 丢弃 subagent 模型 token，主 agent 发 thinking / text_delta
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
- `/skills/` → `FilesystemBackend`（只读业务 Skill 源；主 agent 原生权限禁止写 `/skills/**`）
- 其它（含 DeepAgents 内部 `/conversation_history/` 与未使用的 `/logs/`）→ `StateBackend`（同 `thread_id` 图状态，不进跨 session store）

业务工作流不增加图状态字段、恢复接口或数据库表。A/B/C extraction、adjudication、canonical 与生成工作簿均以显式 `/artifacts/downloads/...` 路径传递；每次写入唯一新文件。缺信息时当前 run 正常返回问题，下一 run 仍需显式给出路径和选择。

## 4. 事件源模型（run 是事件源）

每个 run 的进展以**事件**形式不可变追加到 `run_events` 表，并由 `event_id` 单调递增。`GET /runs/{run_id}?after_event_id=N` 仅靠事件表增量回放，无需额外会话状态；同时可按 `run_id + type != 'status' + event_id desc limit 1` 取 `latest_content_event`。

事件类型序列（典型成功 run）：

```text
status(queued) -> status(running) -> thinking/text_delta/tool_call/tool_status/tool_result/assistant_message/model_usage/... -> status(succeeded)
```

`status` 事件同时驱动 `runs` 表的 `status`/`reply`/`error`/`updated_at` 列更新（即 run 状态是事件投影）。`emit_run_status` 校验 status 必须在 `RUN_STATUSES = {queued, running, succeeded, failed}` 内。`latest_content_event` 用 `type not in ('status','model_usage')` 取末位内容事件（`model_usage` 是成本/缓存观测，不算内容事件），成功 run 的最终结果通常会落在 `assistant_message`。

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
| `workflow_subagents()` | `subagents.py` | 四个临时 extractor 配置；每个仅暴露对应 extraction 保存工具和内置只读文件能力 |
| Philips 业务模块 | `philips_wgq_import.py` | extraction/canonical/adjudication 严格合同、tracking 历史与 Oracle fallback、三个 Excel 生成 |
| Tecan 业务模块 | `tecan_import.py` | 物流投票、订单/信息表内容识别、重量守恒、一个 Excel 生成 |
| artifact 路径/JSON helper | `workflow_artifacts.py` | `/artifacts/` 安全解析、唯一下载名、不可覆盖 JSON 读写；不含业务分支 |
| `FakeBrain` / `FakeBrainFactory` | `backend/tests/test_support.py` | 本地测试用的 Brain 替身（流式产出固定 chunk） |

## 7. 存储边界

`backend/data/` 固定三条**逻辑持久化通道**（路径由 `ResourceConfig` 决定，与 CWD 无关；文件会按需创建）：

| 文件 | 通道 | 写入方 |
|------|------|--------|
| `dsagents_runs.db` | run ledger | `SqliteRunLedger` |
| `dsagents_checkpoints.db` | LangGraph checkpointer | `SqliteSaver`（`thread_id=session_id`） |
| `dsagents_store.db` | LangGraph store | `SqliteStore`（`namespace=("dsagents",)`） |

其中 `dsagents_runs.db` 会在首次创建 run 或显式进入 `AgentResources` 时出现；`dsagents_checkpoints.db` / `dsagents_store.db` 同样由资源装配按需创建。用户可见文件只落在 `data/artifacts/uploads/` 与 `data/artifacts/downloads/`：后者除 MinerU JSON/ZIP 和解压目录外，也保存唯一命名的 extraction、adjudication、canonical JSON 与业务 Excel。内部大 payload spill 独立落在 `data/internal/run-events/`。

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

大 JSON 外溢到 `backend/data/internal/run-events/*.json`（阈值 `max_inline_bytes=262_144`，仅真正发生 spill 时创建目录）。

## 8. 运行约束（已确认）

- `POST /runs` 立即返回 `{"run_id","session_id","status":"queued"}`。
- 同一 `session_id` 同时只允许一个活跃 run，靠进程内 `threading.Lock`（`session_locks`）+ `active_runs` 字典保护；冲突返回 `409 该会话正在运行`。
- 启动时 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` 把遗留 `queued`/`running` run 标记为 `failed("执行已中断，请重试")`。
- `GET /runs/{run_id}` 支持 `after_event_id` 增量；`after_event_id` 只影响 `events[]`，不影响 `latest_content_event`，也不影响顶层 `usage`（`usage` 始终从该 run 全部 `model_usage` 事件汇总）；未知 run 返回 `404`。
- `POST /upload` 返回 `{"files":[...]}`；每项含 `/artifacts/uploads/<原名>_<上传时间戳>(_n).ext` 路径、清洗后的原名、mime、size；同一请求共用一个上传时间戳，只有真实物理重名时才追加 `_2`、`_3`。`parse_documents` 对单文件会复用源文件 stem 命名 JSON/ZIP（`<stem>.json` / `<stem>.zip`），便于上传/下载路径一一对应。

## 9. 配置加载

`.env` 由两个模块在**导入时**加载（`load_dotenv(Path(__file__).with_name(".env"))`）：

- `harness.py`（MiniMax 模型相关：`MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL`）
- `tools.py`（MinerU 相关：`MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_EFFORT`〔可留空〕/ `MINERU_TIMEOUT_SECONDS`）

Philips generator 另从已加载环境读取可选的 `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS`。Oracle 配置缺失、无记录或查询失败只追加人工校验并继续生成。

## 10. 这里没有（已确认的范围边界）

- 没有 session 模块 / session 表 / session 事件回放。
- 没有 `context_window` 概念（短期上下文全交给 checkpointer + thread_id）。
- 没有 `RemoveMessage(REMOVE_ALL_MESSAGES)`、`run_turn` / `stream_turn`。
- 没有 model/tool trace 落库；唯一例外是 `model_usage` 事件，只记录每次模型调用的 token 计数与 cache 细节用于成本/缓存观测，不含请求/响应正文，也不进 AgentState/checkpointer/store。
- 没有业务工作流接口、业务状态表、恢复游标、动态路由 middleware 或通用 A/B 引擎。
- 没有真正的 one-shot 单函数入口；程序内调用需显式组合 `AgentResources` + `create_harness(...)` + `execute_run(...)`。
