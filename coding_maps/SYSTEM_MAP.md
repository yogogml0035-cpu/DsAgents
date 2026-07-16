# SYSTEM_MAP

> 系统层跨子项目理解手册。本文件只描述系统形态、边界与读图指南；底层实现细节以 [`backend/.planning/codebase/`](../backend/.planning/codebase/) 为事实来源。
> 上游事实：[`ARCHITECTURE.md`](../ARCHITECTURE.md)、[`INTERFACES.md`](../INTERFACES.md)、[`AGENTS.md`](../AGENTS.md)。
> 本轮刷新（2026-07-16）对齐 backend 全部 7 份事实文档（Analysis Date: 2026-07-16）与根级三件套：固定 Philips workflow、`run.result` 结构化通道、独立 `runtime/middleware.py`（含 `StructuredOutputRecovery` 有界重试）、5 静态工具、2 个 Tecan SubAgent；run-first、四 HTTP 端点、7 类事件、无 SSE/session 持久化层边界保持。

## 1. 系统目的和仓库形态

DsAgents 是一个 **agent 运行时底座**：把能力（Brain、工具）做成可插拔，而不绑定具体 runner、容器、模型或工作流。整个产品收口在 `backend/` 顶层源码布局（`api.py`、`runtime/`、`integrations/`、`skills/`；绝对导入 `from runtime import ...`）。

| 维度 | 事实 |
|------|------|
| 形态 | **单子项目**仓库；唯一产品子项目 `backend/`；发行名 `dsagents`；包管理器 `uv`。当前源文档**未确认**任何前端子项目归属本仓库 |
| 架构 | **run-first**：run 是唯一执行与查询单位；`run_events` append-only，`runs` 为事件投影快照；无 session 模块 / session 持久化层 |
| `session_id` | 仅作 LangGraph `thread_id` 与进程内单飞锁键，**不是**一等持久化对象 |
| 能力可插拔 | `Brain` / `BrainFactory` 为 `typing.Protocol`（`runtime/agent.py`）；middleware 集中在 `runtime/middleware.py`；工具为 callable + `ToolCatalog` |
| 默认装配 | `create_harness` → `DeepAgentsBrainFactory` + `default_tool_catalog()`；测试用 `FakeBrainFactory` |
| 工具注册 | `default_tool_catalog()` **静态**注册 5 工具；普通 import，无自动扫描 / 插件平台 |
| 业务 Skill | Philips：`skills/philipswgqinboundrecognition/`（固定 workflow + 结构化合同 + 1 主数据 Tool）；Tecan：`skills/tecanimport/`（2 业务 Tool + Excel） |
| SubAgent | 仅 `tecan-extractor-a` / `tecan-extractor-b`；Philips **无** SubAgent |
| 入口 | HTTP（四端点、立即 `queued`、纯轮询、无 SSE）+ 程序内组合（`AgentResources` + `create_harness(...).execute_run(...)`）；无 one-shot 单函数 API |
| 业务工作流 | 唯一固定 workflow：`philips_wgq_inbound_recognition` → 验证后的 `run.result`；Tecan 由 Skill 驱动，不增业务 HTTP / 状态表 / 跨 run 恢复 |

详细运行时原则见 [`docs/conventions.md`](../docs/conventions.md)（改 backend 前必读）。系统定位总览见根级 [`ARCHITECTURE.md`](../ARCHITECTURE.md)。

## 2. 子项目职责表

| 子项目 | 目录 | 当前职责 | 技术栈要点 | 边界（不做什么） |
|--------|------|----------|------------|------------------|
| backend | `backend/` | 发行名 `dsagents`；源码顶层 `api.py`、`runtime/`、`integrations/`、`skills/`：run-first runtime + Philips/Tecan 两个内置 Skill + 2 个 Tecan SubAgent + runtime middleware + 5 静态工具 | Python `>=3.11,<4.0`；`uv`；FastAPI / uvicorn；DeepAgents / LangGraph；SQLite 三库；MinerU；openpyxl；可选 oracledb | 不提供 session/业务状态表、SSE、鉴权/CORS、通用工作流引擎、跨进程队列/锁、沙箱 / 脚本执行、插件平台、健康检查端点 |

