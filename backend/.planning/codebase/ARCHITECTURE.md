---
title: backend 架构事实
last_mapped_commit: 3dadbc4
last_mapped_commit_full: 3dadbc4feada64f2f4e70d292a67d343beb77c10
analysis_date: 2026-07-20
focus: arch
---

# ARCHITECTURE — backend（dsagents）

> 分析日期：2026-07-20
> 映射提交：`3dadbc4`（完整 `3dadbc4feada64f2f4e70d292a67d343beb77c10`）
> 范围：`backend/` 源码与包结构（不含 venv、artifacts 大文件、egg-info、`.oracle` 客户端二进制）

## Pattern Overview

`backend/` 是发行名 **dsagents** 的单子项目 agent 运行时底座。核心模式如下。

| 模式 | 实现要点 |
|------|----------|
| **run-first harness runtime** | 执行与查询的唯一单位是 `run`；`HarnessRuntime.execute_run(...)` 驱动 brain 流式执行，并将观测写入 ledger |
| **事件溯源 ledger** | `run_events` append-only；`runs` 表为投影快照（status / reply / error / result） |
| **Brain Protocol** | `typing.Protocol` 仅用于 `Brain` / `BrainFactory`；默认实现 `DeepAgentsBrainFactory` → `create_deep_agent` |
| **ToolCatalog** | 静态 **5** 个 callable 工具注册；workflow 用 **denylist** 收窄业务工具，不扫描目录 |
| **Skill 成对目录** | kebab-case 资源目录（`SKILL.md` / references / assets，挂载 `/skills/`）+ 可 import 的 Python 包 |
| **OMS 旁路** | `append_run_created_log` 写 JSONL 索引；best-effort，不阻塞已创建 run，不进入 `run_events` |

无 session 业务 API、无 SSE。`session_id` 只作 LangGraph `thread_id` 与进程内单飞锁键。

映射提交 `3dadbc4` 删除了 `backend/build/` 下冗余构建副本，源码布局不变：`api.py` + `runtime/` + `integrations/` + `skills/` + `tests/`。

## Layers

```
HTTP 层          api.py（FastAPI：POST /upload、POST /runs、GET /runs/{run_id}、POST /runs/{run_id}/cancel）
      ↓
资源 / 执行层    runtime/resources.py（AgentResources）
                 runtime/execution.py（HarnessRuntime / create_harness）
                 runtime/oms_log.py（OMS 旁路索引）
      ↓
Agent 图层       runtime/agent.py（DeepAgentsBrainFactory、workflow denylist、Tecan SubAgent）
                 runtime/middleware.py（ToolTelemetry / NoProgress / StructuredOutput* / Memory）
                 runtime/observability.py（纯 chunk → 事件 payload 抽取）
      ↓
工具 / Skill 层  runtime/tools.py（ToolCatalog）
                 integrations/mineru.py、integrations/artifacts.py
                 skills/philipswgqinboundrecognition/、skills/tecanimport/
                 skills/philips-wgq-inbound-recognition/、skills/tecan-import/（资源）
      ↓
Ledger / 集成    runtime/runs.py（SqliteRunLedger、RunEvent、RunSnapshot）
                 SQLite：runs.db / store.db / checkpoints.db
                 外部：MinerU HTTP、Oracle（Philips 主数据，可选 thick）
```

### 层职责

1. **HTTP（`api.py`）**
   装配 lifespan 内的 `AgentResources` + `HarnessRuntime`；处理上传、创建 run、查询、取消；维护 `session_locks` / `active_runs` 进程内注册表；对 MiniMax-M3 做 usage 费用趋势估算（非计费事实）。

2. **资源 / 执行（`runtime/resources.py`、`runtime/execution.py`）**
   `AgentResources` 打开 SQLite store/checkpointer/ledger，装配 `CompositeBackend` 路由。
   `HarnessRuntime` 创建 brain、消费 stream、写事件、终态投影、协作式 cancel。

3. **Agent 图（`runtime/agent.py`、`middleware.py`、`observability.py`）**
   通过 deepagents `create_deep_agent` 构图；middleware 洋葱模型；observability 无 I/O。

4. **工具 / Skill**
   通用 MinerU 工具 + 业务工具；Skill 资源只读挂载；业务逻辑在 skill Python 包。

