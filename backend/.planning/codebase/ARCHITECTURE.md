# ARCHITECTURE

> 事实来源：当前 `backend/` 源码（run-first runtime，`backend/` 顶层源码）。
> 本轮刷新（2026-07-13）已逐文件核对工作树：`api.py`、`runtime/{agent,execution,observability,resources,runs,tools}.py`、`integrations/{artifacts,mineru}.py`、两个内置 Skill 包及其 `scripts/`。所有结论以源码为准。

## 1. 架构定位

`backend/` 是一个 **Harness 级 agent runtime 底座**，定位是「能力可插拔的运行时壳」，而非某个具体 runner / 容器 / 模型 / 工作流的实现。整个产品收口在 `backend/` 顶层源码布局（`api.py`、`runtime/`、`integrations/`、`skills/`），子包 `runtime/`（运行时核心）、`integrations/`（外部解析与 artifact 路径）、`skills/`（两个内置 Skill 包）。

- **Brain 可插拔**：`Brain` / `BrainFactory` 是 `Protocol`（`runtime/agent.py`），任何实现 `stream(payload, config, **kwargs)` 的对象都可作 Brain；默认实现 `DeepAgentsBrainFactory` 用 `deepagents.create_deep_agent` + MiniMax（伪装成 Anthropic 客户端）模型。
- **工具静态注册**：`ToolCatalog`（`runtime/tools.py`）是一组普通 `Callable[..., Any]`，不是 `Protocol`；`default_tool_catalog()` 静态注册 6 个工具（2 个 MinerU 通用 + 每个 Skill 2 个业务），主 Agent 装配时直接 import，不自动扫描、无插件平台、无动态加载器。
- **业务能力按 Skill 打包**：两个内置 Skill 包 `skills/philipswgqimport/` 与 `skills/tecanimport/`（目录名同时满足 Agent Skill 命名与 Python 包标识符规则，故无需动态 loader）；每个含 `SKILL.md` + `references/` + `assets/` + `scripts/{tools.py, documents.py}`。`workflow_subagents()`（`runtime/agent.py`）注册 4 个声明式 extractor SubAgent（A/B 各两个），每个 SubAgent 自装自己的 middleware。
- 存储与持久化通道收口在 `AgentResources`（`runtime/resources.py`），由调用方注入；模型由 `BrainFactory` 创建并注入运行时，二者边界不混合。

> 运行时不绑定特定 runner、特定容器、特定模型、特定工作流。

`typing.Protocol` 只用于可注入能力边界 `Brain` / `BrainFactory`（`runtime/agent.py`）。其余抽象（`AgentResources`、`ToolCatalog`、`SqliteRunLedger`、`RunEvent`/`RunSnapshot`）都是具体类与 dataclass。顶层 HTTP 入口 `api.py` 保留；旧辅助模块（`harness.py`/`hands.py`/`resources.py`/`run_ledger.py`/`tools.py`/`subagents.py`/`workflow_artifacts.py`/`artifact_names.py`/`philips_wgq_import.py`/`tecan_import.py`）与旧带连字符 `skills/` 目录均已删除。

## 2. run-first 架构核心

run 是唯一的执行单位与查询单位。短期上下文全交给 LangGraph checkpointer + `thread_id=session_id`，不再有 session 持久化层。

### 两个等价入口

1. **HTTP 入口**（`api.py`）：`POST /runs` 创建 run 并立即返回 `queued`，run 在后台 daemon 线程执行；`GET /runs/{run_id}` 轮询增量事件（非 SSE，纯轮询）；`POST /runs/{run_id}/cancel` 协作式 drain。
2. **程序内入口**：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(messages, session_id, run_id)`，返回 `Iterator[RunEvent]`。本地测试脚本与 `FakeBrain` 测试也走这条路。

### `session_id` 的现状

`session_id` 不是持久化对象，只作两处进程内键：

- LangGraph checkpointer 的 `thread_id`（短期上下文键）——`config={"configurable":{"thread_id": session_id}}`。
- 进程内「同 session 串行」并发保护键——`app.state.session_locks[session_id]` + `app.state.active_runs[session_id]`。

> 本地 SQLite 没有 session 表；`session_id` 不再有事件流、不落库为 session 事实源。run 才是一等查询单位。

## 3. 分层与数据流

```text
HTTP 层 (api.py)
  POST /upload(files[])
    -> 保存到 /artifacts/uploads/<cleaned-stem>_<upload-ts>(_n).ext
  POST /runs(messages, session_id?)
    -> create_run(run_id, session_id, input_messages_json)   # runs.py
    -> threading.Thread(target=_run_background, daemon=True)
       -> HarnessRuntime.execute_run(messages, session_id, run_id)
  GET  /runs/{run_id}?after_event_id=N
    -> run, events[], latest_content_event, usage            # 纯轮询，非 SSE
  POST /runs/{run_id}/cancel
    -> 协作 drain：RunControl；GraphDrained -> cancelled

