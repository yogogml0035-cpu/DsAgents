---
last_mapped_commit: 28534a9
---

# Architecture

**Analysis Date:** 2026-07-16

> 事实来源：`backend/` 源码（run-first runtime）。本轮已逐文件核对：`api.py`、`runtime/{agent,execution,middleware,observability,resources,runs,tools}.py`、`integrations/{artifacts,mineru}.py`、`skills/{philipswgqinboundrecognition,tecanimport}/` 及其 `scripts/`。结论以源码为准。

## Pattern Overview

`backend/` 是 **Harness 级 agent runtime 底座**：能力可插拔的运行时壳，不是绑定某一 runner / 容器 / 模型 / 工作流的产品实现。发行名仍为 `dsagents`（`pyproject.toml` `name = "dsagents"`），源码顶层为 `api.py`、`runtime/`、`integrations/`、`skills/`。

核心模式：

| 模式 | 说明 |
|------|------|
| **Run-centric** | `run` 是唯一执行与查询单位；`run_events` 为 append-only 事件源，`runs` 为投影快照 |
| **Event-sourced run ledger** | 进展以不可变事件追加；状态由 `status` 事件投影到 `runs` 表 |
| **Harness + Brain 注入** | `HarnessRuntime` 只做 stream→event 规范化；模型/图由 `Brain` / `BrainFactory`（`Protocol`）注入 |
| **静态 Tool 目录** | `ToolCatalog` 持有普通 callable；`default_tool_catalog()` 静态注册 5 个工具，无插件扫描 |
| **Skill 打包业务** | Philips 是固定 workflow + 结构化响应 + 单一主数据工具；Tecan 保留 `SKILL.md` / references / assets / A/B extractor |

`typing.Protocol` **仅**用于可注入边界 `Brain` / `BrainFactory`（`runtime/agent.py`）。`AgentResources`、`ToolCatalog`、`SqliteRunLedger`、`RunEvent` / `RunSnapshot` 均为具体类或 frozen dataclass。

`session_id` **不是**持久化对象，只作两处进程内键：

1. LangGraph checkpointer 的 `thread_id`（`config={"configurable":{"thread_id": session_id}}`）
2. 进程内同 session 单飞锁（`app.state.session_locks` + `app.state.active_runs`）

本地 SQLite 无 session 表；短期上下文全交给 checkpointer，run 才是一等查询单位。

## Layers

```text
┌─────────────────────────────────────────────────────────────┐
│  HTTP 层  api.py                                             │
│  POST /upload · POST /runs · GET /runs/{id} · POST …/cancel  │
│  进程内单飞锁 · 后台 daemon 线程 · usage 计价汇总              │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  Harness 层  runtime/execution.py                            │
│  HarnessRuntime.execute_run · request_cancel(RunControl)     │
│  stream chunk → RunEvent；artifact block 归一化              │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  能力层  runtime/agent.py + runtime/middleware.py + tools.py │
│  Brain/BrainFactory · DeepAgentsBrainFactory                 │
│  middleware / workflow_subagents()                           │
│  ToolCatalog（5 callables）                                  │
└───────────────┬─────────────────────────┬───────────────────┘
                │                         │
┌───────────────▼──────────┐  ┌───────────▼───────────────────┐
│  业务 Skill 层            │  │  集成层 integrations/          │
│  skills/philipswgqinbound-│  │  artifacts.py 路径/JSON helper │
│  recognition/             │  │                                │
│  skills/tecanimport/      │  │  mineru.py 解析/解压工具       │
│  scripts/tools.py         │  └───────────────────────────────┘
└───────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────────┐
│  持久化层  runtime/resources.py + runtime/runs.py            │
│  SqliteRunLedger · SqliteSaver · SqliteStore · CompositeBackend│
│  data/dsagents_{runs,checkpoints,store}.db · artifacts/      │
└─────────────────────────────────────────────────────────────┘
```

