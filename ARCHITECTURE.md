# ARCHITECTURE

> 系统级总览。底层实现事实以 [`backend/.planning/codebase/`](backend/.planning/codebase/) 为准；本文件只沉淀系统边界、子系统职责、理解路径与维护约定。
> 跨子项目系统视图见 [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md)。
> 本轮刷新（2026-07-16）对齐 backend 全部事实文档与 `SYSTEM_MAP`：新增固定 `philips_wgq_inbound_recognition` workflow、经 Pydantic 校验的 `run.result` 通道和独立 `runtime/middleware.py`；旧 Philips A/B/C、Excel 生成及兼容语义已删除。run-first、四 HTTP 端点、7 类事件、通用/Tecan 行为和无 SSE/session 持久化层边界保持。

## 1. 系统定位

`DsAgents` 是一个 **agent 运行时底座**：把能力做成可插拔，而不绑定具体 runner、容器、模型或工作流。整个产品收口在 `backend/` 顶层源码布局（`api.py`、`runtime/`、`integrations/`、`skills/`；绝对导入 `from runtime import ...`），发行名仍为 `dsagents`。

- **能力可插拔**：`Brain` / `BrainFactory` 是 `typing.Protocol`（`runtime/agent.py`）；middleware 实现集中在 `runtime/middleware.py`，由 agent 工厂装配；工具保持普通 callable + `ToolCatalog`；资源 / ledger 保持具体类。默认装配从 `create_harness` 进入（`DeepAgentsBrainFactory` + `default_tool_catalog()`）；本地测试用 `FakeBrainFactory` 替换。
- **工具静态注册**：`default_tool_catalog()` 静态注册 5 个工具（2 个 MinerU 通用、Philips 1 个主数据工具、Tecan 2 个业务工具）；普通 Python import，不自动扫描、无插件平台。
- **业务能力按 Skill 打包**：`skills/philipswgqinboundrecognition/` 提供固定响应合同、专用 Skill 与 `lookup_philips_wgq_master_data`；`skills/tecanimport/` 保留抽取保存与 Excel 生成。`workflow_subagents()` 当前只注册 2 个 Tecan extractor，Philips workflow 不使用 SubAgent。
- **run-first**：run 是唯一执行与查询单位；`run_events` append-only，`runs` 为事件投影快照。`session_id` 仅作 LangGraph `thread_id` 与进程内单飞锁键，不是一等持久化对象。
- **入口形态**：HTTP（`POST /runs` 立即返回 `queued`，后台 daemon 线程执行；纯轮询增量事件，无 SSE）+ 程序内组合（`AgentResources` + `create_harness(...).execute_run(...)`）；无 one-shot 单函数 API。
- **业务工作流形态**：`POST /runs` 可显式选择唯一固定 workflow；Philips 用 `ToolStrategy(PhilipsWgqRecognitionResult)` 返回结构化 JSON，Tecan 仍按 Skill 驱动 A/B 抽取与 Excel。两者均不新增业务 HTTP、状态表或跨 run 恢复接口。
- **单子项目**：仓库当前只有 `backend/` 一个产品子项目；当前源文档未确认任何前端子项目归属本仓库。

## 2. 子系统职责

| 子项目 | 目录 | 当前职责 | 边界（不做什么） |
|--------|------|----------|------------------|
| backend | `backend/` | 发行名 `dsagents`；源码顶层 `api.py`、`runtime/`、`integrations/`、`skills/`：run-first agent runtime；Brain/BrainFactory、`ToolCatalog`（5 工具）、Philips/Tecan 两个内置 Skill、2 个 Tecan SubAgent、运行时 middleware | 不提供 session/业务状态表、SSE、鉴权/CORS、跨进程锁/队列、通用工作流引擎、沙箱 / 脚本执行、插件平台、健康检查端点 |

