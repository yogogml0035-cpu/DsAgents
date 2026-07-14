# SYSTEM_MAP

> 系统层跨子项目理解手册。本文件只描述系统形态、边界与读图指南；底层实现细节以 [`backend/.planning/codebase/`](../backend/.planning/codebase/) 为事实来源。
> 上游事实：[`ARCHITECTURE.md`](../ARCHITECTURE.md)、[`INTERFACES.md`](../INTERFACES.md)、[`AGENTS.md`](../AGENTS.md)。
> 本轮刷新（2026-07-14）对齐 backend 全部事实文档（同日 `Analysis Date: 2026-07-14`）：保持 run-first、四 HTTP 端点、7 类事件、每 Skill 2 业务 Tool、`runtime/`/`integrations/`/`skills/` 顶层布局等既有结论；补充分层调用视图、共享状态边界、测试分桶与 CONCERNS 中已确认的运维/安全风险（WAL、上传限制、`allow_local`、协作 cancel 等）。

## 1. 系统目的和仓库形态

DsAgents 是一个 **agent 运行时底座**：把能力（Brain、工具）做成可插拔，而不绑定具体 runner、容器、模型或工作流。整个产品收口在 `backend/` 顶层源码布局（`api.py`、`runtime/`、`integrations/`、`skills/`；绝对导入 `from runtime import ...`）。

- **形态**：单子项目仓库，唯一产品子项目是 `backend/`（发行名 `dsagents`，包管理器 `uv`）。**无前端子项目**（当前源文档未确认任何前端代码归属本仓库）。
- **架构**：run-first。run 是唯一的执行单位与查询单位；`run_events` 表 append-only，`runs` 表是事件投影出的快照；不再有 session 模块 / session 持久化层。
- **短期上下文**：完全交给 LangGraph `checkpointer` + `thread_id=session_id`。`session_id` 标识符保留，但用途已收窄为 checkpointer 键和进程内串行保护键，不再是一等持久化对象。
- **能力可插拔**：`Brain` / `BrainFactory` 是 `typing.Protocol`（`runtime/agent.py`）；工具保持普通 callable + `ToolCatalog`（`runtime/tools.py`）。默认装配从 `create_harness` 进入（`DeepAgentsBrainFactory` + `default_tool_catalog()`）；本地测试用 `FakeBrainFactory` 替换。
- **工具静态注册**：`default_tool_catalog()` 静态注册 6 个工具（2 个 MinerU 通用 + 每个 Skill 2 个业务），普通 Python import；不自动扫描、无插件平台、无动态模块加载器。
- **业务能力按 Skill 打包**：两个内置 Skill 包 `skills/philipswgqimport/` 与 `skills/tecanimport/`，每个仅暴露 2 个业务 Tool。4 个声明式 SubAgent（`workflow_subagents()`）各自装自己的 middleware。
- **入口形态**：HTTP（`POST /runs` 创建 run、立即返回 `queued`；纯轮询获取增量事件，无 SSE；含 cancel）+ 程序内组合（`AgentResources` + `create_harness(...).execute_run(...)`）；无单函数 one-shot API。
- **业务能力形态**：模型按明确业务目标加载 Skill；A/B/C、裁决、canonical 与 Excel 以显式 artifact 路径串联，不增加业务 HTTP、状态表或恢复接口。

详细运行时原则与维护规则见根级 [`docs/conventions.md`](../docs/conventions.md)（`AGENTS.md` 要求改动 backend 前必读）。

## 2. 子项目职责表

| 子项目 | 目录 | 当前职责 | 技术栈要点 | 边界（不做什么） |
|--------|------|----------|------------|------------------|
| backend | `backend/` | 发行名 `dsagents`；源码顶层 `api.py`、`runtime/`、`integrations/`、`skills/`：run-first runtime + 两个内置 Skill + 4 个声明式 SubAgent + 两个运行时 middleware + 6 个静态注册工具 | Python `>=3.11,<4.0`；`uv`；FastAPI / uvicorn；DeepAgents / LangGraph；SQLite 三库；MinerU（内网 HTTP）；openpyxl；可选 oracledb（thick mode） | 不提供 session/业务状态表、SSE、鉴权/CORS、通用工作流引擎、跨进程队列/锁、沙箱 / 脚本执行、插件平台、健康检查端点 |

backend 内部分层、目录与配置事实见 [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) 与 [`backend/.planning/codebase/STRUCTURE.md`](../backend/.planning/codebase/STRUCTURE.md)。