| 层 | 职责 | 主要文件 |
|----|------|----------|
| HTTP | 契约校验、run 创建/轮询/取消、上传落盘、usage 汇总 | `api.py` |
| Harness | 装配 Brain、stream 消费、事件写入、协作取消 | `runtime/execution.py` |
| 能力 | 模型工厂、middleware、SubAgent、工具目录 | `runtime/agent.py`、`runtime/middleware.py`、`runtime/tools.py` |
| 可观测提取 | 纯函数：chunk → usage/thinking/text/assistant payload | `runtime/observability.py` |
| 业务 Skill | Philips 识别合同/主数据补齐；Tecan 抽取与 Excel 生成 | `skills/*/` |
| 集成 | `/artifacts/` 安全路径、MinerU HTTP | `integrations/` |
| 持久化 | run ledger、checkpoint、store、虚拟 FS 路由 | `runtime/resources.py`、`runtime/runs.py` |

## Data Flow

### HTTP 与程序内双入口

```text
POST /upload(files[])
  → clean_filename + make_timestamped_name
  → data/artifacts/uploads/<stem>_<ts>(_n).ext
  → {files:[{file_path:"/artifacts/uploads/...", name, mime_type, size}]}

POST /runs({workflow?, messages, session_id?})
  → workflow 仅允许 philips_wgq_inbound_recognition 或省略
  → Philips workflow 拒绝同时带 session_id（服务端生成新 session）
  → 通用路径 session_id 缺省则 uuid4.hex；run_id 始终服务端生成
  → _acquire_session_run（冲突 409）
  → runs.create_run(..., workflow, status=queued)   # 同时写 status 事件
  → daemon Thread → harness.execute_run(..., workflow=workflow)
  → 立即返回 {run_id, session_id, status:"queued"}

GET /runs/{run_id}?after_event_id=N
  → {run, workflow, result, events[], latest_content_event, usage}   # 纯轮询，非 SSE

POST /runs/{run_id}/cancel
  → 投影 cancelling → harness.request_cancel → RunControl drain
  → GraphDrained → cancelled；未注册 control 则直接 cancelled

程序内：
  with AgentResources(config) as resources:
      harness = create_harness(resources)
      for event in harness.execute_run(messages, session_id, run_id, workflow=workflow):
          ...
```

### execute_run 主路径

```text
emit status=running
  → 注册 RunControl
  → brain_factory.create(resources, middleware, tools, workflow)
    → Philips（workflow == WORKFLOW == "philips_wgq_inbound_recognition"）：
        ToolStrategy(PhilipsWgqRecognitionResult) + handle_errors
        缺则补 StructuredOutputCompatibility / StructuredOutputRecovery
        denylist 排除 save_tecan_extraction / generate_tecan_import
        保留 parse_documents + extract_archives + lookup_philips_wgq_master_data
        subagents=[]（无 Tecan SubAgent）
        system_prompt 追加 PHILIPS_WORKFLOW_PROMPT
    → 通用（workflow is None）：
        default tools + workflow_subagents()（tecan-extractor-a/b）
  → brain.stream(
       {"messages": normalized},          # artifact block → 文本提示
       config={"configurable":{"thread_id": session_id}},
       stream_mode=["messages","custom","updates"],
       subgraphs=True, version="v2", control=RunControl
     )
  → messages  : model_usage（含 subagent）/ thinking / text_delta（subagent 文本丢弃）
  → custom    : tool_progress（MinerU name）| tool_execution（ToolTelemetry）
  → updates   : tool_execution + assistant_message + 可选 structured_response
  → Philips：再次 Pydantic 校验 structured_response；缺失/非法则 failed
  → 正常结束  : status=succeeded(reply=文本摘要, result=验证后的业务 JSON 或 null)
  → GraphDrained : status=cancelled
  → NoProgressLoop / 其它异常 : status=failed
  → finally 弹出 run_controls[run_id]
```

### CompositeBackend 路由（`runtime/resources.py`）