backend 内部分层、目录与配置事实见 [`backend/.planning/codebase/ARCHITECTURE.md`](backend/.planning/codebase/ARCHITECTURE.md) 与 [`STRUCTURE.md`](backend/.planning/codebase/STRUCTURE.md)。分层调用视图与跨边界数据流见 [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §3。

## 3. 推荐理解路径

按任务类型的阅读顺序见 [`docs/reading-order.md`](docs/reading-order.md)（权威）与 [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §7（系统层速查）。

系统级导航要点：

1. 边界与定位：本文件 §1–§2、§4
2. 对外契约：[`INTERFACES.md`](INTERFACES.md)
3. 调用链与风险清单：[`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md)
4. 实现事实：`backend/.planning/codebase/` 对应 fact docs
5. 改 backend 前必读：[`docs/conventions.md`](docs/conventions.md)

## 4. 稳定目录职责（`backend/` 顶层源码）

`backend/` 安装根下源码顶层为 `api.py` 与 `runtime/`、`integrations/`、`skills/`，模块内使用绝对导入。系统级职责概览（实现细节见 [`STRUCTURE.md`](backend/.planning/codebase/STRUCTURE.md)）：

| 模块 | 系统级职责 |
|------|-----------|
| `api.py` | FastAPI HTTP 适配层（四端点）+ workflow/session 校验 + 同 session 单飞锁 + 启动恢复 + 顶层 `workflow`/`result`/`usage` |
| `runtime/agent.py` | `Brain` / `BrainFactory` Protocol、`DeepAgentsBrainFactory`、Philips ToolStrategy 路由、Tecan SubAgent 与 middleware 装配 |
| `runtime/middleware.py` | `ToolTelemetry`、`NoProgressMiddleware`、`StructuredOutputCompatibility` 与 `runtime_middlewares()` |
| `runtime/execution.py` | `HarnessRuntime.execute_run`（stream → `RunEvent`）、结构化响应捕获/复验、`create_harness`、协作 cancel |
| `runtime/observability.py` | 纯函数：chunk → `model_usage` / thinking / text / assistant payload（按 `lc_agent_name` 区分主 agent 与 subagent） |
| `runtime/resources.py` | `AgentResources` + `ResourceConfig` + `CompositeBackend`（`/memories/` `/artifacts/` `/large_tool_results/` `/skills/`） |
| `runtime/runs.py` | `SqliteRunLedger` + `RunEvent` / `RunSnapshot`；`workflow` / `result_json` 投影；fresh schema；大 payload spill |
| `runtime/tools.py` | `ToolCatalog` + `default_tool_catalog()` 静态 5 工具 |
| `integrations/artifacts.py` | `/artifacts/` 安全路径、唯一下载名、不可覆盖 JSON、上传命名 |
| `integrations/mineru.py` | `parse_documents` / `extract_archives` + `tool_progress` |
| `skills/philipswgqinboundrecognition/` | Philips 外高桥进境：`SKILL.md` + Pydantic schema + Tracking/Oracle 单一主数据 Tool |
| `skills/tecanimport/` | Tecan 帝肯进口：`SKILL.md` + references/assets + 2 业务 Tool |

固定数据目录 `backend/data/`（`ResourceConfig` 锚定，与 CWD 无关）：

| 路径 | 通道 |
|------|------|
| `dsagents_runs.db` | run ledger |
| `dsagents_checkpoints.db` | LangGraph checkpointer（`thread_id=session_id`） |
| `dsagents_store.db` | LangGraph store（`namespace=("dsagents",)`） |
| `artifacts/uploads/` | `POST /upload` 源文件 |
| `artifacts/downloads/` | MinerU / 解压 / Tecan 业务 JSON·Excel（唯一命名，不覆盖） |
| `internal/run-events/` | 大 payload spill（`max_inline_bytes=262_144`，按需创建） |

三库互不共享连接；无跨库事务。

## 5. run-first 执行模型与事件流

run 是唯一执行与查询单位。短期上下文交给 LangGraph checkpointer + `thread_id=session_id`，无 session 持久化层。

### 两个等价入口

1. **HTTP**（`api.py`）：`POST /runs` 创建并立即返回 `queued`；`GET /runs/{run_id}?after_event_id=N` 轮询增量事件；`POST /runs/{run_id}/cancel` 协作 drain。
2. **程序内**：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(messages, session_id, run_id, workflow=None)` → `Iterator[RunEvent]`。

### 主路径（概览）

```text
POST /upload → /artifacts/uploads/...
POST /runs   → 校验 workflow/session → create_run(queued, workflow) → daemon → execute_run
  → status=running；artifact block → 文本路径提示
  → brain.stream(messages/custom/updates, v2, subgraphs, RunControl)
  → messages → model_usage（含 subagent）/ thinking / text_delta（仅主 agent 文本）
  → custom   → tool_execution（ToolTelemetry）+ tool_progress（MinerU）
  → updates  → assistant_message / tool_execution / 可选 structured_response
  → Philips 再次 Pydantic 校验 → status(succeeded, result)；缺失/非法 → failed
  → 通用/Tecan succeeded(reply) | GraphDrained→cancelled | 异常/NoProgressLoop→failed
GET /runs/{id} → run + 顶层 workflow/result + events[] + latest_content_event + usage
POST .../cancel → cancelling → drain → cancelled
```

完整逐步调用链见 [`SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §3 与 backend ARCHITECTURE §Data Flow。

### 事件源

- 固定 7 类：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`。
- `status` 投影 `runs.status` / `reply` / `error` / `result_json` / `updated_at`；`workflow` 在创建 run 时写入。
- `model_usage` 为成本/缓存观测，**不计入** `latest_content_event`。
- `after_event_id` 只裁剪 `events[]`，不影响 `latest_content_event` 与 `usage`。
- 旧事件 `tool_call` / `tool_status` / `tool_result` 已删除。

### 状态机与 cancel（概览）

```text
queued → running → succeeded | failed
queued → cancelled
running → cancelling → cancelled
```

活跃 cancel：投影 `cancelling` → `request_cancel` → `GraphDrained` → `cancelled`；尚未注册 `RunControl` 时直接 `cancelled`。终态再 cancel → `409`。取消不回滚已写 artifacts，不跨进程强杀。启动 lifespan 将遗留 `queued`/`running`/`cancelling` 标为 `failed("执行已中断，请重试")`。契约细节见 [`INTERFACES.md`](INTERFACES.md) §2。

### 系统级边界摘要

| 面 | 约定 |
|----|------|
| Middleware | `runtime/middleware.py` 统一实现并由 `runtime/agent.py` 导入；每个 agent graph 使用 `ToolTelemetry`、`NoProgressMiddleware`、`StructuredOutputCompatibility`，Tecan SubAgent **不继承**主 Agent middleware，须各自注入 |
| Skill | Philips：结构化 `success|partial_success|input_problems` + 单一主数据 Tool；Tecan：2 Tool + `status=generated|input_problems`；无跨 run 状态机 |
| 工具注册 | 新增 Skill = 新包目录 + `default_tool_catalog()` 静态注册 + `package-data`；无动态 loader |
| Provider | 生产 LLM：MiniMax via Anthropic 兼容；文档解析：MinerU HTTP；可选 Oracle（仅 Philips） |

## 6. 维护约定

- **改动归属**：改 backend 代码后，**先更新** [`backend/.planning/codebase/`](backend/.planning/codebase/) 对应事实文档，**再视影响回看**本文件 / [`INTERFACES.md`](INTERFACES.md) / [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md)（见 [`AGENTS.md`](AGENTS.md)）。
- **文档分层**：根级三件套（边界与导航）→ `SYSTEM_MAP`（系统层视图）→ `docs/*.md`（约定/命令/阅读顺序）→ `backend/.planning/codebase/*`（实现事实）。四层手工保持一致。
- **系统级文档不堆实现**：表结构、完整配置键、主调用链细节归 backend 事实文档；接口形状归 `INTERFACES.md` + `INTEGRATIONS.md`。
- **包管理**：`uv`（非 pip）；`cd backend && uv sync`；禁止 `pip install -e .` 绕过 `uv.lock`。
- **包布局**：安装根 `backend/`；`py-modules=["api"]`，packages `runtime*` / `integrations*` / `skills*`；绝对顶层导入；无 `python -m backend.*`。
- **文档语言**：简体中文；标识符/路径/命令/配置键/API 名保持原文；不写密钥与私有连接串。

## 7. 当前风险（系统级）

提炼自 [`backend/.planning/codebase/CONCERNS.md`](backend/.planning/codebase/CONCERNS.md) 与 [`SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §8；改动触及下列面时回读证据原文：

- **配置完整性**：MinerU 必需键 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS`（缺则 fail-fast）；`MINERU_EFFORT` 可空；MiniMax 三键在首次 `create`/调用前可用（工厂无启动期强校验）。
- **配置文档边界**：长期文档只记键名与消费者，不抄录 `.env` 真实值。
- **并发与 cancel**：单飞锁 / `run_controls` 仅进程内；多 worker 同 `session_id` 可交错写 checkpointer；cancel 为协作 drain，非强杀，不回滚 artifacts。
- **安全面**：HTTP 匿名、无 CORS、无用户隔离；`/upload` 无大小/类型/数量限制；错误与 raw 未脱敏可经 `GET /runs` 回传；`parse_documents` 的 `allow_local` 仅宜测试/程序内使用。
- **存储与留存**：`runs.db` 无 WAL / 无 busy_timeout（高频 emit+轮询可能锁冲突）；`run_events` 与 spill 只增不删；fresh schema 无迁移，破坏性变更需整清 `backend/data/`。
- **测试门禁**：本地 7 个 assert 脚本人工选择；无 pytest/CI/lint；真实模型/MinerU/Oracle 脚本 opt-in，勿并入默认回归。
- **Oracle thick client**：Philips 可用 Oracle 补齐 Tracking 缺失的稳定字段；配置缺失、client/查询失败或未命中写入 `problems`，保留已有结果并形成 `partial_success`；Tecan 不消费 Oracle。

验证入口与按任务核对清单见 [`SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §8 与 [`TESTING.md`](backend/.planning/codebase/TESTING.md)。
