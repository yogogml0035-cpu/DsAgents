# SYSTEM_MAP

> 系统层跨子项目理解手册。本文件只描述系统形态、边界与读图指南；底层实现细节以 [`backend/.planning/codebase/`](../backend/.planning/codebase/) 为事实来源。
> 上游事实：[`ARCHITECTURE.md`](../ARCHITECTURE.md)、[`INTERFACES.md`](../INTERFACES.md)、[`AGENTS.md`](../AGENTS.md)。
> 本轮刷新（2026-07-16）对齐 backend 全部事实文档：加入固定 Philips workflow、`run.result` 结构化通道、独立 `runtime/middleware.py`、严格 Tracking/Oracle 主数据补齐与真实 HTTP 验收入口；删除旧 Philips A/B/C、Excel 和兼容语义。run-first、四 HTTP 端点、7 类事件及通用/Tecan 边界保持。

## 1. 系统目的和仓库形态

DsAgents 是一个 **agent 运行时底座**：把能力（Brain、工具）做成可插拔，而不绑定具体 runner、容器、模型或工作流。整个产品收口在 `backend/` 顶层源码布局（`api.py`、`runtime/`、`integrations/`、`skills/`；绝对导入 `from runtime import ...`）。

- **形态**：单子项目仓库，唯一产品子项目是 `backend/`（发行名 `dsagents`，包管理器 `uv`）。**无前端子项目**（当前源文档未确认任何前端代码归属本仓库）。
- **架构**：run-first。run 是唯一的执行单位与查询单位；`run_events` 表 append-only，`runs` 表是事件投影出的快照；不再有 session 模块 / session 持久化层。
- **短期上下文**：完全交给 LangGraph `checkpointer` + `thread_id=session_id`。`session_id` 标识符保留，但用途已收窄为 checkpointer 键和进程内串行保护键，不再是一等持久化对象。
- **能力可插拔**：`Brain` / `BrainFactory` 是 `typing.Protocol`（`runtime/agent.py`）；middleware 实现集中在 `runtime/middleware.py` 并由 agent 工厂装配；工具保持普通 callable + `ToolCatalog`（`runtime/tools.py`）。默认装配从 `create_harness` 进入（`DeepAgentsBrainFactory` + `default_tool_catalog()`）；本地测试用 `FakeBrainFactory` 替换。
- **工具静态注册**：`default_tool_catalog()` 静态注册 5 个工具（2 个 MinerU 通用 + Philips 1 个 + Tecan 2 个），普通 Python import；不自动扫描、无插件平台、无动态模块加载器。
- **业务能力按 Skill 打包**：`skills/philipswgqinboundrecognition/` 提供专用 Skill、Pydantic 合同与单一主数据工具；`skills/tecanimport/` 保留 2 个业务 Tool。声明式 SubAgent 仅 `tecan-extractor-a/b`，各自装 middleware。
- **入口形态**：HTTP（`POST /runs` 创建 run、立即返回 `queued`；纯轮询获取增量事件，无 SSE；含 cancel）+ 程序内组合（`AgentResources` + `create_harness(...).execute_run(...)`）；无单函数 one-shot API。
- **业务能力形态**：Philips 由 `workflow="philips_wgq_inbound_recognition"` 显式路由并返回验证后的结构化 JSON；Tecan 继续由 Skill 驱动 A/B 抽取和 Excel。二者均不增加业务 HTTP、状态表或恢复接口。

详细运行时原则与维护规则见根级 [`docs/conventions.md`](../docs/conventions.md)（`AGENTS.md` 要求改动 backend 前必读）。

## 2. 子项目职责表

| 子项目 | 目录 | 当前职责 | 技术栈要点 | 边界（不做什么） |
|--------|------|----------|------------|------------------|
| backend | `backend/` | 发行名 `dsagents`；源码顶层 `api.py`、`runtime/`、`integrations/`、`skills/`：run-first runtime + Philips/Tecan 两个内置 Skill + 2 个 Tecan SubAgent + runtime middleware + 5 个静态工具 | Python `>=3.11,<4.0`；`uv`；FastAPI / uvicorn；DeepAgents / LangGraph；SQLite 三库；MinerU；openpyxl；可选 oracledb | 不提供 session/业务状态表、SSE、鉴权/CORS、通用工作流引擎、跨进程队列/锁、沙箱 / 脚本执行、插件平台、健康检查端点 |