| 虚拟前缀 | 后端 | 落盘 |
|----------|------|------|
| `/memories/` | `StoreBackend` | `dsagents_store.db`，`namespace=("dsagents",)`；启动时若缺 `/memories/AGENTS.md` 则写入 ZIP/result 消费基线，已有内容不覆盖 |
| `/artifacts/` | `FilesystemBackend` | `data/artifacts/`（`virtual_mode=True`） |
| `/large_tool_results/` | 同上 disk 实例 | 同上 |
| `/skills/` | `FilesystemBackend` | `backend/skills/`（主 Agent 写权限 deny `/skills/**`） |
| 其它（默认） | `StateBackend` | 同 `thread_id` 图状态，不跨 session |

业务工作流不增加对外业务图状态字段。Philips 从 `updates` 读取 LangChain 已有 `structured_response`，结果投影到 `runs.result_json` 和终态 `status.payload.result`；`result.outcome=input_problems` 仍是 `run.status=succeeded`。`StructuredOutputRecovery` 仅增加内部重试计数 `structured_recovery_attempts`（对 invoke 输入/输出 schema 隐藏）。无暂停/恢复/跨 run 游标。

### 事件源与投影

`run_events` 表 append-only，`event_id` 自增。`runs` 表由 `emit_run_status` 更新 `status` / `reply` / `error` / `result_json` / `updated_at`；`workflow` 在创建 run 时写入。

固定 7 种事件类型（由 `runtime/execution.py` 写出）：

```text
status · tool_execution · tool_progress · thinking · text_delta · assistant_message · model_usage
```

典型成功序列：

```text
status(queued) → status(running) → thinking / text_delta / tool_execution /
tool_progress / assistant_message / model_usage / ... → status(succeeded)
```

`latest_content_event` 排除 `status` 与 `model_usage`。`aggregate_model_usage` 汇总 token；CNY 估算在 `api.py` `_usage_summary` 层叠加（仅 `MiniMax-M3` 可计价）。

## Key Abstractions

| 抽象 | 定义处 | 作用 |
|------|--------|------|
| `Brain` / `BrainFactory` | `runtime/agent.py` | 唯一 `Protocol` 注入边界；`stream` / `create` |
| `DeepAgentsBrainFactory` | `runtime/agent.py` | 默认：MiniMax + Skills；Philips 显式 `if` 选择 ToolStrategy/工具/提示词，Tecan 保留 2 SubAgent |
| `HarnessRuntime` | `runtime/execution.py` | run 执行核心；`execute_run(..., workflow?)` → `Iterator[RunEvent]`；捕获结构化结果；`request_cancel` |
| `create_harness` | `runtime/execution.py` | 默认工厂：`AgentResources` + `default_tool_catalog()` + `DeepAgentsBrainFactory()` |
| `create_app` | `api.py` | FastAPI 工厂；可注入 `resource_config` / `harness_factory` |
| `AgentResources` / `ResourceConfig` | `runtime/resources.py` | context manager：ledger + store + checkpointer + CompositeBackend |
| `SqliteRunLedger` | `runtime/runs.py` | runs / run_events；大 payload 外溢；`aggregate_model_usage`；`fail_incomplete_runs` |
| `RunEvent` / `RunSnapshot` | `runtime/runs.py` | frozen dataclass |
| `ToolCatalog` / `default_tool_catalog` | `runtime/tools.py` | 5 个静态注册 callable |
| `ToolTelemetry` | `runtime/middleware.py` | `wrap_tool_call` → custom `tool_execution` 三态；不自动写手册 |
| `NoProgressMiddleware` | `runtime/middleware.py` | `before_model`：从当前消息状态计算同 tool+args 连续 3 次 → `NoProgressLoop`；不保存实例级调用状态 |
| `StructuredOutputCompatibility` | `runtime/middleware.py` | `wrap_model_call`：仅当 `ToolStrategy` + thinking 时用 `request.override(model=...)` 复制模型并关闭 thinking，不增加 graph state |
| `StructuredOutputRecovery` | `runtime/middleware.py` | `after_model`：从纯文本 JSON 恢复 `structured_response`；失败则有界 `jump_to: "model"`，耗尽或无法继续时 `jump_to: "end"` |
| `MemoryMiddleware`（内置） | DeepAgents + `runtime/middleware.py` | 主 Agent 仅：`sources=["/memories/AGENTS.md"]` + 受限 `RUNTIME_MEMORY_SYSTEM_PROMPT` + `add_cache_control=True`；不走 `memory=` 默认提示 |
| `workflow_subagents()` | `runtime/agent.py` | 2 个声明式 Tecan extractor（`tecan-extractor-a` / `tecan-extractor-b`）；各装 `runtime_middlewares()`（含 Recovery，无 Memory）+ 只读 FS；Philips 不使用 SubAgent |
| `observability.*` | `runtime/observability.py` | 无 I/O 的 chunk 载荷提取；`MAIN_AGENT_NAME = "dsagents-main"` |
| artifact helpers | `integrations/artifacts.py` | 路径解析、唯一下载名、不可覆盖 JSON |
| MinerU tools | `integrations/mineru.py` | `parse_documents` / `extract_archives` + progress custom 事件 |
| Philips schema/tool | `skills/philipswgqinboundrecognition/{schema.py,scripts/tools.py}` | 固定响应合同 + `lookup_philips_wgq_master_data` |
| Tecan tools | `skills/tecanimport/scripts/tools.py` | `save_tecan_extraction` + `generate_tecan_import` |
| `FakeBrain` / `FakeBrainFactory` | `tests/test_support.py` | 本地回归替身 |

