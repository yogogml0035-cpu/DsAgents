# ARCHITECTURE — backend（dsagents）

> Analysis Date: 2026-07-22。事实来源是 `backend/api.py`、`runtime/`、`integrations/` 与 `skills/`；不以历史构建产物（`backend/build/`、`backend/dist/`、`dsagents.egg-info/`）为权威源。

## Pattern Overview

DsAgents 是**单子项目** Agent 运行时底座：产品代码在 `backend/`，发行名 `dsagents`，以可注入 Brain、执行器、工具和资源承载通用文档处理与 Philips / Tecan 内置 Skill。**无前端子项目**。

核心模式：

| 概念 | 说明 |
|------|------|
| **run-first** | `run` 是唯一执行与查询单位；`run_events` append-only，`runs` 为投影快照 |
| **轮询 HTTP** | 仅四端点；无 SSE、无 session CRUD API |
| **可注入 Brain** | `Brain` / `BrainFactory` 为唯一 `typing.Protocol`；默认 `DeepAgentsBrainFactory` |
| **静态工具表** | 五工具经 `ToolCatalog` 静态注册；workflow 用 **denylist** 收窄 |
| **渠道终态 JSON** | Philips ToolStrategy / Tecan finalizer → `run.result`；业务问题走 `input_problems`，run 仍 `succeeded` |

不存在的边界（刻意不做）：

- session 列表 / 创建 / 删除 API
- SSE 或 WebSocket 推送
- 业务 SubAgent / 任务状态表 / 业务-only middleware
- Tecan Excel 模板或生成器
- 多 worker 跨进程互斥（session 单飞与 `run_controls` 均为进程内）

## 分层与依赖方向

```text
api.py                          HTTP 适配层
  └── runtime/                  运行时核心
        ├── agent.py            Brain 装配
        ├── execution.py        run 执行与 stream 投影
        ├── middleware.py       横切 middleware
        ├── tools.py            ToolCatalog
        ├── resources.py        三库 + CompositeBackend
        ├── runs.py             SqliteRunLedger
        ├── observability.py    纯抽取（无 I/O）
        └── oms_log.py          OMS JSONL（旁路）
              ├── integrations/ 外部系统与 artifact
              │     ├── artifacts.py
              │     └── mineru.py
              └── skills/       业务 Skill + 渠道合同
                    ├── channel_contract.py
                    ├── philips_wgq_inbound_recognition/   Skill 资源 + 包
                    └── tecan_import/                      Skill 资源 + 包
```

| 层 | 位置 | 职责 |
|----|------|------|
| HTTP | `api.py` | 请求校验、上传、创建 run、后台线程、轮询、cancel、usage 计价层 |
| 运行时 | `runtime/` | Brain 装配、stream 投影、middleware、ledger、资源、工具目录 |
| 集成 | `integrations/` | `/artifacts` 路径、MinerU HTTP、JSON artifact 读写 |
| 业务 Skill | `skills/` | 流程提示（`SKILL.md`）、schema、主数据 / XLSX / finalizer 工具 |
| 测试 | `tests/` | 可执行 assert 脚本（非 pytest） |

依赖单向：`api → runtime → integrations / skills`。Skill 工具可依赖 `integrations.artifacts`，不反向调用 HTTP。`Brain` / `BrainFactory` 是仅有的 `Protocol` 注入点；工具是普通 callable；资源与 ledger 用具体类。

## 执行数据流

### HTTP 四端点

| 方法 | 路径 | 行为 |
|------|------|------|
| `POST` | `/upload` | 多文件写入 `data/artifacts/uploads/`，返回虚拟路径 `/artifacts/uploads/...` |
| `POST` | `/runs` | 校验 `RunRequest` → 创建 `queued` run → best-effort OMS 索引 → 后台线程 `execute_run` → 立即返回 `{run_id, session_id, status: "queued"}` |
| `GET` | `/runs/{run_id}` | 投影快照 + 增量 `events`（`after_event_id`）+ `latest_content_event` + `usage` |
| `POST` | `/runs/{run_id}/cancel` | `cancelling` → `RunControl.request_drain`；无 control 则直接 `cancelled` |

