# ARCHITECTURE

> 事实来源：当前 `backend/` 源码（run-first runtime）。
> 本轮刷新（2026-07-11）已逐文件核对工作树：`api.py`、`harness.py`、`resources.py`、`hands.py`、`run_ledger.py`、`workflow_artifacts.py`、`tools.py`、`subagents.py`、`artifact_names.py`，以及 `skills/`、`tests/`、`data/` 目录组织。所有结论以源码为准。

## 1. 架构定位

`backend/` 是一个 **Harness 级 agent runtime 底座**，定位是「能力可插拔的运行时壳」，而非某个具体 runner / 容器 / 模型 / 工作流的实现：

- **Brain 可插拔**：`Brain` 是 `Protocol`（`harness.py`），任何实现 `stream(payload, config, **kwargs)` 的对象都可作 Brain；默认实现 `DeepAgentsBrainFactory` 用 `deepagents.create_deep_agent` + MiniMax（伪装成 Anthropic 客户端）模型。
- **执行器（Hands）可插拔**：`Hands` 是 `Protocol`（`hands.py`），`Hands.middleware()` 返回一组 `AgentMiddleware`；默认 `ToolStatusHands` 只挂 `ToolStatusMiddleware`（发 `tool_status` custom event）。
- **工具可插拔**：`ToolCatalog` 是一组 `ToolHandler`（普通 `Callable[..., Any]`），不是 `Protocol`；`default_tool_catalog()`（`tools.py`）含 `parse_documents`、`extract_archives` 两个通用工具，以及 Philips/Tecan 各四个业务工具，共 10 个。
- **业务能力按需加载**：`DeepAgentsBrainFactory.create(...)` 从只读虚拟 `/skills/` 挂载 `philips-wgq-import` 与 `tecan-import` 两个业务 Skill，并通过 `subagents.workflow_subagents()` 注册四个声明式无状态 extractor；A/B 抽取由 Skill 编排 subagent，确定性投票、canonical 与 Excel 生成留在业务模块。
- 模型 / 后端存储 / 持久化通道全部收口在 `AgentResources`（`resources.py`），由调用方注入。

> 运行时不绑定特定 runner、特定容器、特定模型、特定工作流。

`typing.Protocol` 只用于三个可注入运行时边界：`Brain` / `BrainFactory`（`harness.py`）与 `Hands`（`hands.py`）。其余抽象（`AgentResources`、`ToolCatalog`/`ToolHandler`、`SqliteRunLedger`、`RunEvent`/`RunSnapshot`）都是具体类与 dataclass。

## 2. run-first 架构核心

run 是唯一的执行单位与查询单位。本次架构基线（commit `8890292 refactor: 迁移到 run-first 架构，移除 session 相关代码`）已完成：

- 删除 `session.py`（旧 session 模块）、`tests/test_stream_typing.py`、`hands.py` 中 trace 相关代码、旧 `self_check.py`。
- 重命名为 `run_ledger.py`，所有 run 元数据 / 事件落库走它。
- API 层改为以 run 为中心的新接口。

### 两个等价入口

1. **HTTP 入口**（`api.py`）：`POST /runs` 创建 run 并立即返回 `queued`，run 在后台 daemon 线程执行；`GET /runs/{run_id}` 轮询增量事件（非 SSE，纯轮询）。
2. **程序内入口**：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(messages, session_id, run_id)`，返回 `Iterator[RunEvent]`。本地测试脚本与 `FakeBrain` 测试也走这条路。

### `session_id` 的现状（已收窄，易误读）

`session.py` 模块与 session 持久化层**已移除**，但 `session_id` 这个**标识符仍保留**，用途已收窄为两处进程内键：

- LangGraph checkpointer 的 `thread_id`（短期上下文键）——`config={"configurable":{"thread_id": session_id}}`。
- 进程内「同 session 串行」并发保护键——`app.state.session_locks[session_id]` + `app.state.active_runs[session_id]`。

> 已确认：本地 SQLite 不再有 session 表；`session_id` 不再有事件流、不再落库为 session 事实源。session 不再是一等持久化对象，run 才是。

## 3. 分层与数据流

```text
HTTP 层 (api.py)
  POST /upload(files[])
    -> 保存到 /artifacts/uploads/<cleaned-stem>_<upload-ts>(_n).ext
  POST /runs(messages, session_id?)
    -> create_run(run_id, session_id, input_messages_json)   # run_ledger
    -> threading.Thread(target=_run_background, daemon=True)
       -> HarnessRuntime.execute_run(messages, session_id, run_id)
  GET  /runs/{run_id}?after_event_id=N
    -> run, events[], latest_content_event, usage            # 纯轮询，非 SSE