backend 内部分层、目录与配置事实见 [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) 与 [`backend/.planning/codebase/STRUCTURE.md`](../backend/.planning/codebase/STRUCTURE.md)。

## 3. 跨边界调用链和数据流

当前是**单子项目**。下列描述 backend 内部主调用链与外部 provider 边界（分层细节见 backend ARCHITECTURE §Layers / §Data Flow）。

### 3.1 分层视图

```text
HTTP (api.py)
  → Harness (runtime/execution.py)          # stream → RunEvent；cancel
    → 能力 (runtime/agent.py + middleware.py + tools.py)  # Brain / middleware / SubAgent / ToolCatalog
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
  │    └─ Philips：ToolStrategy 结构化合同；仅 parse_documents + lookup 工具；无 SubAgent；追加
  │       StructuredOutputCompatibility（关 thinking）+ StructuredOutputRecovery（文本 JSON 兜底）
  ├─ brain.stream({"messages": normalized_messages},
  │                config={"configurable":{"thread_id":session_id}},
  │                stream_mode=["messages","custom","updates"],
  │                subgraphs=True, version="v2", control=RunControl())
  │    ├─ messages → 先提取 model_usage（主 agent + subagent），再仅主 agent thinking / text_delta
  │    │             （subagent 文本按 lc_agent_name 丢弃，usage 仍计入）
  │    ├─ custom   → tool_execution（ToolTelemetry 三态 + 计时 + scope）
  │    │             + tool_progress（parse_documents / extract_archives 进度）
  │    └─ updates  → assistant_message / tool_execution / 可选 structured_response
  ├─ Philips → Pydantic 再校验 structured_response；缺失/非法 → failed
  ├─ 成功 → status=succeeded(reply=..., result=验证后业务 JSON 或 null)
  ├─ GraphDrained → status=cancelled   （POST /runs/{id}/cancel 的 RunControl drain）
  └─ 异常 / NoProgressLoop → status=failed(error=...)（真实错误透传）

GET /runs/{run_id}?after_event_id=N  → runs 快照 + 顶层 workflow/result + 增量 events + latest_content_event + usage
POST /runs/{run_id}/cancel            → 协作 drain；未知 404 / 终态 409 / 已 cancelling|cancelled 200 / 活跃 202
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

Tecan
  → parse_documents → 同回合并行 tecan-extractor-a/b（各自装 middleware）
  → save_tecan_extraction → 必要时 C 回查/最小 decisions
  → generate_tecan_import → generated Excel 或 input_problems
```

- **事件获取靠轮询**，当前无 `StreamingResponse` / `text/event-stream`。
- 事件类型固定 7 类：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`。
- run 状态机：`queued → running → succeeded | failed`；`queued → cancelled`；`running → cancelling → cancelled`。启动 lifespan 把遗留 `queued/running/cancelling` 标 `failed("执行已中断，请重试")`。
- 程序内等价路径：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(...)` → `Iterator[RunEvent]`。

### 3.3 外部 provider 边界

| 边界 | 用途 | 集成方式 | 证据 |
|------|------|----------|------|
| MiniMax via Anthropic adapter（生产） | LLM | `DeepAgentsBrainFactory`：`init_chat_model("anthropic:<MINIMAX_MODEL>", ...)` + `thinking={"type":"adaptive"}` → `create_deep_agent(...)` | `runtime/agent.py` |
| MinerU（内网 HTTP） | 文档解析 + ZIP 解压 | `requests`：`POST /tasks` → 轮询 status → 取 JSON/ZIP | `integrations/mineru.py` |
| Oracle（可选） | Philips 稳定主数据缺失字段补齐 | `oracledb` 参数化查询；配置/初始化/查询失败或未命中写 `problems` | `skills/philipswgqinboundrecognition/scripts/tools.py` |
| LangGraph savers | checkpointer / store | `SqliteSaver`（`thread_id=session_id`）/ `SqliteStore`（`namespace=("dsagents",)`） | `runtime/resources.py` |
| DeepAgents / LangGraph runtime | 协作 drain | `RunControl` per-run → `GraphDrained` → `cancelled` | `runtime/execution.py` |

键名清单（不含值）见 [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) 与 [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)。

## 4. 接口边界

### 4.1 HTTP API（`api.py`）