## 3. 跨边界调用链和数据流

当前是**单子项目**。下列描述 backend 内部主调用链与外部 provider 边界（分层细节见 backend ARCHITECTURE §Layers / §Data Flow）。

### 3.1 分层视图

```text
HTTP (api.py)
  → Harness (runtime/execution.py)          # stream → RunEvent；cancel
    → 能力 (runtime/agent.py + tools.py)    # Brain / middleware / SubAgent / ToolCatalog
      → 业务 Skill (skills/*/scripts/)      # save_* / generate_* + Excel
      → 集成 (integrations/)                # artifacts 路径、MinerU
    → 持久化 (runtime/resources.py + runs.py)  # ledger / checkpointer / store / CompositeBackend
```

### 3.2 主调用链（HTTP → harness → brain → tools/skills → ledger/artifacts）

```text
POST /upload  multipart files[]
  └─ 保存到 /artifacts/uploads/<cleaned-stem>_<upload-ts>(_n).ext，返回元数据

POST /runs  {messages, session_id?}
  ├─ session_id 为空 → 生成 uuid4().hex；run_id = uuid4().hex
  ├─ 进程内按 session_id 取 threading.Lock（单飞锁）；冲突 → 409
  ├─ resources.runs.create_run(run_id, session_id, input_messages_json)   # status=queued
  ├─ 起 daemon 线程 → HarnessRuntime.execute_run(messages, session_id, run_id)
  └─ 立即返回 {run_id, session_id, status:"queued"}

HarnessRuntime.execute_run(...)   # runtime/execution.py
  ├─ emit status=running；注册 RunControl
  ├─ 归一化 content blocks：
  │    ├─ text     → 原样保留
  │    └─ artifact → "Uploaded artifact: /artifacts/..."  (ARTIFACT_REFERENCE_HINT)
  ├─ brain_factory.create(resources, middleware=runtime_middlewares(), tools=tools.as_list())
  ├─ brain.stream({"messages": normalized_messages},
  │                config={"configurable":{"thread_id":session_id}},
  │                stream_mode=["messages","custom","updates"],
  │                subgraphs=True, version="v2", control=RunControl())
  │    ├─ messages → 先提取 model_usage（主 agent + subagent），再仅主 agent thinking / text_delta
  │    │             （subagent 文本按 lc_agent_name 丢弃，usage 仍计入）
  │    ├─ custom   → tool_execution（ToolTelemetry 三态 + 计时 + scope）
  │    │             + tool_progress（parse_documents / extract_archives 进度）
  │    └─ updates  → _update_events 派生 assistant_message / tool_execution
  ├─ 成功 → status=succeeded(reply=...)
  ├─ GraphDrained → status=cancelled   （POST /runs/{id}/cancel 的 RunControl drain）
  └─ 异常 / NoProgressLoop → status=failed(error=...)（真实错误透传）

GET /runs/{run_id}?after_event_id=N  → 读 runs 快照 + 增量 run_events + latest_content_event + usage
POST /runs/{run_id}/cancel            → 协作 drain；未知 404 / 终态 409 / 已 cancelling|cancelled 200 / 活跃 202
```

业务分支由同一主链中的工具调用完成（流程由对应 `SKILL.md` 指令驱动）：