5. **Ledger / 集成**
   事件与快照；大 blob 溢出到 `data/internal/run-events/`；时间戳统一 UTC+8 本地 `YYYY-MM-DD HH:MM:SS`。

## Data Flow

### 1. Upload

```
POST /upload (multipart files)
  → clean_filename + make_timestamped_name
  → 写入 data/artifacts/uploads/
  → 返回 { files: [{ file_path: "/artifacts/uploads/...", name, mime_type, size }] }
```

虚拟路径前缀固定为 `/artifacts/...`，与 `CompositeBackend` 的 `/artifacts/` 路由一致。

### 2. Create run

```
POST /runs { workflow?, session_id?, messages: [{ role, content: text|artifact }] }
  → session_id = 请求值或 uuid4.hex（workflow 与 session_id 互斥：workflow 强制新 session）
  → run_id = uuid4.hex
  → _acquire_session_run：同 session 非阻塞锁，冲突 409「该会话正在运行」
  → SqliteRunLedger.create_run → status=queued + status 事件
  → append_run_created_log（best-effort try/except）
  → daemon Thread(_run_background) → execute_run
  → 立即返回 { run_id, session_id, status: "queued" }
```

### 3. execute_run

```
HarnessRuntime.execute_run(messages, session_id, run_id, workflow)
  → emit status=running
  → 注册 RunControl 到 run_controls[run_id]
  → brain_factory.create(
        middleware=runtime_middlewares(memory_backend=backend),  # 主 Agent 含 Memory
        tools=ToolCatalog.as_list(),
        workflow=workflow,
     )
  → brain.stream(
        {"messages": normalized},  # artifact 块 → 文本提示 ARTIFACT_REFERENCE_HINT
        config={"configurable": {"thread_id": session_id}},
        stream_mode=["messages", "custom", "updates"],
        subgraphs=True, version="v2", control=RunControl,
     )
  → 按 chunk 类型写事件（见「事件 7 类」）
  → workflow==philips_wgq_inbound_recognition 时：
        必须从 updates 得到 structured_response
        → PhilipsWgqRecognitionResult.model_validate → result dict
  → 终态 succeeded（reply + result）| failed | cancelled
  → finally 移除 run_controls
```

后台线程 `_run_background` 消费 generator 至耗尽；未捕获异常时 `_ensure_failed_run`；`finally` 释放 session 锁。

### 4. Query snapshot + events

```
GET /runs/{run_id}?after_event_id=
  → run 投影（RunSnapshot）
  → events（可增量）
  → latest_content_event（排除 status / model_usage）
  → usage（aggregate_model_usage + API 层 CNY 估算）
```

### 5. Cancel

```
POST /runs/{run_id}/cancel
  → 终态 succeeded/failed → 409
  → cancelling/cancelled → 200 幂等
  → 否则 emit cancelling
  → harness.request_cancel(run_id) → RunControl.request_drain
       True：执行中协作 drain → GraphDrained → cancelled
       False：尚未进入 execute_run（queued）→ 直接 emit cancelled
  → 202 { status: "cancelling" }
```

启动时 `fail_incomplete_runs` 将残留 `queued|running|cancelling` 标为 failed（`INTERRUPTED_RUN_ERROR`）。

## Key Abstractions

### HarnessRuntime（`runtime/execution.py`）

- 持有 `AgentResources`、`ToolCatalog`、`BrainFactory`、`run_controls: dict[str, RunControl]`。
- `execute_run`：唯一执行入口；yield `RunEvent`。
- `request_cancel`：协作 drain；无 control 返回 `False`。
- `create_harness(resources)`：默认 `default_tool_catalog()` + `DeepAgentsBrainFactory()`（延迟 import 避免循环依赖）。

### Brain / BrainFactory Protocol（`runtime/agent.py`）

```python
class Brain(Protocol):
    def stream(self, payload, config=None, **kwargs) -> Iterator: ...

class BrainFactory(Protocol):
    def create(self, *, resources, middleware, tools, workflow=None) -> Brain: ...
```

`DeepAgentsBrainFactory`：

- 模型：`init_chat_model(anthropic:{MINIMAX_MODEL}, ...)`，`thinking={"type": "adaptive"}`。
- `create_deep_agent`：backend / checkpointer / store / skills=`["/skills/"]` / permissions deny write `/skills/**`。
- 注册 harness profile `anthropic` 且 **关闭** auto general-purpose subagent（`GeneralPurposeSubagentProfile(enabled=False)`）。
- 主 Agent 名：`MAIN_AGENT_NAME = "dsagents-main"`（定义于 `runtime/observability.py`）。

