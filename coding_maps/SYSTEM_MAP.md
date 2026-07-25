# DsAgents 系统地图

> 刷新日期：2026-07-24（对齐 `backend/.planning/codebase/` Analysis Date: 2026-07-24，`last_mapped_commit: 79f97d239243d0513de93f10224eef470fffd83c`）。
> 用途：快速定位调用链、边界与按任务阅读路径。实现细节以 `backend/.planning/codebase/` 为准；系统边界以根级 `ARCHITECTURE.md` / `INTERFACES.md` 为准。

## 1. 系统目的与仓库形态

DsAgents 是**单子项目、run-first** 的 Agent 运行时底座：接收本轮消息与上传 artifact，驱动可注入的 DeepAgents Brain，将过程投影为可轮询的 run 与固定 7 类事件，并将 Philips / Tecan 渠道的最终业务 JSON 写入 `run.result`，供 OMS 消费。

| 项 | 事实 |
|----|------|
| 发行名 | `dsagents`（`backend/pyproject.toml`） |
| 产品代码 | 仅 `backend/`（`api.py` + `runtime/` + `integrations/` + `skills/`） |
| 前端子项目 | **无**；无 TypeScript / Web UI 子仓；前端/页面类任务**不适用**本仓 |
| 交互模式 | HTTP 轮询四端点；**无 SSE / session CRUD / 下载路由 / Webhook / Auth** |
| 业务 workflow | `WGQ`（飞利浦外高桥）与 `DK`（帝肯境外供应链） |
| 包管理 | `uv` + `backend/uv.lock`（勿用 `pip install -e .` 绕过 lock） |
| Python | `>=3.11,<4.0` |
| 部署假设 | **单进程** session 锁与 cancel control；多 worker 无跨进程互斥 |

```mermaid
flowchart LR
  Client["调用方"] --> Upload["POST /upload"]
  Client --> Create["POST /runs"]
  Upload --> Artifacts["/artifacts/uploads"]
  Create --> Ledger["SQLite run ledger"]
  Ledger --> Runtime["HarnessRuntime"]
  Runtime --> Brain["DeepAgents / LangGraph"]
  Brain --> Skills["Philips / Tecan Skills"]
  Skills --> Result["run.result 最终 JSON"]
  Runtime --> Events["7 类 run_events"]
  Client --> Query["GET /runs/{run_id}"]
  Query --> Ledger
```

权威源码树：`backend/api.py` + `runtime/` + `integrations/` + `skills/` + `tests/`。**勿**把 `backend/build/`、`dist/`、`*.egg-info` 等 setuptools 产物当源码；`data/`、`log/`、`.venv/` 为运行时/环境，非权威源码。

---

## 2. 子项目职责表

仓库只有 **backend** 一个产品子项目（外加根级文档与本地图）。**无前端子项目。**

| 区域 | 路径 | 职责 | 详细事实 |
|------|------|------|----------|
| HTTP 适配 | `backend/api.py` | 四端点、请求校验、session 进程内单飞、后台 daemon 线程执行、轮询投影、cancel、usage 计价、OMS best-effort 写点 | [INTEGRATIONS](../backend/.planning/codebase/INTEGRATIONS.md)、[STRUCTURE](../backend/.planning/codebase/STRUCTURE.md) |
| 运行时核心 | `backend/runtime/` | Brain 装配、stream→事件、middleware、ToolCatalog、三库资源、run ledger、OMS JSONL | [ARCHITECTURE](../backend/.planning/codebase/ARCHITECTURE.md) |
| 外部集成 | `backend/integrations/` | `/artifacts` 路径、MinerU HTTP、JSON artifact 读写 | [INTEGRATIONS](../backend/.planning/codebase/INTEGRATIONS.md) |
| 业务 Skill | `backend/skills/` | 渠道合同、Philips/Tecan 下划线 Skill 包（资源 + 代码）、主数据/XLSX/finalizer | [STRUCTURE](../backend/.planning/codebase/STRUCTURE.md)、[CONVENTIONS](../backend/.planning/codebase/CONVENTIONS.md) |
| 本地门禁 | `backend/tests/` | 可执行 assert 脚本（`python -m tests.*`，**非 pytest**） | [TESTING](../backend/.planning/codebase/TESTING.md) |
| 实现事实文档 | `backend/.planning/codebase/` | 架构/结构/栈/集成/约定/测试/风险（Analysis Date: **2026-07-24**） | 本目录七文件 |
| 系统文档 | 仓库根 `ARCHITECTURE.md`、`INTERFACES.md`、`AGENTS.md`、`docs/*` | 边界决策、接口合同、导航与业务 PRD | 根级与 `docs/` |
| 系统地图 | `coding_maps/SYSTEM_MAP.md` | 调用链、边界与按任务阅读（本文） | — |