Harness 层 (runtime/execution.py execute_run)
  -> emit status=running
  -> artifact block 归一化为文本提示 (ARTIFACT_REFERENCE_HINT)
  -> brain_factory.create(resources, middleware, tools)      # 装配 Brain
  -> brain.stream(
       {"messages": normalized_messages},
       config={"configurable":{"thread_id": session_id}},
       stream_mode=["messages","custom","updates"],
       subgraphs=True,
       version="v2",
       control=RunControl(),                                 # 协作 drain 入口
     )
  -> chunk[type=messages]   => model_usage / thinking / text_delta（按 lc_agent_name 丢弃 subagent 文本）
  -> chunk[type=custom]     => tool_progress / tool_execution（ToolTelemetry 自发）
  -> chunk[type=updates]    => _update_events 派生 assistant_message / tool_execution
  -> 结束 => status=succeeded(reply=assistant_text 或拼接 text_parts)
       GraphDrained => status=cancelled
       NoProgressLoop / 其它异常 => status=failed(error=...)

能力层
  Brain / BrainFactory（Protocol）+ Tools（callable ToolCatalog）
  + workflow_subagents()（4 个声明式 SubAgent，各自装 middleware）

持久化层
  runtime/runs.py (SqliteRunLedger, data/dsagents_runs.db)
    + LangGraph SqliteSaver (data/dsagents_checkpoints.db, thread_id=session_id)
    + LangGraph SqliteStore  (data/dsagents_store.db, namespace=("dsagents",))
    + CompositeBackend (/memories/ / /artifacts/ / /large_tool_results/ / /skills/ 路由)