`RunRequest` 约束：

- `workflow: Literal["WAG", "DK"] | None` — `WAG` 为飞利浦外高桥，`DK` 为帝肯境外供应链
- `workflow` 与客户端 `session_id` 互斥（workflow run 必须服务端生成新 session）
- `messages[].content` 为 `text` | `artifact` 判别联合；`extra="forbid"`

### run 生命周期

```text
POST /upload
  → data/artifacts/uploads/<stem>_<timestamp>.<ext>
  → 响应 file_path = /artifacts/uploads/...

POST /runs
  → 进程内 session 单飞锁（同 session_id 冲突 → 409）
  → SqliteRunLedger.create_run(status=queued) + status 事件
  → append_run_created_log（best-effort，失败不阻塞）
  → threading.Thread → HarnessRuntime.execute_run
      → emit status=running
      → BrainFactory.create(middleware, tools, workflow)
      → brain.stream(messages, thread_id=session_id, control=RunControl)
      → 七类事件写入 run_events；终态投影 runs
      → succeeded | failed | cancelled
  → finally 释放 session 锁

GET /runs/{run_id}
  → run 快照、workflow、result、events、latest_content_event、usage
```

状态机：

```text
queued → running → succeeded | failed | cancelled
queued → cancelled
running → cancelling → cancelled
```

启动时 `fail_incomplete_runs("执行已中断，请重试")` 清理残留 `queued` / `running` / `cancelling`。

### stream → 事件投影

`HarnessRuntime.execute_run` 以 `stream_mode=["messages", "custom", "updates"]` 消费 LangGraph：

| stream kind | 产出事件 |
|-------------|----------|
| `messages` | `model_usage`（含 subagent）、`thinking`、`text_delta`（主 Agent 文本；subagent 文本过滤） |
| `custom` | `tool_progress`（`parse_documents` / `extract_archives` 进度）或 `tool_execution`（`ToolTelemetry` 等） |
| `updates` | `tool_execution`（tool_calls）、`assistant_message`；同时捕获 `structured_response` 与 Tecan finalizer ToolMessage |

终态业务结果：

- **WAG**：必须从 `updates` 得到 `structured_response`，再 `PhilipsWgqRecognitionResult.model_validate` → `run.result`；缺失则 `failed`
- **DK**：必须接受名为 `finalize_tecan_overseas_recognition` 的 ToolMessage JSON → `TecanOverseasRecognitionResult` → `run.result`；缺失则 `failed`
- **通用 Tecan 请求**：若调用 finalizer 同样投影结果；未调用时可作为普通阅读 run 成功且 `result=null`
- **普通阅读 run**：`result` 可为 `null`，run 仍 `succeeded`

`artifact` 内容块在进入 Brain 前归一为带路径提示的 `text`（`ARTIFACT_REFERENCE_HINT`）。

## 七类事件

固定事件类型（不可随意扩展为业务状态通道）：

| type | 来源 | 语义 |
|------|------|------|
| `status` | ledger `emit_run_status` | 状态机投影；终态可带 `reply` / `error` / `result` |
| `tool_execution` | `ToolTelemetry` custom + updates tool_calls | 工具开始/完成/错误或调用意图 |
| `tool_progress` | MinerU 工具 `get_stream_writer` | 解析/解压进度 |
| `thinking` | messages 流 thinking 块 | 模型思考增量 |
| `text_delta` | messages 流文本 | 主 Agent 流式文本 |
| `assistant_message` | updates 终态助手消息 | 完整助手文本（可附 thinking） |
| `model_usage` | messages `usage_metadata` | 单次模型调用 token；API 层聚合并可选计价 |

大 payload 超过 `max_inline_bytes`（默认 256KiB）时落盘到 `data/internal/run-events/`，DB 仅存引用。

`latest_content_event` 排除 `status` 与 `model_usage`，取最新内容类事件。

## 渠道供应链 JSON 合同