### 一页总览（模块入口）

| 区域 | 入口 | 当前职责 |
|------|------|----------|
| HTTP | `backend/api.py` | 四端点、run/session 校验、后台执行、轮询、cancel、OMS 索引、MiniMax-M3 usage 估价 |
| 执行 | `backend/runtime/execution.py` | `HarnessRuntime.execute_run`、stream→七类 events、结果投影、协作 cancel |
| Agent | `backend/runtime/agent.py` | `Brain`/`BrainFactory` Protocol、`DeepAgentsBrainFactory`、WGQ / DK ToolStrategy、denylist、关闭默认子代理 |
| Middleware | `backend/runtime/middleware.py` | workflow recovery、telemetry、loop 检测、thinking 兼容、memory |
| 工具目录 | `backend/runtime/tools.py` | 静态 **5** 工具 `ToolCatalog` |
| 资源 / 三库 | `backend/runtime/resources.py` | `AgentResources`、`CompositeBackend`、路径锚定 `backend/`、`/skills/` 连字符别名 |
| ledger | `backend/runtime/runs.py` | runs 投影 + append-only events |
| 可观测抽取 | `backend/runtime/observability.py` | 纯函数 chunk 抽取（无 I/O）；`MAIN_AGENT_NAME = "dsagents-main"` |
| OMS 旁路 | `backend/runtime/oms_log.py` | `run_created` JSONL best-effort |
| artifacts | `backend/integrations/artifacts.py` | 虚拟路径、上传命名、JSON 读写 |
| MinerU | `backend/integrations/mineru.py` | `parse_documents` / `extract_archives` |
| 业务合同 | `backend/skills/channel_contract.py` | 共享 24 字段 `OrderItem`、problems、outcome |
| Philips（WGQ） | `backend/skills/philips_wgq_inbound_recognition/` | workflow、Skill、schema、Tracking / 共享 Oracle lookup |
| Tecan（DK） | `backend/skills/tecan_import/` | workflow、Skill/references、XLSX inspection、普通请求兼容 finalizer（无 Excel） |

依赖方向（单向）：`api → runtime → integrations / skills`。Skill 工具可依赖 `integrations.artifacts`，不反向调用 HTTP。`typing.Protocol` **只**用于 `Brain` / `BrainFactory`；工具 = callable + `ToolCatalog`；资源与 ledger = 具体类。

---

## 3. 调用链与数据流

### 3.1 HTTP：upload → runs → poll → cancel

```text
POST /upload
  → backend/data/artifacts/uploads/<stem>_<timestamp>.<ext>
  → 响应 file_path = /artifacts/uploads/...

POST /runs {workflow?, session_id?, messages[]}
  → api.py 校验 RunRequest（extra=forbid）
  → 进程内 session 单飞锁（冲突 → 409 + active_run_id）
  → SqliteRunLedger.create_run(status=queued) + status 事件
  → OMS JSONL best-effort（失败不阻塞）
  → threading.Thread(daemon) → HarnessRuntime.execute_run
      → emit status=running
      → BrainFactory.create(middleware, tools, workflow)
      → brain.stream(messages, thread_id=session_id, control=RunControl)
      → messages/custom/updates → 7 类 run_events
      → workflow structured_response → runtime finalizer → run.result
      → status=succeeded | failed | cancelled
  → finally 释放 session 锁

GET /runs/{run_id}?after_event_id=
  → {run, workflow, result, events, latest_content_event, usage}

POST /runs/{run_id}/cancel
  → cancelling + RunControl.request_drain（协作式，不强杀外部 HTTP/Oracle）
  → 尚无 control（queued / 未进入 execute_run）→ 直接 cancelled
  → 活跃 drain 后 GraphDrained → cancelled
```