| 方法 / 路径 | 行为 | 返回要点 |
|---|---|---|
| `POST /runs` | body `{workflow?, messages, session_id?}`；固定 Philips workflow 强制新 session；`content` 仅 `text`/`artifact`；`extra="forbid"` | `200 {run_id, session_id, status:"queued"}`；未知 workflow/非法复用 `422`；通用同 session 冲突 `409` |
| `GET /runs/{run_id}` | query `after_event_id?`；未知 → `404` | `200 {run, workflow, result, events[], latest_content_event, usage}`；`run` 同时含 workflow/result；无模型调用时 `usage=null` |
| `POST /runs/{run_id}/cancel` | 协作 drain | 未知 `404` / 终态 `409` / 已 cancelling\|cancelled `200` / 活跃 `202` |
| `POST /upload` | multipart 字段名 `files`（可多文件）；只保存不解析 | `200 {files:[{file_path,name,mime_type,size}]}` |

- `after_event_id` **只裁剪** `events[]`，不影响 `latest_content_event` 与 `usage`。
- `artifact` block 是项目 API 语义，进入 Brain 前转为文本路径提示，再由 agent 决定 `read_file` / `parse_documents`。
- 当前**无**鉴权、**无** CORS、**无** SSE。
- 时间字段：UTC ISO-8601 毫秒。
- 启动：`cd backend` 后 `uv run uvicorn api:app --host 0.0.0.0 --port 8500`。
- `create_app(*, resource_config=None, harness_factory=create_harness)` 支持测试注入；模块级 `app = create_app()` 为生产装配。

完整契约见 [`INTERFACES.md`](../INTERFACES.md) §1/§2 与 [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md)（APIs & External Services / Data Storage）。已删除的旧 session 接口清单见 `INTERFACES.md` §6。

### 4.2 程序内入口

仓库**不**提供 one-shot 单函数入口。组合路径：

```text
AgentResources(ResourceConfig) → create_harness(resources) → runs.create_run(...) → harness.execute_run(...)
```

稳定导出见 `runtime/__init__.py`：`AgentResources`、`ResourceConfig`、`HarnessRuntime`、`create_harness`、`RunEvent`、`RunSnapshot`、`SqliteRunLedger`。

### 4.3 Brain / middleware / Skill 调用边界

- Brain 调用固定：`BrainFactory.create(..., workflow)` 后使用 `stream_mode=["messages","custom","updates"]`，`subgraphs=True`，`version="v2"`，`control=RunControl()`，`thread_id=session_id`。
- Philips：主 Agent `ToolStrategy(PhilipsWgqRecognitionResult)`，无 SubAgent，仅两个允许工具；`StructuredOutputCompatibility.wrap_model_call` 只为该 `ToolStrategy` 请求用 `request.override(model=...)` 复制模型并关闭 thinking；`StructuredOutputRecovery.after_model` 在模型把合法 JSON 写进文本、未产生 schema tool_call 时补写 `structured_response`（不改写 outcome；`success` 允许非空 `problems`）。Harness 捕获/复验 `structured_response`；tool 与 `run.result` 统一英文字段名（OMS 中文表单由调用方映射）。`input_problems` 是业务 outcome，不是 run 失败。
- `runtime/middleware.py` 的 `runtime_middlewares(*, memory_backend=None)` 始终返回 `StructuredOutputRecovery`、`ToolTelemetry`、`NoProgressMiddleware`、`StructuredOutputCompatibility`（Recovery 置前，使 `after_model` 在 after 链末位执行）；主 Agent 经 `execution.py` 传 `memory_backend=resources.backend` 时再追加内置 `MemoryMiddleware`（`/memories/AGENTS.md` + 受限提示，不走 `memory=` 默认语义）。两个 Tecan SubAgent **不继承**主 Agent middleware，须经无 memory 的 `runtime_middlewares()` 显式注入；兼容/recovery middleware 不增加 `state_schema` 或 LangGraph 自定义状态，no-progress 判断也只从现有消息状态派生。
- 主 agent 名 `MAIN_AGENT_NAME = "dsagents-main"`；`register_harness_profile("anthropic", ...)` 禁用默认 general-purpose subagent（锁定 `deepagents==0.6.12` 无构造参数式 `harness_profile`）。
- 5 工具清单：`parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`、`save_tecan_extraction`、`generate_tecan_import`。

## 5. 共享状态、存储、事件、上传、产物、provider 边界

### 5.1 共享状态与查询维度