Harness 层 (harness.py execute_run)
  -> emit status=running
  -> text block 原样保留；artifact block 归一化为
     "Uploaded artifact: <path>. Use read_file ... or parse_documents ..."
  -> brain_factory.create(resources, middleware, tools)      # 装配 Brain
  -> brain.stream(
       {"messages": normalized_messages},
       config={"configurable":{"thread_id": session_id}},
       stream_mode=["messages","custom","values"],
       version="v2",
     )
  -> chunk[type=messages]
       先 _model_usage 提取 token（含 subagent），再按 lc_agent_name 丢弃 subagent 文本；
       主 agent 发 thinking / text_delta
  -> chunk[type=custom]   => tool_status
  -> chunk[type=values]   => 从 snapshot 派生 tool_call / tool_result / assistant_message
       （assistant_message 保留同条 AIMessage 最后一个 thinking 文本；
        末位 assistant 文本仍作 reply 候选）
  -> 结束 => status=succeeded(reply=assistant_text 或拼接 text_parts)
       异常 => status=failed(error=...)，raw={"status":"failed","error":repr(exc)}

能力层
  Brain / Hands（Protocol）+ Tools（callable catalog）+ Subagents（声明式临时 extractor）

持久化层
  run_ledger.py (SqliteRunLedger, data/dsagents_runs.db)
    + LangGraph SqliteSaver (data/dsagents_checkpoints.db, thread_id=session_id)
    + LangGraph SqliteStore  (data/dsagents_store.db, namespace=("dsagents",))
    + CompositeBackend (StateBackend default + StoreBackend/FilesystemBackend 路由)