```

### CompositeBackend 路由（`runtime/resources.py`）

`AgentResources.__enter__` 装配 `CompositeBackend`：

- `/memories/` → `StoreBackend`（显式长期记忆，持久化到 `dsagents_store.db`，`namespace=("dsagents",)`）。
- `/artifacts/`、`/large_tool_results/` → `FilesystemBackend`（同一实例，落 `data/artifacts/`，`virtual_mode=True`）。
- `/skills/` → `FilesystemBackend`（只读业务 Skill 源；主 agent 显式 `permissions` 禁止写 `/skills/**`）。
- 其它（含 DeepAgents 内部 `/conversation_history/` 与未使用的 `/logs/`）→ `StateBackend`（同 `thread_id` 图状态，不进跨 session store）。

业务工作流不增加图状态字段、恢复接口或数据库表。A/B extraction、裁决、canonical 与生成工作簿均以显式 `/artifacts/downloads/...` 路径传递，每次写入唯一新文件；缺信息时当前 run 正常返回 `input_problems`，run 结束，不设游标、不暂停/恢复、不跨 run 状态。

## 4. 事件源模型（run 是事件源）

每个 run 的进展以**事件**形式不可变追加到 `run_events` 表，`event_id` 单调递增。`GET /runs/{run_id}?after_event_id=N` 仅靠事件表增量回放，无需额外会话状态；`latest_content_event` 由 `run_id + type not in ('status','model_usage') + event_id desc limit 1` 取得。

事件类型固定 7 种（`runtime/execution.py` 写库）：

```text
status, tool_execution, tool_progress, thinking, text_delta, assistant_message, model_usage
```

> 旧事件 `tool_call` / `tool_status` / `tool_result` 已删除；旧的 values-snapshot 去重 helper 已删除。

典型成功 run 的事件序列：

```text
status(running) -> thinking / text_delta / tool_execution / tool_progress /
                   assistant_message / model_usage / ... -> status(succeeded)
```

`status` 事件同时驱动 `runs` 表的 `status`/`reply`/`error`/`updated_at` 列更新（即 run 状态是事件投影）。`emit_run_status` 校验 status 必须在 `RUN_STATUSES = {queued, running, succeeded, failed, cancelled, cancelling}` 内。

关键事件类型语义：

- `tool_execution`：由 `ToolTelemetry.wrap_tool_call`（`runtime/agent.py`）经 `get_stream_writer()` 发出，载荷 `{name, agent_name, status: started|error|completed, args?, duration_ms?, result?}`，含 scope 路径以重建「主 Agent → SubAgent → Tool」调用链。
- `tool_progress`：MinerU 通用工具（`parse_documents`/`extract_archives`）自发报告提交/轮询/下载进度（custom payload），与 `tool_execution` 是两套独立 custom 事件。
- `assistant_message`：从 `updates` channel 派生，`payload` 由 `observability.assistant_message_payload(message, tool_calls=...)` 构造，保留最终 `text` 与（若有）最后一个 `thinking` 文本。
- `model_usage`：每次模型调用终态提取一次，载荷含 `model`、`scope`（`main_agent`/`subagent`）、`agent_name`、`input_tokens`、`output_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens`。它是成本/缓存观测事件，不算内容事件，因此被 `latest_content_event` 排除。

## 5. run 状态机

```text
queued → running → succeeded | failed
queued → cancelled
running → cancelling → cancelled
```

取消流（`POST /runs/{run_id}/cancel`，`api.py`）：

- 未知 run → `404 {"error":"Unknown run: ..."}`。
- 终态（`succeeded`/`failed`）→ `409 {"error":"Run already terminal: ...","status":...}`。
- 已 `cancelling`/`cancelled` → `200 {"status":...}`。
- 活跃 run（`queued`/`running`）→ 投影 `cancelling` → `harness.request_cancel(run_id)` 经 LangGraph `RunControl` 协作 drain → `GraphDrained` 投影为 `cancelled`；若 run 尚未进入 `execute_run`（`queued` 或未注册 `RunControl`），直接置 `cancelled`，返回 `202 {"status":"cancelling"}`。

取消不回滚已生成文件，不实现多进程强杀。`fail_incomplete_runs` 在 app lifespan 启动时把遗留 `queued`/`running`/`cancelling` run 标记为 `failed`。

## 6. 中间件边界

运行时恰好两个 middleware（`runtime/agent.py` `runtime_middlewares()` 返回新实例列表）：

- `ToolTelemetry`（`wrap_tool_call`）：工具调用前/异常/成功后发 `tool_execution` 三态，含计时与 scope 路径。
- `NoProgressMiddleware`（`before_model`）：自最近一条 `HumanMessage` 之后，若同一 `tool + 归一化 args` 连续出现 `NO_PROGRESS_WINDOW`（=3）次则抛 `NoProgressLoop`，由 `execute_run` 投影为 `failed`。

**关键约束**：主 Agent 与每个 SubAgent 都各自装这两个 middleware——声明式 SubAgent **不继承**主 Agent 的 middleware，故 `workflow_subagents()` 通过 `_extractor(...)` 给每个 SubAgent 显式注入 `runtime_middlewares()`。

明确**不使用**：`ToolCallLimitMiddleware`、`wrap_model_call`、`before_agent`/`after_agent`、自定义 state schema、自定义 stream transformer、v3 stream、sandbox / 脚本执行。

## 7. 核心运行时原则

- **能力可插拔**：仅运行时注入边界 `Brain` / `BrainFactory` 用 `Protocol`；Tools 用普通 callable + `ToolCatalog`，Resources 用具体类。默认实现从 `create_harness(...)` 进入，运行时本身不写死具体模型实现。
- **run 是事件源**：状态是事件流的投影；查询靠事件表增量回放。
- **保持运行时薄**：`HarnessRuntime.execute_run` 只做 chunk 规范化与事件转发，不做业务逻辑；业务规则全部下沉到 Skill 的 `scripts/`。
- **真实错误透传**：异常 → `status=failed` + `error` 文本 + `raw`；`NoProgressLoop` 同样投影为 `failed`。
- **优先删减范围**：旧顶层辅助模块、旧带连字符 skills 目录、旧事件类型、旧 multi-step builder 全部删除，业务 Tool 收敛为每 Skill 2 个。

## 8. 关键抽象

| 抽象 | 定义处 | 作用 |
|------|--------|------|
| `AgentResources` | `runtime/resources.py` | 资源装配器（context manager）：run ledger + store + checkpointer + CompositeBackend；`ResourceConfig` 给出固定路径 |
| `create_app(*, resource_config, harness_factory)` | `api.py` | FastAPI 工厂：lifespan 装配 `AgentResources`、`fail_incomplete_runs`、harness、单飞锁注册表；模块级 `app = create_app()` |
| `create_harness(resources)` | `runtime/execution.py` | 默认 Harness 工厂：`HarnessRuntime(resources, default_tool_catalog(), DeepAgentsBrainFactory())` |
| `HarnessRuntime.execute_run(messages, session_id, run_id)` | `runtime/execution.py` | run 执行核心，产出 `Iterator[RunEvent]`；`request_cancel(run_id)` 触发 `RunControl` drain |
| `Brain` / `BrainFactory` | `runtime/agent.py` | 模型/Agent 抽象（Protocol） |
| `DeepAgentsBrainFactory` | `runtime/agent.py` | 默认实现：`create_deep_agent` 装配模型/Skills/SubAgents/permissions/middleware |
| `workflow_subagents()` | `runtime/agent.py` | 4 个声明式 extractor SubAgent；每个只挂对应 extraction 保存工具 + 只读文件权限 + 自装 middleware |
| `ToolTelemetry` / `NoProgressMiddleware` | `runtime/agent.py` | 两个运行时 middleware（见 §6） |
| `SqliteRunLedger` | `runtime/runs.py` | run 元数据 + 事件 + 大 payload 外溢 + `aggregate_model_usage`；fresh schema，UTC ISO-8601 毫秒时间戳，无迁移 |
| `RunEvent` / `RunSnapshot` | `runtime/runs.py` | 不可变事件 / run 投影 dataclass（frozen） |
| `ToolCatalog` / `default_tool_catalog()` | `runtime/tools.py` | 工具集合 dataclass / 静态注册 6 个工具 |
| `CompositeBackend` 装配 | `runtime/resources.py` | `/memories/` `/artifacts/` `/large_tool_results/` `/skills/` 四路由 |
| artifact 路径/JSON helper | `integrations/artifacts.py` | `/artifacts/` 安全解析、唯一下载名、不可覆盖 JSON 读写、命名清洗 |
| MinerU 通用工具 | `integrations/mineru.py` | `parse_documents` / `extract_archives` + `MINERU_POLL_INTERVAL_SECONDS` |
| Philips Skill 业务 Tool | `skills/philipswgqimport/scripts/tools.py` | `save_philips_wgq_extraction` + `generate_philips_wgq_import`（一站式 canonical + Oracle + 3 Excel） |
| Philips Skill Excel 写入 | `skills/philipswgqimport/scripts/documents.py` | `generate_tracking` / `generate_invoice_packing` / `generate_bonded_checklist` + 共享 openpyxl helper |
| Tecan Skill 业务 Tool | `skills/tecanimport/scripts/tools.py` | `save_tecan_extraction` + `generate_tecan_import`（订单 + 信息表 join） |
| Tecan Skill Excel 写入 | `skills/tecanimport/scripts/documents.py` | `generate_invoice_packing` + `insert_rows` |
| `FakeBrain` / `FakeBrainFactory` | `tests/test_support.py` | 本地测试 Brain 替身（updates + subgraphs + v2 stream） |

## 9. 存储边界

`backend/data/` 固定三条**逻辑持久化通道**（路径由 `ResourceConfig` 决定，与 CWD 无关；文件按需创建）：

| 文件 | 通道 | 写入方 |
|------|------|--------|
| `dsagents_runs.db` | run ledger | `SqliteRunLedger` |
| `dsagents_checkpoints.db` | LangGraph checkpointer | `SqliteSaver`（`thread_id=session_id`） |
| `dsagents_store.db` | LangGraph store | `SqliteStore`（`namespace=("dsagents",)`） |

三者由 `AgentResources.__enter__` 按需创建，互不共享连接（每次 `SqliteRunLedger` 方法都新开 `sqlite3.connect`）。用户可见文件只落在 `data/artifacts/uploads/`（`POST /upload`）与 `data/artifacts/downloads/`（MinerU JSON/ZIP、解压目录，以及唯一命名的业务 JSON/Excel）。内部大 payload spill 独立落在 `data/internal/run-events/`。

`dsagents_runs.db` 表结构（`runtime/runs.py _setup`，fresh schema，无迁移代码）：

- `runs(run_id, session_id, input_messages_json, status, created_at, updated_at, reply, error)`。
- `run_events(event_id integer primary key autoincrement, run_id, type, created_at, payload_json, payload_artifact_path, raw_json, raw_artifact_path)` + `idx_run_events_run_order(run_id, event_id)`。
- 时间字段统一写 UTC ISO-8601 毫秒：`_now_text()` = `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{microsecond//1000:03d}Z"`（如 `2026-07-13T08:18:59.250Z`）。

大 JSON 外溢到 `data/internal/run-events/*.json`：阈值 `max_inline_bytes=262_144`，仅真正发生 spill 时由 `_store_blob` 创建目录，外溢文件名 `{uuid}.json`，内联列改写为 `{"artifact_path","bytes"}` 引用。

## 10. 运行约束

- `POST /runs` 立即返回 `{"run_id","session_id","status":"queued"}`；`session_id` 缺省时服务端 `uuid.uuid4().hex` 生成，`run_id` 始终服务端生成。
- 同一 `session_id` 同时只允许一个活跃 run，靠进程内 `threading.Lock`（`session_locks`）+ `active_runs` 字典保护，统一经 `registry_lock` 串行注册；冲突返回 `409 {"error":"该会话正在运行","active_run_id":...}`。
- 启动时 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` 把遗留 `queued`/`running`/`cancelling` run 标记为 `failed("执行已中断，请重试")`。
- `GET /runs/{run_id}` 支持 `after_event_id` 增量；`after_event_id` 只影响 `events[]`，不影响 `latest_content_event`，也不影响顶层 `usage`；未知 run 返回 `404 {"error":"Unknown run: ..."}`。
- `POST /upload` 返回 `{"files":[...]}`；每项含 `/artifacts/uploads/<原名>_<上传时间戳>(_n).ext` 路径、清洗后的原名、mime、size。

## 11. 配置加载

`backend/.env` 由相关模块在**导入时**加载（`load_dotenv(...)`）：

- `runtime/agent.py`（MiniMax 模型：`MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL`）。
- `integrations/mineru.py`（MinerU：`MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_EFFORT`〔可留空〕/ `MINERU_TIMEOUT_SECONDS`）。

`generate_philips_wgq_import` 另从已加载环境读取可选的 `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS`。Oracle thick mode 缺 `ORACLE_CLIENT_LIB_DIR` 时优雅降级为人工校验（详见 CONCERNS.md §8）。

## 12. 这里没有（范围边界）

- 没有 session 模块 / session 表 / session 事件回放。
- 没有 SSE / `StreamingResponse` / `text/event-stream`；`GET /runs/{run_id}` 是纯轮询。
- 没有 `context_window` 概念（短期上下文全交给 checkpointer + thread_id）。
- 没有 model/tool trace 落库；唯一例外是 `model_usage` 事件，只记录每次模型调用的 token 计数与 cache 细节，不含请求/响应正文，也不进 AgentState/checkpointer/store。
- 没有业务工作流接口、业务状态表、恢复游标、动态路由 middleware 或通用 A/B 引擎；A/B/C 流程与裁决由 `SKILL.md` 指令驱动，无跨 run 状态。
- 没有插件平台 / 动态 Skill 加载器；工具是静态注册的 6 个。
- 没有沙箱 / 脚本执行能力。
- 没有真正的 one-shot 单函数入口；程序内调用需显式组合 `AgentResources` + `create_harness(...)` + `execute_run(...)`。