共享合同在 `skills/channel_contract.py`：

### 共用结构

- **`OrderItem`**：固定 **24 字段**，Philips 与 Tecan 共用
  `invoice_number`, `invoice_date`, `so_item`, `product_id`, `new_or_used`, `chinese_name`, `specification`, `quantity`, `unit`, `currency`, `unit_price`, `total_price`, `trade_terms`, `origin_country`, `customs_code`, `declaration_elements`, `legal_quantity_1`, `legal_unit_1`, `legal_quantity_2`, `legal_unit_2`, `gross_weight`, `net_weight`, `business_unit`, `pre_or_post_sales`
- 每个已返回行字段齐全，未知为 `null`；`extra="forbid"`
- 数量/金额/重量：无千分位、非科学计数法十进制字符串
- 日期 JSON：`YYYY-MM-DD`；编号保留前导零
- `currency`：大写三位 ISO；`trade_terms` 大写；`new_or_used` ∈ {新, 旧}；`pre_or_post_sales` ∈ {售前, 售后}
- **`RecognitionProblem`**：`{source, location, issue, action}` 均非空

### outcome 语义（`validate_channel_outcome`）

| outcome | 约束 |
|---------|------|
| `success` | 无未解决缺失；若仍有 null 路径则自动降为 `partial_success` 并补 problem |
| `partial_success` | 至少一条 problem；核心事实已确认（票次身份 + 行级 product_id/quantity/unit/currency/total_price）；字段已完整则归正为 `success` |
| `input_problems` | 至少一条 problem；`items` 可为空；正式字段只放已证实值 |

### 渠道独立 header

| 渠道 | header 模型 | 差异要点 |
|------|-------------|----------|
| Philips | `OrderHeader` | 含 `om`/`so`/`salesperson`/`etd` 等；无 `invoice_date` |
| Tecan | `TecanHeader` | 含 `invoice_date`；无 `om`/`so`/`salesperson`/`etd` |

终态外壳均为：`{outcome, data: {header, items}, problems}`。

**不输出**：`shipment`、Excel、候选列表、审计明细。OMS 只消费 `run.result`，不解析 `reply` 或工具候选文本。

业务规则（同票归集、多发票逗号拼接、同 12NC 不合并、主数据仅补缺）由 Skill 提示词驱动，在**单一 run** 内完成；无跨 run 消息/任务状态表。

## Agent、middleware 与状态

### Brain 装配（`runtime/agent.py`）

- `Brain` Protocol：`stream(payload, config, **kwargs) -> Iterator`
- `BrainFactory` Protocol：`create(resources, middleware, tools, workflow) -> Brain`
- 默认实现 `DeepAgentsBrainFactory`：
  - 模型：`init_chat_model(anthropic:{MINIMAX_MODEL}, ...)`，`thinking={"type": "adaptive"}`
  - `create_deep_agent`：`subagents=[]`，`skills=["/skills/"]`，`/skills/**` 写拒绝
  - harness profile `anthropic`：关闭 general-purpose subagent
  - `WAG` 时追加 `WAG_WORKFLOW_PROMPT`、`response_format=ToolStrategy(PhilipsWgqRecognitionResult)`、工具 denylist
  - `DK` 时追加 `DK_WORKFLOW_PROMPT`、保留 `structured_schema=None`，并以 finalizer 终态校验

### middleware 栈（`runtime_middlewares`）

每次 `create` 新建实例（洋葱模型；Recovery 列表靠前 → `after_model` 最后执行）：

| 顺序 | Middleware | 条件 |
|------|------------|------|
| 1 | `StructuredOutputRecovery` | 仅 `structured_schema` 非空（Philips workflow） |
| 2 | `ToolTelemetry` | 始终 |
| 3 | `NoProgressMiddleware` | 始终；同工具同参连续 `NO_PROGRESS_WINDOW=3` 次 → `NoProgressLoop` |
| 4 | `StructuredOutputCompatibility` | 始终；ToolStrategy 请求关闭 thinking |
| 5 | `MemoryMiddleware` | 主 Agent 且传入 `memory_backend`；加载 `/memories/AGENTS.md` |