```text
parse_documents (integrations/mineru.py)
  → 主 agent 回合并行 A/B 声明式 SubAgent（workflow_subagents()，各自装 middleware）
  → 每个 SubAgent 调 save_*_extraction 保存 extraction artifact
  → 必要时 extractor C 回查；A/B/C 仍冲突则主 agent 形成最小 decisions
  → 主 agent 调 generate_*_import（显式 artifact 路径 + 可选 tracking/forwarder/customs_mode/decisions）
      ├─ 成功 → 一次性 canonical / 匹配 / 计算 / Excel / 复核
      │         返回 {status:generated, canonical_artifact, artifacts, manual_checks}
      └─ 业务问题 → 返回 {code:input_problems, problems:[{source,location,issue,action}]}
                    run 结束，用户修正材料后重新显式传路径
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
| Oracle（可选） | Philips 计量单位查询 | `oracledb` thick mode；凭证与 Instant Client 齐备才查询；否则优雅降级 | `skills/philipswgqimport/scripts/tools.py` |
| LangGraph savers | checkpointer / store | `SqliteSaver`（`thread_id=session_id`）/ `SqliteStore`（`namespace=("dsagents",)`） | `runtime/resources.py` |
| DeepAgents / LangGraph runtime | 协作 drain | `RunControl` per-run → `GraphDrained` → `cancelled` | `runtime/execution.py` |

键名清单（不含值）见 [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) 与 [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)。

## 4. 接口边界

### 4.1 HTTP API（`api.py`）

| 方法 / 路径 | 行为 | 返回要点 |
|---|---|---|
| `POST /runs` | body `{messages, session_id?}`；`content` 仅 `text`/`artifact`；`ConfigDict(extra="forbid")`；同 session 活跃 run → `409` | `200 {run_id, session_id, status:"queued"}`；校验失败 `422` |
| `GET /runs/{run_id}` | query `after_event_id?`；未知 → `404` | `200 {run, events[], latest_content_event, usage}`；`usage` 从全部 `model_usage` 汇总，无模型调用时为 `null` |
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

- Brain 调用固定：`stream_mode=["messages","custom","updates"]`，`subgraphs=True`，`version="v2"`，`control=RunControl()`，`thread_id=session_id`。
- 运行时恰好两个 middleware：`ToolTelemetry`、`NoProgressMiddleware`；声明式 SubAgent **不继承**主 Agent middleware，须经 `runtime_middlewares()` 显式注入。
- 主 agent 名 `MAIN_AGENT_NAME = "dsagents-main"`；`register_harness_profile("anthropic", ...)` 禁用默认 general-purpose subagent（锁定 `deepagents==0.6.12` 无构造参数式 `harness_profile`）。
- 每 Skill 2 业务 Tool：`save_*_extraction` + `generate_*_import`；业务错误统一 `input_problems` 形状。
- 6 工具清单：`parse_documents`、`extract_archives`、`save_philips_wgq_extraction`、`generate_philips_wgq_import`、`save_tecan_extraction`、`generate_tecan_import`。

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
| `data/artifacts/downloads/` | 解析/业务产物 | MinerU、解压、Skill JSON/Excel（唯一下载名，不覆盖） |
| `data/internal/run-events/` | 大 payload spill | ledger（`max_inline_bytes=262_144`，按需创建） |
| `backend/skills/` | Skill 源（非 data） | 只读挂载为 `/skills/` |

`CompositeBackend` 路由摘要：`/memories/` → Store；`/artifacts/` 与 `/large_tool_results/` → 磁盘；`/skills/` → 只读 Skill 源；其它 → `StateBackend`。详表见 backend ARCHITECTURE。

### 5.3 事件边界

- append-only `run_events`，`event_id` 单调递增；`status` 事件投影 `runs.status/reply/error/updated_at`。
- 7 类事件；`model_usage` 为成本/缓存观测，**不计入** `latest_content_event`。
- raw v2 chunk 整体落库（可 spill）；无 TTL/归档。
- API 层 `_usage_summary` 叠加 cache hit rate 与 MiniMax-M3 tier 计价（`PRICING_AS_OF` 等硬编码于 `api.py`）；不可计价模型金额为 `null`。

### 5.4 上传 / 产物 / 路径

- 上传：`clean_filename` + `make_timestamped_name`（同请求共用 batch 时间戳；仅物理重名时加序号）。
- 虚拟路径 `/artifacts/...` 经 `integrations/artifacts.py` 解析，拒绝 `..` 越权。
- HTTP/业务 Skill 只接受显式 `/artifacts/...`；`parse_documents` 为测试/程序内保留 `allow_local`（生产风险见 §8）。
- 模板在 `/skills/<skill>/assets/`；生成时复制填充，不改仓库模板。
- 取消/失败**不回滚**已写 downloads 文件。

### 5.5 Provider 配置键（仅键名）

| 组 | 键（示例） | 消费者 |
|----|------------|--------|
| MiniMax | `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` | `runtime/agent.py` |
| MinerU | `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS`（必需，fail-fast）；`MINERU_EFFORT` 可空 | `integrations/mineru.py` |
| Oracle（可选，仅 Philips） | `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS` | `skills/philipswgqimport/scripts/tools.py` |

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
| backend 整体 / runtime / 存储 | [`docs/conventions.md`](../docs/conventions.md) → [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) + [`STRUCTURE.md`](../backend/.planning/codebase/STRUCTURE.md) → `runtime/execution.py` / `agent.py` / `runs.py` / `resources.py` |
| HTTP 契约 / 入口 | [`INTERFACES.md`](../INTERFACES.md) §1/§2 → [`INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md)（APIs）→ `api.py` |
| run 状态 / 事件 / 持久化 | backend ARCHITECTURE §Data Flow / 状态机 / 存储 → `runtime/runs.py` + `runtime/execution.py` |
| 模型流 / Brain / middleware | [`INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) LLM 节 → `runtime/agent.py` + `runtime/observability.py` |
| MinerU | INTEGRATIONS MinerU 节 → `integrations/mineru.py` |
| Oracle | INTEGRATIONS Oracle 节 + [`CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md) Oracle 条 → `skills/philipswgqimport/scripts/tools.py` |