状态机：

```text
queued → running → succeeded | failed | cancelled
queued → cancelled
running → cancelling → cancelled
```

启动 lifespan：`fail_incomplete_runs("执行已中断，请重试")` 清理残留 `queued` / `running` / `cancelling`（不自动续跑）。

### 3.2 程序内 harness（不经 HTTP / 不写 OMS）

```python
with AgentResources(...) as resources:
    harness = create_harness(resources)
    for event in harness.execute_run(messages, session_id, run_id, workflow=None):
        ...
```

- HTTP：`api:app` / `create_app()` + `uvicorn`（示例：`uv run uvicorn api:app --host 0.0.0.0 --port 8500`）。
- 程序内调用**不**写 OMS 旁路索引。
- 业务 JSON 的唯一读取路径仍是 ledger 中的 `run.result`。

### 3.3 stream → 事件投影

`stream_mode=["messages", "custom", "updates"]`，`version="v2"`，`subgraphs=True`：

| stream kind | 产出 |
|-------------|------|
| `messages` | `model_usage`（含 subagent）、`thinking`、`text_delta`（主 Agent；subagent 文本过滤） |
| `custom` | `tool_progress`（MinerU/解压）或 `tool_execution`（ToolTelemetry 等） |
| `updates` | `tool_execution`（tool_calls）、`assistant_message`；捕获 workflow `structured_response` 与普通 Tecan finalizer ToolMessage |

`artifact` 内容块在进入 Brain 前归一为带路径提示的文本。生产 `subagents=[]`；FakeBrain 可模拟 subagent 元数据以锻炼过滤，**不**表示生产有业务 SubAgent。

### 3.4 终态业务结果路径

| 路径 | 触发 | 投影 | 缺结果时 |
|------|------|------|----------|
| WGQ（Philips） | `workflow=WGQ` | `ToolStrategy(PhilipsWgqRecognitionResult)` → `structured_response` → runtime finalizer → `run.result` | run `failed` |
| DK（Tecan） | `workflow=DK` | `ToolStrategy(TecanOverseasRecognitionResult)` → `structured_response` → runtime finalizer → `run.result` | run `failed` |
| 通用 Tecan 请求 | 无 workflow + 明确 Skill 请求 | Tecan finalizer ToolMessage → runtime finalizer | 可 `succeeded` 且 `result=null`（普通阅读合法） |
| 普通阅读 | 无 workflow、无 finalizer | `result` 可为 `null` | 仍 `succeeded` |

`run.result` 是 OMS 消费的**唯一**业务通道；`reply` 仅为自然语言摘要。`input_problems` 是合法业务 outcome，run 仍为 `succeeded`。

### 3.5 渠道路径（同票单一 run）

```text
WGQ workflow
  → /skills/philips-wgq-inbound-recognition/SKILL.md
  → references/freight-forwarders.md（DHL / DSV / FedEx / UPS / 康捷空）
  → parse_documents / inspect_supply_chain_workbooks
  → 唯一 Tracking 时 lookup_philips_wgq_master_data
  → denylist 排除 Tecan finalizer
  → ToolStrategy(PhilipsWgqRecognitionResult) → runtime finalizer → run.result

DK workflow
  → /skills/tecan-import/SKILL.md + references/
  → parse_documents / inspect_supply_chain_workbooks
  → 唯一 12NC 时 lookup_philips_wgq_master_data（不传 Tracking）
  → 同票归集与字段裁决
  → ToolStrategy(TecanOverseasRecognitionResult) + Recovery
  → 同一 denylist 排除 Tecan finalizer
  → runtime finalizer → run.result
```