主 Agent 有 memory 时约 **5** 个 middleware；无 schema 时约 4 个（无 Recovery）。生产不配置业务 SubAgent。

### StructuredOutputRecovery（Philips 专用）

- hook：`after_model`，`can_jump_to` 必须含 **`"model"` 与 `"end"`**
- 从助手 fenced/raw JSON 恢复 `structured_response`；校验失败则 `jump_to: "model"` 纠错（默认最多 2 次）
- 空 `data: {}` 壳：按同回合 `tool_call_id` 配对恢复或 skeleton 纠错
- **耗尽时必须显式 `jump_to: "end"`**，禁止只返回 `None`（否则 ToolStrategy 可能无限重入 model）
- 空壳耗尽 → all-null `data` + `partial_success` + runtime problem
- 其它失败耗尽 → 无 `structured_response` → harness `failed`
- 普通 / Tecan run：`structured_schema=None`，不走此路径；Tecan 由 finalizer 工具校验

### 虚拟文件系统（`AgentResources`）

`CompositeBackend` 路由：

| 虚拟前缀 | 后端 |
|----------|------|
| 默认 | `StateBackend`（图状态临时文件） |
| `/memories/` | `StoreBackend` → `dsagents_store.db` |
| `/artifacts/` | `FilesystemBackend` → `data/artifacts/` |
| `/large_tool_results/` | 同上磁盘 |
| `/skills/` | `FilesystemBackend` → `backend/skills/`（只读权限拒绝写） |

首次启动若 `/memories/AGENTS.md` 缺失则写入 baseline 手册（ZIP/`parse_documents` 使用约定）。

## workflow 与工具表

### 业务 workflow

`WAG`（常量 `WAG_WORKFLOW`）：

1. API 收窄 `workflow` 字面量
2. Brain 加载 `/skills/philips_wgq_inbound_recognition/SKILL.md` 提示
3. `ToolStrategy(PhilipsWgqRecognitionResult)` + Recovery
4. 工具 denylist 去掉 Tecan finalizer
5. harness 强制 `structured_response` → `run.result`

`DK`（常量 `DK_WORKFLOW`）：

1. Brain 加载 `/skills/tecan_import/SKILL.md`
2. 工具 denylist 去掉 Philips 主数据 lookup，保留共享 MinerU / XLSX 与 Tecan finalizer
3. harness 强制 `finalize_tecan_overseas_recognition` 的已校验结果 → `run.result`

### ToolCatalog 五工具（`runtime/tools.py`）

| 工具 | 来源 | 角色 |
|------|------|------|
| `parse_documents` | `integrations.mineru` | MinerU 批量解析 PDF/Office → JSON 或 ZIP |
| `extract_archives` | `integrations.mineru` | 解压 ZIP artifact |
| `lookup_philips_wgq_master_data` | Philips scripts | Tracking XLSX + Oracle 补齐 12NC 主数据 |
| `inspect_supply_chain_workbooks` | Tecan scripts（共享） | XLSX → 可读 JSON artifact |
| `finalize_tecan_overseas_recognition` | Tecan scripts | 校验并返回 Tecan 终态 JSON 字符串 |

**WAG denylist**（`_WAG_EXCLUDED_TOOLS`）：

```text
finalize_tecan_overseas_recognition
```

保留共享 MinerU、共享 XLSX 检查器与 Philips 主数据工具。**禁止**业务-only allowlist（否则 `/memories/AGENTS.md` 的 ZIP 指引会失效）。

**DK denylist**（`_DK_EXCLUDED_TOOLS`）排除 `lookup_philips_wgq_master_data`，保留共享 MinerU / XLSX 与 Tecan finalizer。

新增 Skill 工具：在 `default_tool_catalog()` 静态 import + 注册一行；不自动扫描。

## Key Abstractions