| 概念 | 作用 | 非作用 |
|------|------|--------|
| `run_id` | **唯一**执行与查询单位；事件/快照/`usage` 均按 run 读 | — |
| `session_id` | LangGraph `thread_id` + 进程内单飞锁键 | 不是持久化对象；不参与 `run_events` 查询 |
| `run_controls` | 进程内 `dict[run_id → RunControl]`，仅 cancel | 非跨进程、不落库 |
| `session_locks` / `active_runs` | 进程内同 session 串行 | 多 worker 失效；锁字典只增不删 |

### 5.2 存储（三条逻辑 SQLite + 文件）

路径由 `ResourceConfig` 锚定 `backend/data/`（与 CWD 无关）；三库互不共享连接，无跨库事务。

| 文件 / 目录 | 通道 | 写入方 |
|-------------|------|--------|
| `data/dsagents_runs.db` | run ledger | `SqliteRunLedger`（fresh schema，无迁移；UTC ISO-8601 毫秒） |
| `data/dsagents_checkpoints.db` | LangGraph checkpointer | `SqliteSaver` |
| `data/dsagents_store.db` | LangGraph store | `SqliteStore`（`namespace=("dsagents",)`） |
| `data/artifacts/uploads/` | 上传源 | `POST /upload` |
| `data/artifacts/downloads/` | 解析/业务产物 | MinerU、解压、Tecan JSON/Excel（唯一下载名，不覆盖） |
| `data/internal/run-events/` | 大 payload spill | ledger（`max_inline_bytes=262_144`，按需创建） |
| `backend/skills/` | Skill 源（非 data） | 只读挂载为 `/skills/` |

`CompositeBackend` 路由摘要：`/memories/` → Store（共享手册 `/memories/AGENTS.md` 缺失时 seed）；`/artifacts/` 与 `/large_tool_results/` → 磁盘；`/skills/` → 只读 Skill 源；其它 → `StateBackend`。详表见 backend ARCHITECTURE。

### 5.3 事件边界

- append-only `run_events`，`event_id` 单调递增；`status` 事件投影 `runs.status/reply/error/updated_at`。
- 7 类事件；`model_usage` 为成本/缓存观测，**不计入** `latest_content_event`。
- raw v2 chunk 整体落库（可 spill）；无 TTL/归档。
- `runs` 投影另保存可选 `workflow` / `result_json`；终态 `status` 事件可携带同一 `result`。
- API 层 `_usage_summary` 叠加 cache hit rate 与 MiniMax-M3 tier 计价（`PRICING_AS_OF` 等硬编码于 `api.py`）；不可计价模型金额为 `null`。

### 5.4 上传 / 产物 / 路径

- 上传：`clean_filename` + `make_timestamped_name`（同请求共用 batch 时间戳；仅物理重名时加序号）。
- 虚拟路径 `/artifacts/...` 经 `integrations/artifacts.py` 解析，拒绝 `..` 越权。
- HTTP/业务 Skill 只接受显式 `/artifacts/...`；`parse_documents` 为测试/程序内保留 `allow_local`（生产风险见 §8）。
- Tecan 模板在 `/skills/tecanimport/assets/`；生成时复制填充，不改仓库模板。Philips 只读可选 Tracking `.xlsx`，不生成 Excel。
- 取消/失败**不回滚**已写 downloads 文件。

### 5.5 Provider 配置键（仅键名）

| 组 | 键（示例） | 消费者 |
|----|------------|--------|
| MiniMax | `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` | `runtime/agent.py`、`runtime/middleware.py` |
| MinerU | `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS`（必需，fail-fast）；`MINERU_EFFORT` 可空 | `integrations/mineru.py` |
| Oracle（可选，仅 Philips） | `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS` | `skills/philipswgqinboundrecognition/scripts/tools.py` |

`backend/.env` 在相关模块 import 时 `load_dotenv`；长期文档不记录真实值。

## 6. 依赖和归属规则

- **后端代码改动**归属 `backend/`：先更新 [`backend/.planning/codebase/`](../backend/.planning/codebase/) 对应事实文档，再视影响回看 [`ARCHITECTURE.md`](../ARCHITECTURE.md) / [`INTERFACES.md`](../INTERFACES.md) / 本文件（[`AGENTS.md`](../AGENTS.md) 关键约定）。
- **文档分层归属**：
  - 根级 `AGENTS.md` / `ARCHITECTURE.md` / `INTERFACES.md` — 系统边界与导航。
  - `coding_maps/SYSTEM_MAP.md`（本文件）— 系统层跨子项目视图。
  - `docs/*.md` — 详细说明（约定、命令、阅读顺序等）。
  - `backend/.planning/codebase/*` — backend 实现细节的事实来源。