### backend 系统级模块职责（概览）

| 模块 | 系统级职责 |
|------|-----------|
| `api.py` | FastAPI 四端点 + workflow/session 校验 + 同 session 单飞锁 + 启动恢复 + usage 计价 |
| `runtime/agent.py` | `Brain` / `BrainFactory` Protocol、`DeepAgentsBrainFactory`、Philips ToolStrategy 路由、Tecan SubAgent 声明 |
| `runtime/middleware.py` | `StructuredOutputRecovery`、`ToolTelemetry`、`NoProgressMiddleware`、`StructuredOutputCompatibility`、`runtime_middlewares()` |
| `runtime/execution.py` | `HarnessRuntime.execute_run`（stream → `RunEvent`）、结构化响应捕获/复验、`create_harness`、协作 cancel |
| `runtime/observability.py` | 纯函数：chunk → usage / thinking / text / assistant payload |
| `runtime/resources.py` | `AgentResources` + `ResourceConfig` + `CompositeBackend` |
| `runtime/runs.py` | `SqliteRunLedger`；`workflow` / `result_json` 投影；spill |
| `runtime/tools.py` | `ToolCatalog` + 5 工具静态注册 |
| `integrations/` | artifacts 路径安全；MinerU `parse_documents` / `extract_archives` |
| `skills/philipswgqinboundrecognition/` | 固定响应合同 + Tracking/Oracle 主数据 Tool |
| `skills/tecanimport/` | 抽取保存 + Excel 生成 |

内部分层、目录与配置事实见 [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) 与 [`STRUCTURE.md`](../backend/.planning/codebase/STRUCTURE.md)。

## 3. 跨边界调用链和数据流

当前是**单子项目**。下列描述 backend 内部主调用链与外部 provider 边界（分层细节见 backend ARCHITECTURE §Layers / §Data Flow）。

### 3.1 分层视图

```text
HTTP (api.py)
  → Harness (runtime/execution.py)          # stream → RunEvent；cancel
    → 能力 (runtime/agent.py + middleware.py + tools.py)
      → 业务 Skill (skills/*/)              # Philips schema/lookup；Tecan save/generate
      → 集成 (integrations/)                # artifacts 路径、MinerU
    → 持久化 (runtime/resources.py + runs.py)  # ledger / checkpointer / store / CompositeBackend
```

### 3.2 主调用链（HTTP → harness → brain → tools/skills → ledger/artifacts）