两渠道 `header` 独立，`items[]` 共用完整 **24** 字段；不输出 `shipment`、Excel、候选噪声。无跨 run 业务状态表、无生产业务 SubAgent。同票归集在**单一 run** 内完成。

### 3.6 Agent 装配

```text
AgentResources
  ├─ CompositeBackend（/artifacts /skills /memories /large_tool_results + StateBackend）
  ├─ checkpointer（thread_id=session_id → dsagents_checkpoints.db）
  ├─ store（/memories/ → dsagents_store.db，namespace ("dsagents",)）
  └─ runs（dsagents_runs.db + 大事件外置）

DeepAgentsBrainFactory
  ├─ general-purpose subagent disabled；subagents=[]
  ├─ static ToolCatalog（5）
  ├─ WGQ: ToolStrategy + WAG_WORKFLOW_PROMPT + denylist + Recovery
  ├─ DK: ToolStrategy + DK_WORKFLOW_PROMPT + denylist + Recovery
  └─ generic: Skill-driven，structured_schema=None
```

Skill 虚拟路径：下划线源码包 → 连字符 `/skills/` 别名（`philips_wgq_inbound_recognition` → `/skills/philips-wgq-inbound-recognition/`；`tecan_import` → `/skills/tecan-import/`）。`/skills/**` 写拒绝。

### 3.7 Middleware 顺序

每次 `create` 新建实例（洋葱模型；Recovery 列表靠前 → `after_model` 最后执行）：

| 顺序 | middleware | 适用范围 |
|------|------------|----------|
| 1 | `StructuredOutputRecovery` | WGQ / DK（各自 `structured_schema`）；`after_model` + `jump_to` |
| 2 | `ToolTelemetry` | 所有 Agent |
| 3 | `NoProgressMiddleware` | 所有 Agent（同参连续 3 次 → `NoProgressLoop` → failed） |
| 4 | `StructuredOutputCompatibility` | 所有；仅 ToolStrategy 时关 thinking |
| 5 | `MemoryMiddleware` | 有 memory 的主 Agent（约 5 个 middleware） |

无 schema 时约 4 个（无 Recovery）。

**StructuredOutputRecovery 硬约束**（详见 [CONVENTIONS](../backend/.planning/codebase/CONVENTIONS.md) / [CONCERNS](../backend/.planning/codebase/CONCERNS.md)）：

- `can_jump_to` 必须含 `"model"` 与 **`"end"`**
- 耗尽必须显式 `jump_to: "end"`，禁止只返回 `None`（默认 `max_retries=2`）
- 空 data 壳：同回合 `tool_call_id` 恢复或 schema 纠错；空壳耗尽 → 当前 schema 的 all-null `input_problems` + runtime problem（**技术兜底**，非业务模板）
- 其它失败耗尽 → 无 `structured_response` → harness `failed`
- 普通 run 不走此路径

### 3.8 工具地图

| 工具 | 归属 | 作用 |
|------|------|------|
| `parse_documents` | MinerU | 解析 PDF 等 → downloads JSON/ZIP |
| `extract_archives` | 本地 | ZIP artifact 解压 |
| `lookup_philips_wgq_master_data` | Philips scripts（WGQ / DK 共享） | WGQ Tracking XLSX + Oracle 唯一补齐 |
| `inspect_supply_chain_workbooks` | Tecan 包（共享） | 只读 XLSX → JSON artifact |
| `finalize_tecan_overseas_recognition` | Tecan | Pydantic 校验并返回终态 JSON 字符串 |

WGQ / DK 均以同一 **denylist** 排除 `finalize_tecan_overseas_recognition`，并保留共享 MinerU / XLSX / 12NC lookup。**禁止**业务-only allowlist（保护 `/memories/AGENTS.md` 中 ZIP 指引）。Tecan finalizer 仅供无 workflow 的明确请求；Tecan 不输出 Excel，`openpyxl` 只读用户材料。

---

## 4. 接口边界（四 HTTP 端点、无 SSE）