### AgentResources / ResourceConfig（`runtime/resources.py`）

- 数据根：`backend/data/`（`_BACKEND_DIR` 锚定，与 CWD 无关）。
- `dsagents_runs.db`、`dsagents_store.db`、`dsagents_checkpoints.db`、`artifacts/`、`internal/run-events/`。
- `CompositeBackend` 路由：
  - `/memories/` → `StoreBackend`（跨 run 共享；启动写 baseline `AGENTS.md`）
  - `/artifacts/`、`/large_tool_results/` → `FilesystemBackend(artifacts_dir)`
  - `/skills/` → `FilesystemBackend(skills_dir)`（包内资源）
  - default → `StateBackend`

### ToolCatalog（`runtime/tools.py`）

静态 5 个 handler：

| 工具 | 来源 |
|------|------|
| `parse_documents` | `integrations.mineru` |
| `extract_archives` | `integrations.mineru` |
| `lookup_philips_wgq_master_data` | Philips skill `scripts.tools` |
| `save_tecan_extraction` | Tecan skill `scripts.tools` |
| `generate_tecan_import` | Tecan skill `scripts.tools` |

新增 Skill：静态 import + 注册一行；不自动扫描。

### SqliteRunLedger / RunEvent / RunSnapshot（`runtime/runs.py`）

- **Run 状态机**：`queued → running → succeeded|failed|cancelled`；取消路径 `queued → cancelled` 或 `running → cancelling → cancelled`。
- **RunEvent**：`event_id, run_id, event_type, created_at, payload, raw`。
- **RunSnapshot**：`run_id, session_id, input_messages_json, workflow, status, timestamps, reply, error, result`。
- 大 payload：`max_inline_bytes`（默认 262_144）溢出到 `run_events_dir` 文件。
- `aggregate_model_usage`：按 run 汇总 token + by_agent + per-call（供 API 分层计价）。

### Middleware（`runtime/middleware.py`）

`runtime_middlewares(memory_backend=?)` 返回**每次构图新实例**：

| 顺序（列表前 = after_model 后执行） | 类 | 作用 |
|-----------------------------------|-----|------|
| 0 | `StructuredOutputRecovery` | 文本 JSON / 空 data 壳恢复；`can_jump_to=["model","end"]` |
| 1 | `ToolTelemetry` | tool start/complete/error → custom stream |
| 2 | `NoProgressMiddleware` | 同 tool+args 连续 3 次 → `NoProgressLoop` |
| 3 | `StructuredOutputCompatibility` | ToolStrategy 请求关闭 thinking |
| 4（仅主 Agent） | `MemoryMiddleware` | 加载 `/memories/AGENTS.md`；受限追加提示 |

主 Agent 约 **5** 个 middleware；SubAgent 各 **4** 个（无 Memory）。

Philips workflow 额外：

- `DeepAgentsBrainFactory` 确保 `StructuredOutputCompatibility` + 将 `StructuredOutputRecovery` **insert(0)**（after_model 最后跑）。
- `response_format = ToolStrategy(PhilipsWgqRecognitionResult, handle_errors=philips_structured_output_error_message)`。

无参 `StructuredOutputRecovery` 默认绑定 **Philips** schema（`PhilipsWgqRecognitionResult` / skeleton）；Tecan SubAgent 若走文本后备路径会语义错位（Tecan 主要靠 `ExtractionReference` ToolStrategy）。

### Workflow denylist

唯一固定 HTTP workflow：`philips_wgq_inbound_recognition`（常量 `WORKFLOW`，见 `skills/philipswgqinboundrecognition/schema.py`）。

```python
_PHILIPS_EXCLUDED_TOOLS = frozenset({
    "save_tecan_extraction",
    "generate_tecan_import",
})
```

- **denylist** 排除其他业务（帝肯）工具；**保留**共享 MinerU（`parse_documents` / `extract_archives`）与本业务主数据工具 `lookup_philips_wgq_master_data`。
- 禁止业务-only allowlist。
- workflow 时 `subagents=[]`（不挂 Tecan 抽取器）；系统提示追加 `PHILIPS_WORKFLOW_PROMPT`。