```text
POST /upload  multipart files[]
  └─ 保存到 /artifacts/uploads/<cleaned-stem>_<upload-ts>(_n).ext，返回元数据

POST /runs  {workflow?, messages, session_id?}
  ├─ workflow 仅允许 philips_wgq_inbound_recognition 或省略
  ├─ Philips workflow 禁止非空 session_id；每批服务端生成新 session
  ├─ 通用路径 session_id 为空 → 生成 uuid4().hex；run_id 始终 = uuid4().hex
  ├─ 进程内按 session_id 取 threading.Lock（单飞锁）；冲突 → 409
  ├─ resources.runs.create_run(..., workflow)   # status=queued
  ├─ 起 daemon 线程 → HarnessRuntime.execute_run(..., workflow=workflow)
  └─ 立即返回 {run_id, session_id, status:"queued"}

HarnessRuntime.execute_run(...)   # runtime/execution.py
  ├─ emit status=running；注册 RunControl
  ├─ 归一化 content blocks：
  │    ├─ text     → 原样保留
  │    └─ artifact → "Uploaded artifact: /artifacts/..."  (ARTIFACT_REFERENCE_HINT)
  ├─ brain_factory.create(resources, middleware, tools, workflow)
  │    ├─ 主 Agent middleware：runtime_middlewares(memory_backend=resources.backend)
  │    │    顺序：StructuredOutputRecovery → ToolTelemetry → NoProgressMiddleware
  │    │           → StructuredOutputCompatibility → MemoryMiddleware（仅主 Agent，add_cache_control=True）
  │    ├─ Philips：ToolStrategy(PhilipsWgqRecognitionResult)；
  │    │    排除帝肯工具，保留 parse_documents + extract_archives +
  │    │    lookup_philips_wgq_master_data；subagents=[]；
  │    │    工厂缺则补齐：Recovery insert(0)、Compatibility append
  │    └─ 通用/Tecan：default tools + workflow_subagents()（tecan-extractor-a/b，
  │         各装无 memory 的 runtime_middlewares()，共 4 个 middleware）
  ├─ brain.stream({"messages": normalized_messages},
  │                config={"configurable":{"thread_id":session_id}},
  │                stream_mode=["messages","custom","updates"],
  │                subgraphs=True, version="v2", control=RunControl())
  │    ├─ messages → model_usage（主 + subagent）/ thinking / text_delta（仅主 agent 文本）
  │    ├─ custom   → tool_execution（ToolTelemetry）+ tool_progress（MinerU）
  │    └─ updates  → assistant_message / tool_execution / 可选 structured_response
  ├─ Philips → Pydantic 再校验 structured_response；缺失/非法 → failed
  ├─ 成功 → status=succeeded(reply=..., result=验证后业务 JSON 或 null)
  ├─ GraphDrained → status=cancelled
  └─ 异常 / NoProgressLoop → status=failed(error=...)（真实错误透传）

GET /runs/{run_id}?after_event_id=N
  → runs 快照 + 顶层 workflow/result + 增量 events + latest_content_event + usage
POST /runs/{run_id}/cancel
  → 协作 drain；未知 404 / 终态 409 / 已 cancelling|cancelled 200 / 活跃 202
```

业务分支由同一主链中的 Skill 和工具完成：

```text
Philips workflow
  → 一次 parse_documents 解析本批全部 PDF
  → 主 Agent 跨单据识别/关联；多个真实票次或无有效 PDF → result.input_problems
  → 一次 lookup_philips_wgq_master_data(product_ids, tracking_artifact?)
      ├─ Tracking：进口 sheet 严格状态 + 倒序最新合格行；申报要素页优先
      └─ Oracle：只补 Tracking 缺失的稳定字段；失败/未命中写 problems
  → ToolStrategy(PhilipsWgqRecognitionResult) → run.result
  → outcome=input_problems 时 data=null，run 仍 succeeded；
     结构化输出缺失/非法才令 run failed

Tecan（通用路径 + Skill 驱动）
  → parse_documents → 同回合并行 tecan-extractor-a/b（各自装 middleware）
  → save_tecan_extraction → 必要时 C 回查/最小 decisions
  → generate_tecan_import → generated Excel 或 input_problems
```

- **事件获取靠轮询**，当前无 `StreamingResponse` / `text/event-stream`。
- 事件类型固定 7 类：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`。
- run 状态机：`queued → running → succeeded | failed`；`queued → cancelled`；`running → cancelling → cancelled`。
- 启动 lifespan 把遗留 `queued`/`running`/`cancelling` 标 `failed("执行已中断，请重试")`。
- 程序内等价路径：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(...)` → `Iterator[RunEvent]`。

### 3.3 外部 provider 边界

| 边界 | 用途 | 集成方式 | 证据 |
|------|------|----------|------|
| MiniMax via Anthropic adapter（生产） | LLM | `DeepAgentsBrainFactory`：`init_chat_model("anthropic:<MINIMAX_MODEL>", ...)` + `thinking={"type":"adaptive"}` → `create_deep_agent(...)` | `runtime/agent.py` |
| MinerU（内网 HTTP） | 文档解析 + ZIP 解压 | `requests`：`POST /tasks` → 轮询 status → 取 JSON/ZIP | `integrations/mineru.py` |
| Oracle（可选） | Philips 稳定主数据缺失字段补齐 | `oracledb` thick mode；配置/失败/未命中写 `problems` | `skills/philipswgqinboundrecognition/scripts/tools.py` |
| LangGraph savers | checkpointer / store | `SqliteSaver`（`thread_id=session_id`）/ `SqliteStore`（`namespace=("dsagents",)`） | `runtime/resources.py` |
| DeepAgents / LangGraph runtime | 协作 drain | `RunControl` per-run → `GraphDrained` → `cancelled` | `runtime/execution.py` |