完整请求/响应字段见根级 [INTERFACES.md](../INTERFACES.md)。

| 端点 | 要点 |
|------|------|
| `POST /upload` | multipart `files` → `{files:[{file_path,name,mime_type,size}]}`；路径形如 `/artifacts/uploads/...` |
| `POST /runs` | `{workflow?, session_id?, messages[]}` → `{run_id, session_id, status:"queued"}`；后台执行 |
| `GET /runs/{run_id}` | 可选 `after_event_id`；返回 `run` 快照、顶层 `result`（= `run.result`）、`events`、`latest_content_event`、`usage` |
| `POST /runs/{run_id}/cancel` | 协作 cancel；活跃 → **202** `cancelling`；终态再 cancel → **409**；未知 → **404** |

约束摘要：

- `messages[]`：`{role, content:[{type:"text",text}|{type:"artifact",path}]}`，`extra="forbid"`；旧 `{message:"..."}` 不支持
- `workflow` 仅允许 `WGQ`、`DK` 或省略；与客户端 `session_id` **互斥**（workflow 强制服务端新 session → 否则 **422**）
- 同 `session_id` 并发第二跑 → **409** + `active_run_id`（进程内锁）
- **无** Auth 中间件、**无** Webhook、**无** session CRUD、**无** 下载端点、**无** SSE
- 建议 `after_event_id` 增量拉取；终态业务数据读顶层 `result`，不要只看 `reply`

### 渠道最终 JSON 形状

```json
{
  "outcome": "success | partial_success | input_problems",
  "data": {"header": {}, "items": []},
  "problems": [{"source": "", "location": "", "issue": "", "action": ""}]
}
```

| outcome / 条件 | run.status |
|----------------|------------|
| `success` / `partial_success` / `input_problems` | `succeeded` |
| WGQ / DK 缺 structured_response / 运行时异常 / NoProgress | `failed` |
| 用户 cancel + GraphDrained | `cancelled` |

`items[]` 每行完整 **24** 字段；未知 `null`。Philips / Tecan header 字段集不同（见 [INTERFACES.md](../INTERFACES.md)）。`validate_channel_outcome` 会校正 success/partial 与缺失字段的一致性。

---

## 5. 存储 / 事件 / artifacts / OMS / provider 边界

### 5.1 状态归属（不要混用）

| 层 | 职责 | 非职责 |
|----|------|--------|
| `runs` + `run_events` | 对外执行终态与可观测投影 | 不存业务中间候选表 |
| LangGraph checkpointer | 短期图上下文（`thread_id=session_id`） | 不是 OMS 合同源 |
| LangGraph store | `/memories/` 手册与跨 run 记忆 | 不是 run 查询 API |
| `session_id` | thread_id + 进程内单飞 | 无 CRUD、非业务归档资源 |
| OMS JSONL | 运维旁路 `run_created` 索引 | 非第 8 类 event、无查询 API、不含 `run.result` |

### 5.2 三 SQLite（物理分离）

| 库 | 默认路径 | 用途 |
|----|----------|------|
| Run ledger | `backend/data/dsagents_runs.db` | runs 快照 + append-only events；大 payload → `backend/data/internal/run-events/` |
| Checkpoints | `backend/data/dsagents_checkpoints.db` | `SqliteSaver` |
| Store | `backend/data/dsagents_store.db` | `SqliteStore`，namespace `("dsagents",)` |

时间戳：ledger 与 OMS 统一 **UTC+8** `YYYY-MM-DD HH:MM:SS`。无自动 schema migration；三库连接不共享。`ResourceConfig` 路径锚定 `backend/`（与 CWD 无关）。

### 5.3 固定 7 类事件

`status` · `tool_execution` · `tool_progress` · `thinking` · `text_delta` · `assistant_message` · `model_usage`

- `run_events` append-only；`runs` 只保存投影。
- 超过 `max_inline_bytes`（默认 256KiB / 262144）的大 payload 落盘到 `data/internal/run-events/`。
- `latest_content_event` 排除 `status` 与 `model_usage`。