### 默认工具目录（5）

| 可调用名 | 模块 |
|----------|------|
| `parse_documents` | `integrations/mineru.py` |
| `extract_archives` | `integrations/mineru.py` |
| `lookup_philips_wgq_master_data` | `skills/philipswgqinboundrecognition/scripts/tools.py` |
| `save_tecan_extraction` | `skills/tecanimport/scripts/tools.py` |
| `generate_tecan_import` | `skills/tecanimport/scripts/tools.py` |

Philips workflow 用 **denylist** 排除帝肯业务工具，**保留**共享 MinerU 工具，使 `/memories/AGENTS.md` 中的 ZIP 指引与模型工具表一致。Philips 业务结果固定为 `success|partial_success|input_problems` + `data` + `problems`，不解析 `reply`。

### Philips 结构化结果合同（`input_problems` 模式）

`PhilipsWgqRecognitionResult`（`skills/philipswgqinboundrecognition/schema.py`）：

| `outcome` | `data` | `problems` | run 终态 |
|-----------|--------|------------|----------|
| `success` | 完整 `RecognitionData`（shipment/header/items） | 可为非空（字段缺口等） | `succeeded` |
| `partial_success` | 完整 `RecognitionData` | **至少一个** | `succeeded` |
| `input_problems` | **必须 `null`** | **至少一个** | `succeeded`（业务问题，非运行失败） |

缺少 `structured_response` 或 Pydantic 校验失败 → harness 投影 `failed`，与 `input_problems` 严格区分。

### 中间件约束

- `runtime/middleware.py` 集中放置运行时 middleware；`runtime/agent.py` 只负责导入、工厂装配与 SubAgent 声明。
- `runtime_middlewares(*, memory_backend=None)` 固定顺序返回（洋葱模型：列表靠前的 `after_model` 后执行）：
  1. `StructuredOutputRecovery()`（列在最前，使 `after_model` 在 after 钩子中最后执行）
  2. `ToolTelemetry()`
  3. `NoProgressMiddleware()`
  4. `StructuredOutputCompatibility()`
  5. 可选：主 Agent 传入 `memory_backend=resources.backend` 时追加内置 `MemoryMiddleware`
- 不使用 `create_deep_agent(memory=...)`，以免默认用户偏好记忆语义与重复加载。
- 声明式 Tecan SubAgent **不继承**主 Agent middleware，故每个 extractor 显式注入 `runtime_middlewares()`（无 handbook）。
- Philips 工厂对调用方已传入的 middleware 做 **缺则补齐、已有则跳过**：缺 `StructuredOutputCompatibility` 时 **append**；缺 `StructuredOutputRecovery` 时 **insert(0)**。
- 操作手册沉淀：基线预置 + 自动注入 system prompt；工具失败后由模型按模板 `edit_file` 追加；无审批/去重/自动拦截写手册。