### SubAgent（Tecan，2 个）

非 Philips workflow 时 `workflow_subagents()` 注册两个无状态抽取器：

- `tecan-extractor-a` / `tecan-extractor-b`
- 工具仅 `save_tecan_extraction`；只读 FS permission（deny write `/**`）；`response_format=ToolStrategy(ExtractionReference)`
- 各自 `runtime_middlewares()`（无 memory）
- 主 Agent 技能资源：`/skills/tecan-import/SKILL.md`；生成侧 `generate_tecan_import`

### Skill 成对目录

| 资源目录（kebab，挂载 `/skills/`） | Python 包 | 用途 |
|----------------------------------|-----------|------|
| `skills/philips-wgq-inbound-recognition/` | `skills/philipswgqinboundrecognition/` | 唯一固定 workflow 识别 |
| `skills/tecan-import/` | `skills/tecanimport/` | 帝肯进口发票箱单 |

`pyproject.toml` `package-data` 打包 kebab 目录下 `SKILL.md`、references、assets。

## Entry Points

### HTTP（`api.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/upload` | 多文件上传 → `/artifacts/uploads/...` |
| `POST` | `/runs` | 创建 run 并后台执行 |
| `GET` | `/runs/{run_id}` | 投影 + 事件 + usage |
| `POST` | `/runs/{run_id}/cancel` | 协作取消 |

模块级 `app = create_app()`；可注入 `resource_config` / `harness_factory`（测试用）。

### 程序内

```python
with AgentResources(ResourceConfig(...)) as resources:
    harness = create_harness(resources)
    for event in harness.execute_run(messages, session_id, run_id, workflow=...):
        ...
```

对外稳定导出见 `runtime/__init__.py`：`AgentResources`、`ResourceConfig`、`HarnessRuntime`、`create_harness`、`RunEvent`、`RunSnapshot`、`SqliteRunLedger`。

## session_id 语义

- **仅**两处用途：
  1. LangGraph `config["configurable"]["thread_id"]`（checkpoint 线程）
  2. 进程内 `_acquire_session_run` / `_release_session_run` 单飞锁（`threading.Lock`，非阻塞）
- 不是业务会话 API；无 list sessions、无 session CRUD。
- `workflow` 与客户端 `session_id` 同时出现 → `RunRequest` 校验失败（workflow 必须新 server-generated session）。

## 唯一固定 workflow

- 名称：`philips_wgq_inbound_recognition`
- HTTP：`RunRequest.workflow: Literal["philips_wgq_inbound_recognition"] | None`
- 业务结果：`run.result` = `PhilipsWgqRecognitionResult` JSON（英文字段）
- outcome：`success` | `partial_success` | `input_problems`
  - `input_problems`：`data=null` 且 `problems` 非空；**run 仍可 `succeeded`**（业务问题 ≠ 执行失败）
  - `success` / `partial_success`：完整 `data.shipment` / `header` / `items`（未知 `null`）
- 工具：MinerU 共享 + `lookup_philips_wgq_master_data`（Tracking xlsx + 可选 Oracle）

Tecan 无 HTTP workflow 字面量；走默认（`workflow=None`）+ Skill 引导 + SubAgent。Tecan 包内另有字符串 `WORKFLOW = "tecan-import"`（资源/业务标识，非 API `Literal`）。

## 事件 7 类

执行路径固定写入以下类型（`emit_run_event` / `emit_run_status`）：

| type | 来源 | payload 要点 |
|------|------|----------------|
| `status` | ledger | `status`；终态可带 `reply`/`error`/`result` |
| `model_usage` | messages + usage_metadata | model/scope/agent_name/tokens/cache |
| `thinking` | messages thinking delta | `{content}` |
| `text_delta` | messages 文本 delta（过滤 subagent 文本） | `{content}` |
| `tool_progress` | custom，name ∈ {parse_documents, extract_archives} | 工具进度字段 |
| `tool_execution` | custom（telemetry）或 updates 中 tool_calls | name/args/status 或 message 侧调用 |
| `assistant_message` | updates 终端助手文本 | message_id/text[/thinking] |

Subagent 的 **usage 仍记录**；subagent **文本**不进入 `text_delta`。

业务问题统一结构 `problems: [{source, location, issue, action}]`（Philips schema / Tecan `input_problems` 返回体）。