键名清单（不含值）见 [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) 与 [`STACK.md`](../backend/.planning/codebase/STACK.md)。

## 4. 接口边界

### 4.1 HTTP API（`api.py`）

| 方法 / 路径 | 行为 | 返回要点 |
|---|---|---|
| `POST /runs` | body `{workflow?, messages, session_id?}`；固定 Philips workflow 强制新 session；`content` 仅 `text`/`artifact`；`extra="forbid"` | `200 {run_id, session_id, status:"queued"}`；未知 workflow/非法复用 `422`；通用同 session 冲突 `409` |
| `GET /runs/{run_id}` | query `after_event_id?`；未知 → `404` | `200 {run, workflow, result, events[], latest_content_event, usage}`；`run` 同时含 workflow/result；无模型调用时 `usage=null` |
| `POST /runs/{run_id}/cancel` | 协作 drain | 未知 `404` / 终态 `409` / 已 cancelling\|cancelled `200` / 活跃 `202` |
| `POST /upload` | multipart 字段名 `files`（可多文件）；只保存不解析 | `200 {files:[{file_path,name,mime_type,size}]}` |

补充约定：

- `after_event_id` **只裁剪** `events[]`，不影响 `latest_content_event` 与 `usage`。
- Philips 终态业务 JSON 从 GET 顶层 `result`（或 `run.result`）读取，**不解析** `reply`。
- Philips `result` 固定 `{"outcome":"success|partial_success|input_problems","data":...|null,"problems":[...]}`；英文字段名；`input_problems` 时 `data=null` 且 run 仍 `succeeded`。
- `artifact` block 是项目 API 语义，进入 Brain 前转为文本路径提示，再由 agent 决定 `read_file` / `parse_documents`。
- 当前**无**鉴权、**无** CORS、**无** SSE；时间字段 UTC ISO-8601 毫秒。
- 启动：`cd backend` 后 `uv run uvicorn api:app --host 0.0.0.0 --port 8500`。
- `create_app(*, resource_config=None, harness_factory=create_harness)` 支持测试注入。