### StructuredOutputRecovery 有界重试（`after_model` + `jump_to`）

实现位置：`runtime/middleware.py` 中 `StructuredOutputRecovery`。

| 要点 | 行为 |
|------|------|
| 触发 | 最新 AI 消息无 tool_calls，且 state 尚无 `structured_response`（含空文本结束）；或 ToolStrategy 校验失败后最新消息为 ToolMessage 且结构化参数是空 `data` 壳 |
| 成功路径 | 从 fenced/raw 文本解析 JSON → `schema.model_validate` → 写入 `structured_response`，计数归零 |
| 失败重试 | 无合法 JSON、校验失败、空文本、或空 `data: {}` 壳：追加校正 `HumanMessage`，`jump_to: "model"`，`structured_recovery_attempts += 1` |
| 空 data 壳 | `is_empty_recognition_data_shell`：`success`/`partial_success` 且 `data` 为 `{}`、缺嵌套字段或 `items` 空；专用中文 `EMPTY_DATA_SHELL_HINT`；纠错 HumanMessage 附 `PHILIPS_MINIMAL_DATA_SKELETON`（全 null 形状）并优先「完整 JSON 文本 → 同内容 tool args」；**重试耗尽**时写入 schema 合法的 all-null `data` + `partial_success` + runtime problem（非 `data:{}` / 非 `data:null`），避免 `structured_response missing` |
| ToolStrategy | Philips 使用 `handle_errors=philips_structured_output_error_message` |
| 上限 | 默认 `DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES = 2`（总模型轮次约 `1 + max_retries`） |
| 耗尽/放行 | `attempts >= max_retries` 时返回 `{"jump_to": "end"}`，**禁止**只返回 `None` |
| 钩子声明 | `@hook_config(can_jump_to=["model", "end"])` — 必须同时声明 `"end"` |
| 为何必须 end | 仅有 `ToolStrategy`、业务 tool 被收窄时，缺 `structured_response` 且返回 `None` 会走 model↔model 边无限循环 |
| 下游 | harness 见 `structured_response missing` → run `failed`（空壳耗尽走 skeleton 成功路径时除外） |

### run 状态机

```text
queued → running → succeeded | failed
queued → cancelled
running → cancelling → cancelled
```

`RUN_STATUSES = {queued, running, succeeded, failed, cancelled, cancelling}`。启动 lifespan 调用 `fail_incomplete_runs("执行已中断，请重试")` 将遗留 `queued`/`running`/`cancelling` 标为 `failed`。取消不回滚已生成文件，不做多进程强杀。

## Entry Points

### HTTP（`api.py`，`app = create_app()`）

| 方法 | 路径 | 行为 |
|------|------|------|
| `POST` | `/upload` | multipart `files[]` → uploads 落盘 |
| `POST` | `/runs` | 可选固定 `workflow`；创建 run，后台执行，立即 `queued` |
| `GET` | `/runs/{run_id}` | 轮询 run + 顶层 `workflow`/`result` + events + `latest_content_event` + `usage` |
| `POST` | `/runs/{run_id}/cancel` | 协作 drain；`202` cancelling / `200` 已取消中 / `409` 终态 / `404` 未知 |

启动示例：`uv run uvicorn api:app --host 0.0.0.0 --port 8500`（在 `backend/` 下，`uv sync` 后）。

### 程序内

```text
AgentResources(config) → create_harness(resources) → execute_run(messages, session_id, run_id, workflow=None)
```

包稳定导出见 `runtime/__init__.py`：`AgentResources`、`ResourceConfig`、`HarnessRuntime`、`create_harness`、`RunEvent`、`RunSnapshot`、`SqliteRunLedger`。

### 测试

`backend/tests/` 为可执行 assert 脚本（非 pytest 套件），如 `python -m tests.test_api`。真实模型 / MinerU / HTTP 脚本与本地回归分离。有界重试封顶可用 `cd backend && python -m tests.test_harness` 验证。Philips 工具 denylist 可用 `python -m tests.test_workflow_setup` 验证。

## Error Handling