### 5.4 Artifacts

- 根：`backend/data/artifacts/`（`uploads/`、`downloads/`）
- 跨层唯一虚拟路径：`/artifacts/...`（禁止 `..`；默认仅接受该前缀）
- MinerU 解析侧 `allow_local=True` 为例外（见风险）
- Agent 视图：`FilesystemBackend` 挂 `/artifacts/` 与 `/large_tool_results/`；`/skills/**` 写拒绝
- 上传、JSON artifact、解压/解析输出均经 `integrations.artifacts`

### 5.5 Provider / 出站边界

| 集成 | 入口 | 失败策略 |
|------|------|----------|
| MiniMax（Anthropic 兼容） | `DeepAgentsBrainFactory`；`MINIMAX_*` | 模型/流异常 → run `failed` |
| MinerU | `integrations/mineru.py`；`MINERU_*`（`requests` 客户端） | 工具异常/超时；可投影 progress |
| Oracle（可选） | WGQ / DK 共享 lookup；`ORACLE_*` + Windows 随仓库 client / 可选 `ORACLE_CLIENT_LIB_DIR` | problems + null 字段，不拖垮已证实结果 |
| OMS JSONL | `runtime/oms_log.py` → `backend/log/oms_log.log` | best-effort，`except: pass` |

- OMS 在 HTTP `create_run` **成功之后**写 `event=run_created` 行（`run_id`、`session_id`、`workflow`、从 messages 抽取的 artifact `files[{name,path}]`）；**不是** `run_events`、**无**查询 API、**不含** prompt/thinking/`run.result`。
- 无第二生产 LLM 接线；无 Auth / Webhooks。
- API 层可对 `MiniMax-M3` 聚合 usage 并做 CNY 趋势估价（`PRICING_AS_OF` 见源码；非账单）；未知模型金额为 null。

详见 [INTEGRATIONS](../backend/.planning/codebase/INTEGRATIONS.md)、[STACK](../backend/.planning/codebase/STACK.md)。

### 5.6 依赖与归属规则（摘要）

| 规则 | 说明 |
|------|------|
| Protocol 仅 Brain | 工具 / 资源 / ledger 不用 Protocol |
| 工具静态注册 | `default_tool_catalog()` 五行；禁止目录扫描 |
| workflow 收窄 = denylist | 禁止业务-only allowlist |
| Skill 单目录 | 下划线可 import 包内含 `SKILL.md`/references、schema、scripts；运行时以连字符 `/skills/` 别名暴露；更新 package-data 与 skills 路由 |
| 终态只写 `run.result` | 不从 `reply`/thinking/候选/Excel 推断 OMS 数据 |
| 同票单一 run | 不新增消息/任务状态表或业务 middleware |
| 横切 vs 业务校验 | Philips recovery 用 middleware；Tecan 用 finalizer 工具 |
| 源码权威 | 勿把 `backend/build/` 当源码 |
| 文档同步 | 改 backend → 先 codebase 事实 → 再根级架构/接口/本地图 → `git diff --check` |

分层放置新代码：见 [STRUCTURE — 放置新代码](../backend/.planning/codebase/STRUCTURE.md)。

---

## 6. 按任务阅读指南

> **无前端子项目**：所有产品改动落在 `backend/`；UI/页面/组件库类需求不适用本仓。前端任务请明确为外部调用方集成，而非本仓实现。

### 6.1 backend 业务（Philips / Tecan / 合同）

1. [docs/channel-supply-chain-json-prd.md](../docs/channel-supply-chain-json-prd.md)（业务合同）
2. 本文 §3.5 / §3.8 与 [INTERFACES.md](../INTERFACES.md) 渠道 JSON 节
3. `backend/skills/channel_contract.py`
4. Philips：`backend/skills/philips_wgq_inbound_recognition/SKILL.md`、`schema.py`、`scripts/tools.py`（及 `references/freight-forwarders.md`）
5. Tecan：`backend/skills/tecan_import/SKILL.md` + `references/`、`schema.py`、`scripts/tools.py`
6. 事实补充：[ARCHITECTURE](../backend/.planning/codebase/ARCHITECTURE.md) 渠道合同节、[CONVENTIONS](../backend/.planning/codebase/CONVENTIONS.md)