- **包管理**：`uv`（非 pip）；`cd backend && uv sync`；禁止 `pip install -e .` 绕过 `uv.lock`。
- **包布局**：安装根 `backend/`；`py-modules = ["api"]`，packages `runtime*` / `integrations*` / `skills*`；绝对顶层导入；新增 Skill 须在 `[tool.setuptools.package-data]` 追加 `SKILL.md`/`references`/`assets`。无 `python -m backend.*`。
- **Protocol 边界**：`typing.Protocol` 只用于 `Brain` / `BrainFactory`；工具用 callable + `ToolCatalog`；资源与 ledger 用具体类。
- **关键运行时依赖**（约束与 lock 版本见 STACK）：DeepAgents、LangGraph、LangChain / langchain-anthropic、FastAPI、uvicorn、openpyxl、oracledb（可选）、requests（MinerU）。

## 7. 按任务分类的阅读指南

完整任务→阅读顺序映射见根级 [`docs/reading-order.md`](../docs/reading-order.md)。系统层速查：

### 7.1 后端业务 / API / 存储 / runner

| 任务 | 先读 |
|------|------|
| backend 整体 / runtime / 存储 | [`docs/conventions.md`](../docs/conventions.md) → [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) + [`STRUCTURE.md`](../backend/.planning/codebase/STRUCTURE.md) → `runtime/execution.py` / `agent.py` / `middleware.py` / `runs.py` / `resources.py` |
| HTTP 契约 / 入口 | [`INTERFACES.md`](../INTERFACES.md) §1/§2 → [`INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md)（APIs）→ `api.py` |
| run 状态 / 事件 / 持久化 | backend ARCHITECTURE §Data Flow / 状态机 / 存储 → `runtime/runs.py` + `runtime/execution.py` |
| 模型流 / Brain / middleware | [`INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) LLM 节 → `runtime/agent.py` + `runtime/middleware.py` + `runtime/observability.py` |
| MinerU | INTEGRATIONS MinerU 节 → `integrations/mineru.py` |
| Oracle | INTEGRATIONS Oracle 节 + [`CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md) Oracle 条 → `skills/philipswgqinboundrecognition/scripts/tools.py` |

### 7.2 跨系统接口

| 任务 | 先读 |
|------|------|
| 对外 HTTP / provider / 存储 / artifacts | [`INTERFACES.md`](../INTERFACES.md) → 本文件 §3–§5 → backend INTEGRATIONS |
| 前端 / 其它子项目 | **现状：仓库无前端子项目**；调用方应只依赖 `INTERFACES.md` 四端点与轮询语义，勿假设 SSE 或 session API |
| 部署面安全（鉴权/CORS） | 已确认缺失；见本文件 §8 与 CONCERNS Security |

### 7.3 领域 Skill / 报告生成

| 任务 | 先读 |
|------|------|
| Philips 识别 | `docs/philips-wgq-inbound-recognition-prd.md` → `skills/philipswgqinboundrecognition/{SKILL.md,schema.py,scripts/tools.py}` → `tests/test_philips_wgq_inbound_recognition.py` |
| Tecan 生成 | `skills/tecanimport/SKILL.md` + `references/` → `scripts/tools.py` / `documents.py` → `tests/test_tecan_import.py` |
| 新增 Skill | CONVENTIONS 工具静态注册约定 → `runtime/tools.py` 追加 import/注册 → `pyproject.toml` package-data → 新建 Skill 包目录 |
| Excel 模板 / 单元格 | 当前仅 Tecan `assets/` + `scripts/documents.py`；Philips Tracking 为只读输入，不生成 Excel |

### 7.4 测试与真实外部依赖

| 任务 | 先读 |
|------|------|
| 测试策略与命令 | [`backend/.planning/codebase/TESTING.md`](../backend/.planning/codebase/TESTING.md) |
| 普通本地回归（FakeBrain / mock 网络） | `cd backend` 后：`python -m tests.test_tools` / `test_run_ledger` / `test_harness` / `test_api` / `test_workflow_setup` / `test_philips_wgq_inbound_recognition` / `test_tecan_import`（**非 pytest**） |
| 真实集成（手动、opt-in） | 上述脚本另加 `test_real_philips_wgq_inbound_recognition`（`DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1`）；`test_minimax_cache_baseline` 无开关，易误跑。均勿纳入默认门禁 |
| 仅文档变更 | `git diff --check` |

## 8. 集成风险检查清单和验证入口

提炼自 [`backend/.planning/codebase/CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md)（证据见该文档）。改动触及下列面时按项核对：