### 7.2 跨系统接口

| 任务 | 先读 |
|------|------|
| 对外 HTTP / provider / 存储 / artifacts | [`INTERFACES.md`](../INTERFACES.md) → 本文件 §3–§5 → backend INTEGRATIONS |
| 前端 / 其它子项目 | **现状：仓库无前端子项目**；调用方应只依赖 `INTERFACES.md` 四端点与轮询语义，勿假设 SSE 或 session API |
| 部署面安全（鉴权/CORS） | 已确认缺失；见本文件 §8 与 CONCERNS Security |

### 7.3 领域 Skill / 报告生成

| 任务 | 先读 |
|------|------|
| Philips / Tecan 流程 | 对应 `skills/<skill>/SKILL.md` + `references/` → `scripts/tools.py` + `scripts/documents.py` → `backend/tests/test_*_import.py` |
| 新增 Skill | CONVENTIONS 工具静态注册约定 → `runtime/tools.py` 追加 import/注册 → `pyproject.toml` package-data → 新建 Skill 包目录 |
| Excel 模板 / 单元格 | Skill `assets/` + `scripts/documents.py`；勿改上传原件 |

### 7.4 测试与真实外部依赖

| 任务 | 先读 |
|------|------|
| 测试策略与命令 | [`backend/.planning/codebase/TESTING.md`](../backend/.planning/codebase/TESTING.md) |
| 普通本地回归（FakeBrain / mock 网络） | `cd backend` 后：`python -m tests.test_tools` / `test_run_ledger` / `test_harness` / `test_api` / `test_workflow_setup` / `test_philips_wgq_import` / `test_tecan_import`（**非 pytest**） |
| 真实集成（手动、opt-in） | `test_real_image_run`（`DSAGENTS_RUN_REAL_IMAGE_TEST=1`）、`test_real_multi_pdf_run`（`DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1`）、`test_minimax_cache_baseline`（无开关，易误跑）— **勿纳入默认门禁** |
| 仅文档变更 | `git diff --check` |

## 8. 集成风险检查清单和验证入口

提炼自 [`backend/.planning/codebase/CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md)（证据见该文档）。改动触及下列面时按项核对：

### 8.1 配置与部署

- [ ] MinerU 必需键 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS`（缺则 fail-fast）；`MINERU_EFFORT` 可空。
- [ ] MiniMax 三键在首次 `create`/调用前可用（工厂无启动期强校验）。
- [ ] Philips Oracle：`ORACLE_CLIENT_LIB_DIR` + 三凭证；缺失优雅降级，不崩溃；Tecan 不消费 Oracle。
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
| Philips / Tecan 业务 | `python -m tests.test_philips_wgq_import` / `test_tecan_import` |
| 真实模型 / MinerU | 见 TESTING.md 真实集成命令（env 守卫；默认关闭） |
| 部署 Oracle | CONCERNS Oracle 条：Instant Client 路径 + 连通性；验证 fallback 与真实查询分场景 |

## 9. 使用过的源文档索引

根级（系统边界与导航）：

- [`AGENTS.md`](../AGENTS.md)
- [`ARCHITECTURE.md`](../ARCHITECTURE.md)
- [`INTERFACES.md`](../INTERFACES.md)

子项目事实（backend 实现细节事实来源，Analysis Date: 2026-07-14）：

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

本轮（2026-07-14）在 backend 7 份事实文档同日刷新后同步改写本图：架构主结论与 2026-07-13 版一致（顶层 `api.py`+三包、run-first、四端点、7 事件、每 Skill 2 Tool、无 frontend）；增补分层调用视图、共享状态专节、按任务分桶阅读指南，以及 CONCERNS 已确认风险（SQLite WAL、上传限制、`allow_local`、协作 cancel、匿名 API 等）与验证入口表。