| 场景 | 处理 |
|------|------|
| `execute_run` 未捕获异常 | `emit_run_status(failed, error=str(exc) or class name, raw=repr)` |
| `NoProgressLoop` | 同上投影 `failed`（非取消） |
| `GraphDrained`（协作取消） | `status=cancelled`，`error="run cancelled"` |
| 后台线程异常（`api._run_background`） | `_ensure_failed_run`：仅当非终态时写 `failed` |
| 线程启动失败 | `_ensure_failed_run` + 释放 session 锁，返回当前 run body |
| `create_run` 失败 | 释放 session 锁后 re-raise |
| 同 session 并发 | `409 {"error":"该会话正在运行","active_run_id":...}` |
| 未知 run 查询/取消 | `404` |
| 终态再取消 | `409 Run already terminal` |
| cancel 时无 `RunControl`（仍 queued） | 直接 `cancelled` |
| Philips `input_problems` | 验证后的 `result.outcome=input_problems`；run 仍 `succeeded` |
| Philips 缺少/非法结构化结果 | run `failed`，不从 `reply` 猜 JSON |
| structured recovery 耗尽（非空壳） | middleware `jump_to: "end"` → harness 报 `structured_response missing` → `failed` |
| structured recovery 空壳耗尽 | middleware 写入 all-null skeleton + `jump_to: "end"` → 可 `succeeded` + `partial_success` |
| 大 payload | `SqliteRunLedger._store_blob` 超 `max_inline_bytes=262_144` 外溢到 `data/internal/run-events/{uuid}.json` |
| Oracle 配置缺失/查询失败/未命中 | 主数据工具写入 `problems`，保留 PDF/Tracking 数据并形成 `partial_success` |
| 真实错误 | 透传异常文本；不吞掉 stack 语义（`raw` 保留 `repr`） |

## Cross-Cutting Concerns

### 配置加载

相关模块 import 时 `load_dotenv(backend/.env)`（**不**在本事实文档中记录密钥值）：

- `runtime/agent.py`：`MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL`
- `integrations/mineru.py`：`MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_EFFORT` / `MINERU_TIMEOUT_SECONDS`
- Philips 主数据补齐：可选 `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS`

### 存储边界（`ResourceConfig`，与 CWD 无关）

| 路径 | 通道 |
|------|------|
| `data/dsagents_runs.db` | run ledger |
| `data/dsagents_checkpoints.db` | LangGraph checkpointer（`thread_id=session_id`） |
| `data/dsagents_store.db` | LangGraph store |
| `data/artifacts/uploads/` | `POST /upload` |
| `data/artifacts/downloads/` | MinerU 与 Tecan 业务 JSON/Excel |
| `data/internal/run-events/` | 事件大 payload spill |

`SqliteRunLedger` 每次方法新开 `sqlite3.connect`；fresh schema，无迁移。时间戳 UTC ISO-8601 毫秒（如 `2026-07-13T08:18:59.250Z`）。

### 并发与生命周期

- 同 `session_id` 进程内单飞：`threading.Lock` + `registry_lock`。
- run 在 daemon 线程执行；取消靠 LangGraph `RunControl.request_drain`。
- lifespan 进入时 `fail_incomplete_runs`；退出时 `AgentResources.__exit__` 关闭 store/checkpointer。

### 可观测与成本

- `model_usage` 事件：token + cache 细节；`scope` 为 `main_agent` / `subagent`。
- API 层 `_usage_summary`：cache hit rate、按调用 input 是否 >512k 分档 CNY 估算（仅 `MiniMax-M3`）；不可计价模型则 estimated 金额为 `null`。
- 无 model/tool 请求正文落库；无 SSE。

### 范围边界（明确没有）

- session 模块 / session 表 / session 事件回放
- SSE / `StreamingResponse`
- 插件平台 / 动态 Skill 加载器
- 业务工作流引擎、HITL、跨 run 恢复游标
- 沙箱 / 脚本执行
- 单函数 one-shot 入口（必须组合 Resources + harness + execute_run）

---
*Architecture analysis: 2026-07-16*