### 6.2 backend API / HTTP

1. [INTERFACES.md](../INTERFACES.md)
2. `backend/api.py`
3. [INTEGRATIONS](../backend/.planning/codebase/INTEGRATIONS.md) HTTP 表面
4. 验证：`python -m tests.test_api`

### 6.3 存储 / ledger / 事件

1. 本文 §5
2. `backend/runtime/runs.py`、`resources.py`、`oms_log.py`
3. [ARCHITECTURE](../backend/.planning/codebase/ARCHITECTURE.md) 执行数据流 + 七类事件
4. 验证：`python -m tests.test_run_ledger`

### 6.4 runtime / Brain / middleware / 工具

1. `backend/runtime/agent.py`、`execution.py`、`middleware.py`、`tools.py`
2. [ARCHITECTURE](../backend/.planning/codebase/ARCHITECTURE.md) Agent/middleware/工具表
3. [CONVENTIONS](../backend/.planning/codebase/CONVENTIONS.md) Recovery 与 denylist
4. [CONCERNS](../backend/.planning/codebase/CONCERNS.md) Recovery / denylist 脆弱点
5. 验证：`test_harness`、`test_workflow_setup`、`test_tools`

### 6.5 跨系统接口（MinerU / Oracle / LLM / OMS / artifacts）

1. [INTEGRATIONS](../backend/.planning/codebase/INTEGRATIONS.md)
2. [STACK](../backend/.planning/codebase/STACK.md) 环境变量表
3. `backend/integrations/mineru.py`、`artifacts.py`、共享 Oracle 工具
4. 风险：[CONCERNS](../backend/.planning/codebase/CONCERNS.md)

### 6.6 领域流程（渠道供应链）

1. 根级 [ARCHITECTURE.md](../ARCHITECTURE.md)「渠道供应链业务设计」
2. 本文 §3.5
3. Skill 资源 + schema + PRD
4. 真实样例：opt-in `tests.test_real_*`（非默认门禁）

### 6.7 文档与导航

1. [AGENTS.md](../AGENTS.md)（全局硬约束入口）
2. [docs/reading-order.md](../docs/reading-order.md)、[docs/conventions.md](../docs/conventions.md)、[docs/commands.md](../docs/commands.md)
3. [docs/backend.md](../docs/backend.md)、[docs/project-overview.md](../docs/project-overview.md)
4. 改文档后：`git diff --check`

### 6.8 新增 Skill 检查顺序

1. 单一下划线 Skill 包（`SKILL.md` / references / schema / scripts）
2. `runtime/tools.py` 静态注册
3. `runtime/resources.py` 增加 `/skills/<hyphen-name>/` 路由
4. WGQ / DK denylist 是否需排除**其他业务**新工具
5. `pyproject.toml` package-data
6. tests + codebase 事实 + 本地图/INTERFACES（若 HTTP 边界变）

### 6.9 前端 / UI 任务

**本仓库无前端子项目。** 若任务是调用方 UI 对接 DsAgents，只读本文 §3–§4 与 [INTERFACES.md](../INTERFACES.md) 的轮询合同；不在本仓新增页面或 TypeScript 包。

---

## 7. 集成风险与验证入口

### 7.1 改动前速查