| 抽象 | 类型 | 位置 | 说明 |
|------|------|------|------|
| `Brain` | `Protocol` | `runtime/agent.py` | 可 stream 的 Agent 图 |
| `BrainFactory` | `Protocol` | `runtime/agent.py` | 按 run 创建 Brain |
| `DeepAgentsBrainFactory` | 具体类 | `runtime/agent.py` | 默认 DeepAgents 实现 |
| `HarnessRuntime` | 具体类 | `runtime/execution.py` | 执行 run、投影事件、协作 cancel |
| `AgentResources` | 具体类 | `runtime/resources.py` | 三 SQLite + backend 生命周期 |
| `ResourceConfig` | dataclass | `runtime/resources.py` | 路径配置（锚定 `backend/`） |
| `SqliteRunLedger` | 具体类 | `runtime/runs.py` | runs / run_events 持久化 |
| `RunSnapshot` / `RunEvent` | dataclass | `runtime/runs.py` | 投影与事件值对象 |
| `ToolCatalog` | dataclass | `runtime/tools.py` | 有序 callable 元组 |
| `ContractModel` | Pydantic | `skills/channel_contract.py` | `extra="forbid"` 基类 |
| `PhilipsWgqRecognitionResult` | schema | Philips | ToolStrategy + run.result |
| `TecanOverseasRecognitionResult` | schema | Tecan | finalizer 校验 + run.result |

## Entry Points

| 入口 | 用途 |
|------|------|
| `api:app` / `create_app()` | uvicorn HTTP 服务 |
| `create_harness(resources)` | 程序内装配默认 Brain + 五工具 |
| `AgentResources(config).__enter__()` | 打开三库与 backend |
| `HarnessRuntime.execute_run(...)` | 同步生成器驱动单次 run |
| `python -m tests.test_*` | 本地 assert 门禁（非 pytest） |

程序内典型装配：

```text
with AgentResources(ResourceConfig()) as resources:
    harness = create_harness(resources)
    for event in harness.execute_run(messages, session_id, run_id, workflow=...):
        ...
```

## Error Handling

| 条件 | run status | `run.result` | 备注 |
|------|------------|--------------|------|
| 合法 Philips/Tecan 终态 JSON（含 `input_problems`） | `succeeded` | 完整业务 JSON | 业务问题 ≠ 执行失败 |
| Philips 缺失/非法 structured_response | `failed` | `null` | Recovery 耗尽或未产出 |
| DK 未调用 Tecan finalizer | `failed` | `null` | workflow 终态缺失 |
| 未调用 Tecan finalizer 的通用 run | `succeeded` | `null` | 普通阅读合法 |
| `NoProgressLoop` | `failed` | `null` | 同工具死循环 |
| 其它模型/工具/运行时异常 | `failed` | `null` | `_ensure_failed_run` 兜底 |
| 用户 cancel + GraphDrained | `cancelled` | 不伪造 | 协作 drain |
| 同 session 并发 | HTTP 409 | — | 进程内锁 |
| 未知 run_id | HTTP 404 | — | |
| 已终态再 cancel | HTTP 409 | — | |
| OMS 索引写失败 | 忽略 | — | 不阻塞已创建 run |
| Oracle 配置/客户端缺失 | 工具返回 problem | — | 优雅降级，不崩溃 run |
| MinerU 超时/失败 | 工具抛错 → 可能 failed | — | 视 Agent 是否恢复 |

时间戳：ledger 与 OMS 统一 **UTC+8** 本地 `YYYY-MM-DD HH:MM:SS`。

持久化：

- `data/dsagents_runs.db` — runs + run_events
- `data/dsagents_checkpoints.db` — LangGraph checkpointer
- `data/dsagents_store.db` — StoreBackend / memory
- `data/artifacts/` — uploads / downloads
- `log/oms_log.log` — best-effort JSONL 旁路索引（非第八类 event，无查询 API）

无自动 schema migration；三库均 `create table if not exists`。

## 验证入口

本地 assert 脚本（`cd backend`）：

```text
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
```

修改 backend 后先同步本目录 codebase 事实文档，再按影响更新根级 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`，并执行 `git diff --check`。