```

### CompositeBackend 路由（`resources.py`）

`AgentResources.__enter__` 装配 `CompositeBackend`：

- `/memories/` → `StoreBackend`（显式长期记忆，持久化到 `dsagents_store.db`，`namespace=("dsagents",)`）。
- `/artifacts/`、`/large_tool_results/` → `FilesystemBackend`（同一实例，落 `data/artifacts/`，`virtual_mode=True`）。
- `/skills/` → `FilesystemBackend`（只读业务 Skill 源；主 agent 显式 `permissions` 禁止写 `/skills/**`）。
- 其它（含 DeepAgents 内部 `/conversation_history/` 与未使用的 `/logs/`）→ `StateBackend`（同 `thread_id` 图状态，不进跨 session store）。

业务工作流不增加图状态字段、恢复接口或数据库表。A/B extraction、adjudication、canonical 与生成工作簿均以显式 `/artifacts/downloads/...` 路径传递，每次写入唯一新文件；缺信息时当前 run 正常返回问题，下一 run 仍需显式给出路径和选择。

## 4. 事件源模型（run 是事件源）

每个 run 的进展以**事件**形式不可变追加到 `run_events` 表，`event_id` 单调递增。`GET /runs/{run_id}?after_event_id=N` 仅靠事件表增量回放，无需额外会话状态；`latest_content_event` 由 `run_id + type not in ('status','model_usage') + event_id desc limit 1` 取得。

典型成功 run 的事件序列：

```text
status(queued) -> status(running) -> thinking / text_delta / tool_call / tool_status /
                  tool_result / assistant_message / model_usage / ... -> status(succeeded)
```

`status` 事件同时驱动 `runs` 表的 `status`/`reply`/`error`/`updated_at` 列更新（即 run 状态是事件投影）。`emit_run_status` 校验 status 必须在 `RUN_STATUSES = {queued, running, succeeded, failed}` 内。

关键事件类型语义：

- `values` 不再作为公开业务事件类型写入 `run_events.type`；它只保留在 `raw` snapshot 中，业务层从 snapshot 派生 `tool_call`、`tool_result` 和 `assistant_message`。
- `assistant_message`：当最终 AIMessage content 同时含 `thinking` 与 `text` block 时，`payload` 保留最后一个 `thinking` 文本并保留最终 `text`；`tests/test_harness.py` 与 `tests/test_api.py` 覆盖该载荷形状。
- `model_usage`：每次模型调用终态（langchain_anthropic 的 `message_delta`）提取一次，载荷含 `model`、`scope`（`main_agent`/`subagent`）、`agent_name`、`input_tokens`、`output_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens`（后者 = `cache_creation` + `ephemeral_5m_input_tokens` + `ephemeral_1h_input_tokens`）。它是成本/缓存观测事件，不算内容事件，因此被 `latest_content_event` 排除。
- `tool_status`：由 `ToolStatusMiddleware`（`hands.py`）在工具调用前/异常时/成功后经 `get_stream_writer()` 发出的 custom event，载荷 `{name, status: started|error|completed}`。`parse_documents`（`tools.py`）也独立发 `tool_status` 风格 custom event 以报告 MinerU 提交/轮询/下载进度。

## 5. model_usage 与成本估算（commit `7126b83`，已确认）

token 用量事实在 `run_ledger.aggregate_model_usage(run_id)` 聚合：返回 `model_calls`、四个 token 维度总量、`by_agent`（按 `(scope, agent_name)` 分组）与 `calls`（每次调用的 model + token 明细，用于分档计价）。无 `model_usage` 事件时返回 `None`。

API 层 `_usage_summary`（`api.py`）在原始 token 之上叠加两层估算：

- **cache_hit_rate**：`cache_read_input_tokens / input_tokens`（无输入时为 `None`）；`by_agent` 每项也有自己的 `cache_hit_rate`。
- **tier 计价**（仅趋势估算，非实际账单）：按单次调用输入规模判定档位——`<=512k` 用 `standard` 档，`>512k` 用 `long_context` 档；每档三价 `(input_per_m, output_per_m, cache_read_per_m)`（CNY / 百万 token）。`cache_creation` 按普通非缓存读输入计价。
  - `estimated_cost_cny` / `estimated_savings_cny`（savings = cache-read 相对 standard 输入价的折扣）：只要该 run 任意一次调用的 `model` 不在 `_PRICEABLE_MODELS = {"MiniMax-M3"}` 内，两个字段整体置 `null`（避免系统性低估），但 token 计数照常返回。
  - `pricing_as_of = "2026-07-12"`、`estimated_cost_note` 标注「Trend estimate only ... final billing is whatever MiniMax invoices.」

> `after_event_id` 只影响 `events[]`，不影响顶层 `usage`（`usage` 始终从该 run 全部 `model_usage` 事件汇总）。

## 6. 核心运行时原则

- **能力可插拔**：仅运行时注入边界 `Brain` / `BrainFactory` / `Hands` 用 `Protocol`；Tools 用普通 callable + `ToolCatalog`，Resources 用具体类。默认实现从 `create_harness(...)` 进入，运行时本身不写死具体模型实现。
- **run 是事件源**：状态是事件流的投影；查询靠事件表增量回放。
- **保持运行时薄**：`HarnessRuntime.execute_run` 只做 chunk 规范化与事件转发，不做业务逻辑。
- **真实错误透传**：异常 → `status=failed` + `error` 文本 + `raw={"status":"failed","error":repr(exc)}`；不吞错。
- **优先删减范围**：重构删除 `session.py`、`tests/test_stream_typing.py`、`hands.py` trace、`self_check.py` 等即为该原则的体现。

## 7. 关键抽象

| 抽象 | 定义处 | 作用 |
|------|--------|------|
| `AgentResources` | `resources.py` | 资源装配器（context manager）：run ledger + store + checkpointer + CompositeBackend；`ResourceConfig` 给出固定路径 |
| `create_app(*, resource_config, harness_factory)` | `api.py` | FastAPI 工厂：在 lifespan 里装配 `AgentResources`、`fail_incomplete_runs`、harness、单飞锁注册表；模块级 `app = create_app()` |
| `create_harness(resources)` | `harness.py` | 默认 Harness 工厂：`HarnessRuntime(resources, ToolStatusHands(), default_tool_catalog(), DeepAgentsBrainFactory())` |
| `HarnessRuntime.execute_run(messages, session_id, run_id)` | `harness.py` | run 执行核心，产出 `Iterator[RunEvent]` |
| `Brain` / `BrainFactory` | `harness.py` | 模型/Agent 抽象（Protocol） |
| `Hands` / `ToolStatusHands` | `hands.py` | 中间件装配抽象（Protocol）+ 默认实现 |
| `SqliteRunLedger` | `run_ledger.py` | run 元数据 + 事件 + 大 payload 外溢 + `aggregate_model_usage` |
| `RunEvent` / `RunSnapshot` | `run_ledger.py` | 不可变事件 / run 投影 dataclass（frozen） |
| `ToolCatalog` / `ToolHandler` | `tools.py` | 工具集合抽象（dataclass）/ 普通类型别名 `Callable[..., Any]`；`default_tool_catalog()` 默认装配 |
| `workflow_subagents()` | `subagents.py` | 四个无状态 extractor 配置；每个仅暴露对应 extraction 保存工具 + 内置只读文件能力 + `ExtractionReference` structured response |
| Philips 业务模块 | `philips_wgq_import.py` | extraction/canonical/adjudication 严格合同、tracking 历史与 Oracle fallback、三个 Excel 生成 |
| Tecan 业务模块 | `tecan_import.py` | 物流投票、订单/信息表内容识别、重量守恒、一个 Excel 生成 |
| artifact 路径/JSON helper | `workflow_artifacts.py` | `/artifacts/` 安全解析、唯一下载名、不可覆盖 JSON 读写；不含业务分支 |
| 命名 helper | `artifact_names.py` | 文件名清洗、`<stem>_<timestamp>(_n).ext` 生成、上传后缀识别 |
| `FakeBrain` / `FakeBrainFactory` | `tests/test_support.py` | 本地测试用的 Brain 替身（流式产出固定 chunk，含主/subagent `model_usage`） |

## 8. 存储边界

`backend/data/` 固定三条**逻辑持久化通道**（路径由 `ResourceConfig` 决定，与 CWD 无关；文件按需创建）：

| 文件 | 通道 | 写入方 |
|------|------|--------|
| `dsagents_runs.db` | run ledger | `SqliteRunLedger` |
| `dsagents_checkpoints.db` | LangGraph checkpointer | `SqliteSaver`（`thread_id=session_id`） |
| `dsagents_store.db` | LangGraph store | `SqliteStore`（`namespace=("dsagents",)`） |

三者由 `AgentResources.__enter__` 按需创建，互不共享连接（每次 `SqliteRunLedger` 方法都新开 `sqlite3.connect`）。用户可见文件只落在 `data/artifacts/uploads/`（`POST /upload`）与 `data/artifacts/downloads/`（MinerU JSON/ZIP、解压目录，以及唯一命名的 extraction、adjudication、canonical JSON 与业务 Excel）。内部大 payload spill 独立落在 `data/internal/run-events/*.json`。

`dsagents_runs.db` 表结构（`run_ledger.py _setup`）：

- `runs(run_id, session_id, input_messages_json, status, created_at, updated_at, reply, error)` + `idx_runs_session_created(session_id, created_at desc)`。
- `run_events(event_id integer primary key autoincrement, run_id, type, created_at, payload_json, payload_artifact_path, raw_json, raw_artifact_path)` + `idx_run_events_run_order(run_id, event_id)`。
- run ledger 时间字段统一写成本机时区秒级文本 `TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"`（`_now_text` 用 `datetime.now().astimezone().strftime(...)`）。

### 时间戳迁移机制（commit `c8cc563 refactor(run-ledger): 统一时区格式并添加数据迁移逻辑`，已确认）

`SqliteRunLedger._setup` 末尾调用 `_migrate(conn)`：

- 读 `pragma user_version`；当前 `< 1` 时执行迁移，迁移后写 `pragma user_version = RUN_LEDGER_SCHEMA_VERSION`（`RUN_LEDGER_SCHEMA_VERSION = 1`）。
- 迁移体调用 `_normalize_existing_timestamps(conn, assume_naive_utc=True)`，遍历 `runs.created_at/updated_at` 与 `run_events.created_at`，逐行用 `_normalize_timestamp_text` 重写。
- `_normalize_timestamp_text`：先用 `datetime.fromisoformat(value.replace("Z","+00:00"))` 解析；解析失败原样返回；无时区信息时按 `assume_naive_utc=True` 视作 UTC（`replace(tzinfo=timezone.utc)`），再 `astimezone()` 转本机时区并 `strftime(TIMESTAMP_FORMAT)`。
- 迁移幂等：对本机时区文本再次解析会带上本地 tz，再 `astimezone()` 回到同一字符串（`tests/test_run_ledger.py` 的 `normalized_again` 断言验证）。

大 JSON 外溢到 `data/internal/run-events/*.json`：阈值 `max_inline_bytes=262_144`，仅真正发生 spill 时由 `_store_blob` 创建目录，外溢文件名 `{uuid}.json`，内联列改写为 `{"artifact_path","bytes"}` 引用。

## 9. 运行约束（已确认）

- `POST /runs` 立即返回 `{"run_id","session_id","status":"queued"}`；`session_id` 缺省时服务端 `uuid.uuid4().hex` 生成，`run_id` 始终服务端生成。
- 同一 `session_id` 同时只允许一个活跃 run，靠进程内 `threading.Lock`（`session_locks`）+ `active_runs` 字典保护，统一经 `registry_lock` 串行注册；冲突返回 `409 {"error":"该会话正在运行","active_run_id":...}`。
- 启动时 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` 把遗留 `queued`/`running` run 标记为 `failed("执行已中断，请重试")`。
- `GET /runs/{run_id}` 支持 `after_event_id` 增量；`after_event_id` 只影响 `events[]`，不影响 `latest_content_event`，也不影响顶层 `usage`；未知 run 返回 `404 {"error":"Unknown run: ..."}`。
- `POST /upload` 返回 `{"files":[...]}`；每项含 `/artifacts/uploads/<原名>_<上传时间戳>(_n).ext` 路径、清洗后的原名、mime、size；同一请求共用一个上传时间戳，只有真实物理重名时才追加 `_2`、`_3`。`parse_documents` 对单文件会复用源文件 stem 命名 JSON/ZIP（`<stem>.json` / `<stem>.zip`），便于上传/下载路径一一对应。

## 10. 配置加载

`.env` 由两个模块在**导入时**加载（`load_dotenv(Path(__file__).with_name(".env"))`）：

- `harness.py`（MiniMax 模型相关：`MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL`）。
- `tools.py`（MinerU 相关：`MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_EFFORT`〔可留空〕/ `MINERU_TIMEOUT_SECONDS`）。

Philips generator 另从已加载环境读取可选的 `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS`。Oracle 配置缺失、无记录或查询失败只追加人工校验并继续生成。

## 11. provider 注入点

`DeepAgentsBrainFactory.__init__` 的 `model` 参数是唯一模型注入点：

- 默认（`model=None`）走 `init_chat_model(f"anthropic:{os.getenv('MINIMAX_MODEL')}", api_key=..., base_url=..., thinking={"type":"adaptive"})`——把 MiniMax 模型伪装成 Anthropic 客户端。
- 模块级常量 `MAIN_AGENT_NAME = "dsagents-main"`、`MAIN_AGENT_MODEL = "MiniMax-M3"`：前者是主 agent 名（`create_deep_agent(name=...)`），后者是写入每个 `model_usage` 事件的固定 model 名。
- `register_harness_profile("anthropic", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))` 关闭 deepagents 0.6.12 自动添加的第五个通用 subagent，只保留 `workflow_subagents()` 的四个 extractor。

## 12. 这里没有（已确认的范围边界）

- 没有 session 模块 / session 表 / session 事件回放。
- 没有 SSE / `StreamingResponse` / `text/event-stream`；`GET /runs/{run_id}` 是纯轮询。
- 没有 `context_window` 概念（短期上下文全交给 checkpointer + thread_id）。
- 没有 `RemoveMessage(REMOVE_ALL_MESSAGES)`、`run_turn` / `stream_turn`。
- 没有 model/tool trace 落库；唯一例外是 `model_usage` 事件，只记录每次模型调用的 token 计数与 cache 细节用于成本/缓存观测，不含请求/响应正文，也不进 AgentState/checkpointer/store。
- 没有业务工作流接口、业务状态表、恢复游标、动态路由 middleware 或通用 A/B 引擎。
- 没有真正的 one-shot 单函数入口；程序内调用需显式组合 `AgentResources` + `create_harness(...)` + `execute_run(...)`。