## Error handling 与 StructuredOutputRecovery

### 执行层失败

| 条件 | 终态 |
|------|------|
| `GraphDrained`（cancel） | `cancelled`，error=`run cancelled` |
| `NoProgressLoop` | `failed` |
| 其他 Exception | `failed`，error 文本 |
| Philips 缺 `structured_response` | `failed`（`ValueError: structured_response missing...`） |
| 正常完成 | `succeeded`（可有 `result`） |

API 层线程异常：`_ensure_failed_run` 仅在非终态时补 `failed`。

### StructuredOutputRecovery 要点（与代码一致）

- Hook：`after_model`；`@hook_config(can_jump_to=["model", "end"])` — **必须含 `"end"`**。
- 已有 `structured_response` → 返回 `None`。
- 空 data 壳（`success`/`partial_success` 且 `data:{}` 或缺 shipment/header/items）：
  1. 若最新为 ToolMessage：用 `tool_call_id` **精确**匹配同一 AIMessage 的 schema call；
  2. 若该 AI 文本 JSON 合法 → 写 `structured_response` + `jump_to: "end"`；
  3. 否则 `EMPTY_DATA_SHELL_HINT` + `PHILIPS_MINIMAL_DATA_SKELETON` 纠错，`jump_to: "model"`。
- 正常提示：优先 schema **tool args**；文本 JSON 仅无法调工具时的后备。
- **空壳耗尽**（`max_retries` 默认 2）：all-null skeleton + `outcome=partial_success` + runtime problem → 可有 `structured_response` → harness **`succeeded`**。
- **其它失败耗尽**：仅 `jump_to: "end"`，**无** `structured_response` → harness 可 **`failed`**。
- **禁止**耗尽时只返回 `None`（否则 ToolStrategy model↔model 死循环）。
- **不编造**业务字段；骨架字段为 `null`。

### 集成降级

- **Oracle**（`lookup_philips_wgq_master_data`）：缺连接 env → problems 提示，不抛死；`ORACLE_CLIENT_LIB_DIR` 存在时 thick `init_oracle_client`，缺失则跳过 init（依赖 thin/默认行为）；查询异常 → problems，不拖垮整次 lookup。
- **MinerU**：缺 env 或任务失败 → 工具异常 / failed progress；部分无效路径记入 `failed` 列表。
- **OMS 日志**：I/O 异常吞掉，不影响 run 创建。

## CompositeBackend 与权限

- 写 `/skills/**`：**deny**（主 Agent permissions）。
- Tecan SubAgent：写 `/**` deny（只读文件）。
- 业务产物写 `/artifacts/downloads/` 等。

## OMS 旁路索引

- 模块：`runtime/oms_log.py`
- 默认路径：`backend/log/oms_log.log`（JSON Lines）
- 时机：`POST /runs` 在 `create_run` 成功之后、后台线程启动之前；`try/except` 吞掉异常
- 记录字段：`event=run_created`、`created_at`、`run_id`、`session_id`、`workflow`、`files[{name,path}]`
- **不是** `run_events`；无查询 API；仅运维 grep

## 测试形态（架构相关）

可执行 assert 脚本（非 pytest）：`cd backend && python -m tests.<name>`。

| 模块 | 覆盖 |
|------|------|
| `tests.test_tools` | MinerU 工具与 artifact 路径 |
| `tests.test_run_ledger` | ledger / resources / usage 聚合 / 手册 |
| `tests.test_harness` | 事件管线 / recovery / cancel 等 |
| `tests.test_api` | 四端点与 session 锁 |
| `tests.test_workflow_setup` | Philips denylist 与构图 |
| `tests.test_philips_wgq_inbound_recognition` | schema / master data |
| `tests.test_tecan_import` | 抽取与生成 |
| `tests.test_real_*` | 真实模型 / 外部服务（与本地回归分开） |

## 包与依赖边界

- 发行名：`dsagents`；安装根 `backend/`；顶层模块 `api` + 包 `runtime*` / `integrations*` / `skills*`。
- 依赖：deepagents、langgraph、langchain*、fastapi/uvicorn、oracledb、openpyxl、requests、python-dotenv 等（见 `pyproject.toml`）。
- 包管理：`uv sync`（`uv.lock`）；不用 `pip install -e .` 绕过锁。