| 主题 | 必守 | 验证 |
|------|------|------|
| Recovery / ToolStrategy | `can_jump_to` 含 `end`；耗尽显式 `jump_to`；空壳 `tool_call_id` | `python -m tests.test_harness` |
| 工具表 / denylist | 仍只 denylist 其他业务工具；禁止 allowlist | `test_tools` + `test_workflow_setup` |
| HTTP / 锁 / cancel | 不引入 SSE/session API；单进程锁语义 | `test_api` + `test_run_ledger` |
| 渠道 JSON 24 字段 | 同步 `channel_contract`、两 schema、Skill、recovery skeleton、两侧测试 | Philips + Tecan 测试模块 |
| Oracle / MinerU / 路径 | 优雅降级；密钥不进文档；注意 `artifacts_root` 与 `ResourceConfig` 对齐 | [CONCERNS](../backend/.planning/codebase/CONCERNS.md)、`test_tools` |
| OMS | best-effort；失败不阻塞 queued | `test_api` |
| 多 worker | 当前**不**支持跨进程 session 互斥与 cancel | 部署单 worker 假设 |
| Skill 资源 | package-data + 连字符挂载与 `SKILL.md` name 一致 | `test_workflow_setup` |

### 7.2 其它已知风险（摘要）

- Cancel 不能强杀已发出的 MinerU/Oracle/模型调用；可能长时间 `cancelling`
- WGQ / DK 未产出 structured_response → `failed`；通用路径未调 finalizer 可 `succeeded` + `result=null`，客户端必须检查 `result`
- 空壳 all-null 是 **runtime 技术兜底**，不是业务 `partial_success` 模板
- 上传无配额、HTTP 无鉴权 → 依赖内网/网关
- daemon 线程 + 启动 `fail_incomplete_runs`：崩溃 run 不自动续跑
- `artifacts_root()` 与 API 注入 `ResourceConfig` 可能脱节（自定义 `data_dir` 时）
- MinerU `allow_local=True` 与 ZIP 解压路径净化（ZipSlip）见 [CONCERNS](../backend/.planning/codebase/CONCERNS.md)
- Windows checkout 的 `backend/.oracle/instantclient/instantclient_19_31` 为 Oracle thick 默认路径（检测 `oci.dll`）；可用 `ORACLE_CLIENT_LIB_DIR` 覆盖；缺客户端或配置时优雅降级为 problems/null
- 进程内 `session_locks` 只增不减（长生命周期内存缓慢增长）；session 单飞与 cancel 不可水平扩展
- 双重 `load_dotenv(backend/.env)`（`agent.py` / `mineru.py`）；`httpx2` 声明但产品源码未直接 import
- 轮询客户端应使用 `after_event_id`，避免短间隔全量拉 events

完整列表见 [CONCERNS](../backend/.planning/codebase/CONCERNS.md)。

### 7.3 本地验证入口

```powershell
cd backend
uv sync
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
# 仓库根目录
git diff --check
```

真实模型 / MinerU / Oracle / 外部 HTTP：opt-in `tests.test_real_*`（`DSAGENTS_RUN_REAL_*` 等）与 [TESTING](../backend/.planning/codebase/TESTING.md) / [docs/commands.md](../docs/commands.md)。

启动服务（运维）：

```powershell
cd backend
uv run uvicorn api:app --host 0.0.0.0 --port 8500
```

---

## 8. 源文档索引

本轮刷新（**2026-07-24**）对齐并引用下列源（路径相对仓库根）：

| 优先级 | 路径 |
|--------|------|
| 1 | `AGENTS.md` |
| 2 | `ARCHITECTURE.md` |
| 3 | `INTERFACES.md` |
| 4 | `backend/.planning/codebase/ARCHITECTURE.md`（Analysis Date: 2026-07-24） |
| 4 | `backend/.planning/codebase/STRUCTURE.md` |
| 4 | `backend/.planning/codebase/STACK.md` |
| 4 | `backend/.planning/codebase/INTEGRATIONS.md` |
| 4 | `backend/.planning/codebase/CONVENTIONS.md` |
| 4 | `backend/.planning/codebase/TESTING.md` |
| 4 | `backend/.planning/codebase/CONCERNS.md` |
| 5 | `coding_maps/SYSTEM_MAP.md`（上一版 2026-07-22，保留仍正确的结构与表述后覆盖刷新） |

相关但不作为本轮逐字源的导航文档：`docs/conventions.md`、`docs/commands.md`、`docs/reading-order.md`、`docs/channel-supply-chain-json-prd.md`、`docs/backend.md`、`docs/project-overview.md`。