### 8.1 配置与部署

- [ ] MinerU 必需键 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS`（缺则 fail-fast）；`MINERU_EFFORT` 可空。
- [ ] MiniMax 三键在首次 `create`/调用前可用（工厂无启动期强校验）。
- [ ] Philips Oracle：三凭证与可选 `ORACLE_CLIENT_LIB_DIR`；配置/初始化/查询失败或未命中写 `problems`，保留已有数据；Tecan 不消费 Oracle。
- [ ] 长期文档只记配置键与消费者，不抄录 `.env` 真实值/连接串。
- [ ] schema 无迁移：切换部署停服并清空整个 `backend/data/`。
- [ ] 数据目录生命周期：三库 + uploads/downloads + spill 一并备份/迁移。

### 8.2 并发、取消与状态

- [ ] 单飞锁 / `run_controls` 仅进程内；`uvicorn --workers N` 或多实例同 `session_id` 可交错写 checkpointer。
- [ ] cancel 为协作 drain，非强杀；工具阻塞（如 MinerU 轮询）期间可能延迟；取消不回滚 artifacts。
- [ ] daemon 线程 + 启动 `fail_incomplete_runs`；强杀后需重启纠正投影。
- [ ] 注意 cancel 与 `execute_run` 注册 `RunControl` 的竞态窗口（CONCERNS 标「需确认」）。

### 8.3 安全与数据面

- [ ] HTTP 匿名、无用户隔离；任意 `run_id` 可读。
- [ ] 无 CORS；浏览器直连需显式评估。
- [ ] `/upload` 无大小/类型/数量限制（磁盘占满风险）。
- [ ] 错误与 raw 未脱敏落库并可经 `GET /runs` 回传。
- [ ] `parse_documents` 的 `allow_local` 可把本机路径交给 MinerU（业务 generator 默认关闭）。
- [ ] 主 Agent 可写 `/artifacts/**`（仅 deny `/skills/**`）；SubAgent 全路径 write deny。

### 8.4 性能与留存

- [ ] `runs.db` 无 WAL / 无 busy_timeout；高频 emit + 轮询可能 `database is locked`。
- [ ] `run_events` 与 spill 只增不删；无 TTL/归档。
- [ ] 三库最终一致，勿假设事件 succeeded 与 checkpoint 强一致。

### 8.5 依赖与文档

- [ ] stream chunk 形状依赖 langchain/deepagents 约定；升级靠 `uv.lock` + FakeBrain 回归。
- [ ] MiniMax 强绑 Anthropic 协议与 thinking/cache 中间件；换 provider 需同步解析与 profile。
- [ ] 四层文档手工同步；pricing 常量硬编码于 `api.py`。
- [ ] 无 CI/lint/pytest 门禁；本地 7 脚本需按影响范围人工跑。

### 8.6 验证入口

| 场景 | 入口 |
|------|------|
| 仅文档 | `git diff --check` |
| HTTP / cancel / usage | `cd backend && python -m tests.test_api` |
| harness / 事件序列 | `python -m tests.test_harness` |
| ledger / spill | `python -m tests.test_run_ledger` |
| 工具 / catalog / MinerU mock | `python -m tests.test_tools` |
| SubAgent / middleware 装配 | `python -m tests.test_workflow_setup` |
| Philips / Tecan 业务 | `python -m tests.test_philips_wgq_inbound_recognition` / `test_tecan_import` |
| 真实 Philips HTTP | `DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1 python -m tests.test_real_philips_wgq_inbound_recognition`（PowerShell 需先设置 env） |
| 真实模型 / MinerU | 见 TESTING.md 其它真实集成命令（默认不进普通门禁） |
| 部署 Oracle | CONCERNS Oracle 条：Instant Client 路径 + 连通性；验证 fallback 与真实查询分场景 |

## 9. 使用过的源文档索引

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

本轮（2026-07-16）在 backend 7 份事实文档同步后更新：固定 Philips workflow、结构化 result、5 个工具、独立 runtime middleware 模块、2 个 Tecan SubAgent、严格 Tracking/Oracle 补齐和真实 Philips HTTP 验收；run-first、四端点、7 事件及现有风险清单保持。