完整契约见 [`INTERFACES.md`](../INTERFACES.md) §1/§2 与 [`INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md)。已删除接口清单见 `INTERFACES.md` §6。

### 4.2 程序内入口

仓库**不**提供 one-shot 单函数入口。组合路径：

```text
AgentResources(ResourceConfig) → create_harness(resources) → runs.create_run(...) → harness.execute_run(...)
```

- `execute_run(messages, session_id, run_id, workflow=None)` → `Iterator[RunEvent]`。
- 稳定导出（`runtime/__init__.py`）：`AgentResources`、`ResourceConfig`、`HarnessRuntime`、`create_harness`、`RunEvent`、`RunSnapshot`、`SqliteRunLedger`。
- 测试注入：`create_app(resource_config=..., harness_factory=...)` + `FakeBrainFactory`（`tests/test_support.py`）。

### 4.3 Brain / middleware / Skill 调用边界

- Brain 调用固定：`BrainFactory.create(..., workflow)` 后使用 `stream_mode=["messages","custom","updates"]`，`subgraphs=True`，`version="v2"`，`control=RunControl()`，`thread_id=session_id`。
- 主 agent 名 `MAIN_AGENT_NAME = "dsagents-main"`；`register_harness_profile("anthropic", ...)` 禁用默认 general-purpose subagent（锁定 `deepagents==0.6.12`）。
- **5 工具清单**：`parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`、`save_tecan_extraction`、`generate_tecan_import`。
- Philips 排除帝肯工具，保留 `parse_documents` + `extract_archives` + `lookup_philips_wgq_master_data`；无 SubAgent。
- `StructuredOutputCompatibility.wrap_model_call`：仅在 `ToolStrategy` 请求上用 `request.override(model=...)` 关闭该次 Anthropic thinking；工厂原始模型与通用/Tecan adaptive thinking 不变。
- **`StructuredOutputRecovery`（硬性约定）**：`after_model` 从纯文本 JSON 恢复 `structured_response`；失败则 `jump_to: "model"`（默认最多 2 次，`DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES`）；耗尽或无法继续时**必须** `jump_to: "end"`，且 `can_jump_to` 须含 `"end"`。禁止只返回 `None`——在仅有 `ToolStrategy`、无业务 tool 的图上会触发 model↔model 无限循环。验证：`cd backend && python -m tests.test_harness`。
- 声明式 Tecan SubAgent **不继承**主 Agent middleware，须经无 memory 的 `runtime_middlewares()` 显式注入；主 Agent 手册用 `memory_backend=` 打开，勿同时使用 `create_deep_agent(memory=...)`。

### 4.4 共享状态与查询维度

| 概念 | 作用 | 非作用 |
|------|------|--------|
| `run_id` | **唯一**执行与查询单位；事件/快照/`usage` 均按 run 读 | — |
| `session_id` | LangGraph `thread_id` + 进程内单飞锁键 | 不是持久化对象；不参与 `run_events` 查询 |
| `run_controls` | 进程内 `dict[run_id → RunControl]`，仅 cancel | 非跨进程、不落库 |
| `session_locks` / `active_runs` | 进程内同 session 串行 | 多 worker 失效；锁字典只增不删 |

### 4.5 存储 / artifacts / 事件 / provider

路径由 `ResourceConfig` 锚定 `backend/data/`（与 CWD 无关）；三库互不共享连接，无跨库事务。

| 文件 / 目录 | 通道 | 写入方 |
|-------------|------|--------|
| `data/dsagents_runs.db` | run ledger | `SqliteRunLedger`（fresh schema，无迁移；UTC ISO-8601 毫秒） |
| `data/dsagents_checkpoints.db` | LangGraph checkpointer | `SqliteSaver`（`thread_id=session_id`） |
| `data/dsagents_store.db` | LangGraph store | `SqliteStore`（`namespace=("dsagents",)`） |
| `data/artifacts/uploads/` | 上传源 | `POST /upload` |
| `data/artifacts/downloads/` | 解析/业务产物 | MinerU、解压、Tecan JSON/Excel（唯一下载名，不覆盖） |
| `data/internal/run-events/` | 大 payload spill | ledger（`max_inline_bytes=262_144`，按需创建） |
| `backend/skills/` | Skill 源（非 data） | 只读挂载为 `/skills/` |

`CompositeBackend` 路由摘要：`/memories/` → Store（共享手册 `/memories/AGENTS.md` 缺失时 seed）；`/artifacts/` 与 `/large_tool_results/` → 磁盘；`/skills/` → 只读 Skill 源；其它 → `StateBackend`。详表见 backend ARCHITECTURE。

**事件边界：**

- append-only `run_events`，`event_id` 单调递增；`status` 事件投影 `runs.status/reply/error/result_json/updated_at`；`workflow` 在创建 run 时写入。
- 7 类事件；`model_usage` 为成本/缓存观测，**不计入** `latest_content_event`。
- raw v2 chunk 整体落库（可 spill）；无 TTL/归档。
- API 层 `_usage_summary` 叠加 cache hit rate 与 MiniMax-M3 tier 计价（`PRICING_AS_OF` 等硬编码于 `api.py`）；不可计价模型金额为 `null`。

**上传 / 产物：**

- 虚拟路径 `/artifacts/...` 经 `integrations/artifacts.py` 解析，拒绝 `..`。
- HTTP/业务 Skill 只接受显式 `/artifacts/...`；`parse_documents` 为测试/程序内保留 `allow_local`。
- Tecan 模板在 `/skills/tecanimport/assets/`；Philips Tracking 只读，不生成 Excel。
- 取消/失败**不回滚**已写 downloads。

**Provider 配置键（仅键名）：**

| 组 | 键 | 消费者 |
|----|----|--------|
| MiniMax | `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` | `runtime/agent.py`、`runtime/middleware.py` |
| MinerU | `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS`（必需，fail-fast）；`MINERU_EFFORT` 可空 | `integrations/mineru.py` |
| Oracle（可选，仅 Philips） | `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS` | `skills/philipswgqinboundrecognition/scripts/tools.py` |

`backend/.env` 在相关模块 import 时 `load_dotenv`；长期文档不记录真实值。

## 5. 依赖和归属规则

- **后端代码改动**归属 `backend/`：先更新 [`backend/.planning/codebase/`](../backend/.planning/codebase/) 对应事实文档，再视影响回看 [`ARCHITECTURE.md`](../ARCHITECTURE.md) / [`INTERFACES.md`](../INTERFACES.md) / 本文件（[`AGENTS.md`](../AGENTS.md) 关键约定）。
- **文档分层归属**：
  - 根级 `AGENTS.md` / `ARCHITECTURE.md` / `INTERFACES.md` — 系统边界与导航。
  - `coding_maps/SYSTEM_MAP.md`（本文件）— 系统层跨子项目视图。
  - `docs/*.md` — 详细说明（约定、命令、阅读顺序等）。
  - `backend/.planning/codebase/*` — backend 实现细节的事实来源。
- **包管理**：`uv`（非 pip）；`cd backend && uv sync`；禁止 `pip install -e .` 绕过 `uv.lock`。
- **包布局**：安装根 `backend/`；`py-modules = ["api"]`，packages `runtime*` / `integrations*` / `skills*`；绝对顶层导入；新增 Skill 须在 `[tool.setuptools.package-data]` 追加资源。无 `python -m backend.*`。
- **Protocol 边界**：`typing.Protocol` 只用于 `Brain` / `BrainFactory`；工具用 callable + `ToolCatalog`；资源与 ledger 用具体类。
- **工具 / Skill 归属**：新增 Skill = 新包目录 + `default_tool_catalog()` 静态注册 + `package-data`；无动态 loader。
- **middleware 归属**：实现只放 `runtime/middleware.py`，经 `runtime_middlewares()` 增删；主 Agent 与 SubAgent 装配路径不同（见 §4.3）。
- **关键运行时依赖**（约束与 lock 版本见 STACK）：DeepAgents、LangGraph、LangChain / langchain-anthropic、FastAPI、uvicorn、openpyxl、oracledb（可选）、requests（MinerU）。

## 6. 按任务分类的阅读指南

完整任务→阅读顺序映射见根级 [`docs/reading-order.md`](../docs/reading-order.md)。系统层速查：

### 6.1 后端业务 / API / 存储 / runner

| 任务 | 先读 |
|------|------|
| backend 整体 / runtime / 存储 | [`docs/conventions.md`](../docs/conventions.md) → backend [`ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) + [`STRUCTURE.md`](../backend/.planning/codebase/STRUCTURE.md) → `runtime/execution.py` / `agent.py` / `middleware.py` / `runs.py` / `resources.py` |
| HTTP 契约 / 入口 | [`INTERFACES.md`](../INTERFACES.md) §1/§2 → [`INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md)（APIs）→ `api.py` |
| run 状态 / 事件 / 持久化 | backend ARCHITECTURE §Data Flow / 状态机 / 存储 → `runtime/runs.py` + `runtime/execution.py` |
| 模型流 / Brain / middleware | INTEGRATIONS LLM 节 → `runtime/agent.py` + `runtime/middleware.py` + `runtime/observability.py`；改 recovery 后跑 `tests.test_harness` |
| MinerU | INTEGRATIONS MinerU 节 → `integrations/mineru.py` |
| Oracle | INTEGRATIONS Oracle 节 + [`CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md) Oracle 条 → `skills/philipswgqinboundrecognition/scripts/tools.py` |

### 6.2 跨系统接口

| 任务 | 先读 |
|------|------|
| 对外 HTTP / provider / 存储 / artifacts | [`INTERFACES.md`](../INTERFACES.md) → 本文件 §3–§4 → backend INTEGRATIONS |
| 前端 / 其它子项目 | **现状：仓库无前端子项目**；调用方应只依赖 `INTERFACES.md` 四端点与轮询语义，勿假设 SSE 或 session API |
| 部署面安全（鉴权/CORS） | 已确认缺失；见本文件 §7 与 CONCERNS Security |

### 6.3 领域 Skill / 报告生成

| 任务 | 先读 |
|------|------|
| Philips 识别 | `docs/philips-wgq-inbound-recognition-prd.md` → `skills/philipswgqinboundrecognition/{SKILL.md,schema.py,scripts/tools.py}` → `tests/test_philips_wgq_inbound_recognition.py` |
| Tecan 生成 | `skills/tecanimport/SKILL.md` + `references/` → `scripts/tools.py` / `documents.py` → `tests/test_tecan_import.py` |
| 新增 Skill | CONVENTIONS 工具静态注册约定 → `runtime/tools.py` 追加 import/注册 → `pyproject.toml` package-data → 新建 Skill 包目录 |
| Excel 模板 / 单元格 | 当前仅 Tecan `assets/` + `scripts/documents.py`；Philips Tracking 为只读输入，不生成 Excel |

### 6.4 测试与真实外部依赖

| 任务 | 先读 |
|------|------|
| 测试策略与命令 | [`backend/.planning/codebase/TESTING.md`](../backend/.planning/codebase/TESTING.md) |
| 普通本地回归（FakeBrain / mock 网络） | `cd backend` 后：`python -m tests.test_tools` / `test_run_ledger` / `test_harness` / `test_api` / `test_workflow_setup` / `test_philips_wgq_inbound_recognition` / `test_tecan_import`（**非 pytest**） |
| 真实集成（手动、opt-in） | `test_real_philips_wgq_inbound_recognition`（`DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1`）；`test_minimax_cache_baseline` / `test_real_philips_wgq_ups` 无完整 env 门闸，易误跑。均勿纳入默认回归 |
| 仅文档变更 | `git diff --check` |

## 7. 集成风险检查清单和验证入口

提炼自 [`backend/.planning/codebase/CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md)（证据见该文档）。改动触及下列面时按项核对：

### 7.1 配置与部署

- [ ] MinerU 必需键 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS`（缺则 fail-fast）；`MINERU_EFFORT` 可空。
- [ ] MiniMax 三键在首次 `create`/调用前可用（工厂无启动期强校验）。
- [ ] Philips Oracle：三凭证与可选 `ORACLE_CLIENT_LIB_DIR`；配置/初始化/查询失败或未命中写 `problems`，保留已有数据；Tecan 不消费 Oracle。Instant Client **不在仓库**。
- [ ] 长期文档只记配置键与消费者，不抄录 `.env` 真实值/连接串。
- [ ] schema 无迁移：切换部署停服并清空整个 `backend/data/`。
- [ ] 数据目录生命周期：三库 + uploads/downloads + spill 一并备份/迁移。

### 7.2 并发、取消与状态

- [ ] 单飞锁 / `run_controls` 仅进程内；`uvicorn --workers N` 或多实例同 `session_id` 可交错写 checkpointer。
- [ ] cancel 为协作 drain，非强杀；工具阻塞（如 MinerU 轮询）期间可能延迟；取消不回滚 artifacts。
- [ ] daemon 线程 + 启动 `fail_incomplete_runs`；强杀后需重启纠正投影。
- [ ] 注意 cancel 与 `execute_run` 注册 `RunControl` 的竞态窗口（CONCERNS 标「需确认」）。

### 7.3 安全与数据面

- [ ] HTTP 匿名、无用户隔离；任意 `run_id` 可读。
- [ ] 无 CORS；浏览器直连需显式评估。
- [ ] `/upload` 无大小/类型/数量限制（磁盘占满风险）。
- [ ] 错误与 raw 未脱敏落库并可经 `GET /runs` 回传。
- [ ] `parse_documents` 的 `allow_local` 可把本机路径交给 MinerU（业务 generator 默认关闭）。
- [ ] 主 Agent 可写 `/artifacts/**`（仅 deny `/skills/**`）；SubAgent 全路径 write deny。

### 7.4 性能与留存

- [ ] `runs.db` 无 WAL / 无 busy_timeout；高频 emit + 轮询可能 `database is locked`。
- [ ] `run_events` 与 spill 只增不删；无 TTL/归档。
- [ ] 三库最终一致，勿假设事件 succeeded 与 checkpoint 强一致。

### 7.5 middleware / 结构化输出 / 依赖

- [ ] 改 `StructuredOutputRecovery` 时必须保留 `can_jump_to` 含 `"end"` 与耗尽时 `jump_to: "end"`；用 `python -m tests.test_harness` 验证重试封顶。
- [ ] 共享 middleware 只改 `runtime_middlewares()`；SubAgent 勿误传 `memory_backend`；`test_workflow_setup` 断言 Sub 无 `MemoryMiddleware`。
- [ ] stream chunk 形状依赖 langchain/deepagents 约定；升级靠 `uv.lock` + FakeBrain 回归。
- [ ] MiniMax 强绑 Anthropic 协议与 thinking/cache 中间件；换 provider 需同步解析与 profile。
- [ ] 四层文档手工同步；pricing 常量硬编码于 `api.py`。
- [ ] 无 CI/lint/pytest 门禁；本地 7 脚本需按影响范围人工跑。

### 7.6 验证入口

| 场景 | 入口 |
|------|------|
| 仅文档 | `git diff --check` |
| HTTP / cancel / usage / workflow/result | `cd backend && python -m tests.test_api` |
| harness / 事件序列 / StructuredOutputRecovery 封顶 | `python -m tests.test_harness` |
| ledger / spill / result_json | `python -m tests.test_run_ledger` |
| 工具 / catalog / MinerU mock | `python -m tests.test_tools` |
| SubAgent / middleware 装配 | `python -m tests.test_workflow_setup` |
| Philips / Tecan 业务 | `python -m tests.test_philips_wgq_inbound_recognition` / `test_tecan_import` |
| 真实 Philips HTTP | `DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1 python -m tests.test_real_philips_wgq_inbound_recognition`（PowerShell 需先设置 env） |
| 真实模型 / MinerU | 见 TESTING.md 其它真实集成命令（默认不进普通门禁） |
| 部署 Oracle | CONCERNS Oracle 条：Instant Client 路径 + 连通性；验证 fallback 与真实查询分场景 |

## 8. 使用过的源文档索引

根级（系统边界与导航）：

- [`AGENTS.md`](../AGENTS.md)
- [`ARCHITECTURE.md`](../ARCHITECTURE.md)
- [`INTERFACES.md`](../INTERFACES.md)

子项目事实（backend 实现细节事实来源，Analysis Date: 2026-07-16）：

- [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md)
- [`backend/.planning/codebase/STRUCTURE.md`](../backend/.planning/codebase/STRUCTURE.md)
- [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md)
- [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)
- [`backend/.planning/codebase/CONVENTIONS.md`](../backend/.planning/codebase/CONVENTIONS.md)
- [`backend/.planning/codebase/TESTING.md`](../backend/.planning/codebase/TESTING.md)
- [`backend/.planning/codebase/CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md)

相关导航（未全文展开，任务阅读时按需打开）：

- [`docs/conventions.md`](../docs/conventions.md)
- [`docs/commands.md`](../docs/commands.md)
- [`docs/reading-order.md`](../docs/reading-order.md)

本轮（2026-07-16）在 backend 7 份事实文档同步后刷新：章节对齐 skill 要求的 8 段结构；补强 `StructuredOutputRecovery` 有界重试硬性约定（含空文本路径、`1+max_retries` 封顶）、工厂缺则补齐（Recovery `insert(0)`）、主/Sub middleware 装配差异、Philips `run.result` 成功/失败语义与 `test_harness` 验证入口；run-first、四端点、7 事件及现有风险清单保持。
