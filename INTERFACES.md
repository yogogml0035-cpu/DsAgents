# INTERFACES

> 系统级接口边界。已确认契约直接陈述；证据不足或推断的标 **需确认**。底层契约细节（完整请求/响应 JSON 形状、表结构、配置键清单、工具入参）以 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) 为准。
> 本轮刷新（2026-07-16）对齐 backend 事实文档（Analysis Date: 2026-07-16，`last_mapped_commit` 28534a9）：固定 `philips_wgq_inbound_recognition` workflow、`run.result` 结构化响应通道、`runtime/middleware.py`（含 `StructuredOutputRecovery` 有界重试、空 data 壳纠错与耗尽 all-null skeleton）、workflow **denylist**（保留共享 MinerU）、主 Agent middleware 共约 5 个 / SubAgent 各 4 个、5 个静态工具与仅保留的 2 个 Tecan SubAgent；四 HTTP 端点、7 类事件、协作 cancel、三 SQLite + artifacts 边界保持。

HTTP 与业务 Skill 的文件边界只接受显式 `/artifacts/...` 路径；`parse_documents` 的程序内调用为测试便利保留 `allow_local`，不改变对外 API 契约。

## 1. HTTP API 边界

四个端点（入口 `api.py`；`create_app(*, resource_config=None, harness_factory=create_harness)`；模块级 `app = create_app()`；`uv run uvicorn api:app --host 0.0.0.0 --port 8500`）。**无 SSE** / `StreamingResponse` / `text/event-stream`，事件靠轮询。

| 方法 / 路径 | 入参 | 行为 | 返回 |
|---|---|---|---|
| `POST /runs` | `{"workflow"?: "philips_wgq_inbound_recognition", "session_id"?: str\|null, "messages": [{"role", "content": [text\|artifact block]}...]}`（`extra="forbid"`） | `run_id` 服务端生成；Philips workflow 禁止非空 `session_id` 并始终生成新 session；省略 workflow 时保留通用 session 语义；写 ledger → daemon 执行 | `200 {"run_id","session_id","status":"queued"}`；未知 workflow/非法复用/其它校验失败 `422`；通用 session 冲突 `409` |
| `GET /runs/{run_id}` | query `after_event_id: int\|null` | run 快照 + 顶层 `workflow`/解析后的 `result` + 增量 events + 最新内容事件 + 全量 `usage` | `200 {"run","workflow","result","events","latest_content_event","usage"}`；未知 `404`。`run` 快照也含 `workflow` / `result`；无模型调用时 `usage=null` |
| `POST /runs/{run_id}/cancel` | path `run_id` | 见 §2 | `404` 未知 / `409` 终态 / `200` 已 cancelling\|cancelled / `202` 活跃 drain |
| `POST /upload` | multipart 字段名 `files`（可多文件） | 同请求共用 `batch_timestamp`；落到 `/artifacts/uploads/<cleaned-stem>_<ts>(_n).ext`；只保存不解析 | `200 {"files":[{"file_path","name","mime_type","size"}]}` |

补充约定：

- **不再支持**旧 `{"message":"..."}` 体；`workflow` 只接受当前固定字面量，不能用自然语言消息猜工作流。
- Philips workflow 的 `POST /runs` 仍只返回 `queued`。终态业务 JSON 从 GET 顶层 `result`（或 `run.result`）读取，**不解析** `reply`。
- Philips `result` 固定为 `{"outcome":"success|partial_success|input_problems","data":...|null,"problems":[...]}`；**字段名为英文**（与 tool schema 一致，如 `original_waybill_number`、`product_id`、`quantity`）；所有业务字段固定存在、缺失为 `null`。OMS 外高桥中文表单 key 由调用方映射。
- `artifact` block 是**项目 API 语义**，进入 Brain 前由 `HarnessRuntime` 转为 `ARTIFACT_REFERENCE_HINT` 文本路径提示，再由 agent 决定 `read_file` / `parse_documents`。
- `after_event_id` **只裁剪** `events[]`，不影响 `latest_content_event` 与顶层 `usage`。
- 事件类型固定 7 类：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`（`model_usage` 不计入 `latest_content_event`）。
- 派生通道：`messages` → usage/thinking/text_delta；`custom` → tool_execution（`ToolTelemetry`）+ tool_progress（MinerU）；`updates` → assistant_message / tool_execution / 可选 `structured_response`。SubAgent 文本 token 不进公开 thinking/text；其 `model_usage` 仍计入。
- 时间字段：中国时区（UTC+8）本地时间 `YYYY-MM-DD HH:MM:SS`（如 `2026-07-17 12:01:59`）。当前**无**鉴权、**无** CORS。
- 启动 lifespan：装配资源 → `fail_incomplete_runs("执行已中断，请重试")`。

**Philips `result.outcome` 与 run 终态：**

| `outcome` | `data` | `problems` | run 终态 |
|-----------|--------|------------|----------|
| `success` | 完整 `RecognitionData`（shipment/header/items） | 可为非空（字段缺口等） | `succeeded` |
| `partial_success` | 完整 `RecognitionData` | **至少一个** | `succeeded` |
| `input_problems` | **必须 `null`** | **至少一个** | `succeeded`（业务问题 ≠ 执行失败） |
| （无/非法 structured_response） | — | — | `failed` |

空 `data: {}` 壳经 recovery 耗尽时 middleware 可写 all-null skeleton + `partial_success`（见 §4），与上表 `partial_success` 行一致；完整 JSON 形状与 `usage` 字段表见 [`INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) 的 **APIs & External Services** 章节。

## 2. 取消流

`POST /runs/{run_id}/cancel`（`api.py` `cancel_run`）：

| 条件 | HTTP | 行为 |
|------|------|------|
| 未知 run | `404` | `{"error":"Unknown run: ..."}` |
| 终态 `succeeded` / `failed` | `409` | `{"error":"Run already terminal: ...","status":...}` |
| 已 `cancelling` / `cancelled` | `200` | 幂等返回当前 `status` |
| 活跃 `queued` / `running` | `202` | 投影 `cancelling` → `harness.request_cancel(run_id)`；有 `RunControl` 则协作 drain（`GraphDrained` → `cancelled`）；尚未进入 `execute_run` / 未注册 control 则直接 `cancelled` |

`run_controls: dict[run_id → RunControl]` 为进程内字典，仅服务 cancel。取消**不回滚**已生成文件，**不**实现多进程强杀。工具阻塞（如 MinerU 轮询）期间 drain 可能延迟。

## 3. 程序内接口

仓库**不**提供 one-shot 单函数入口。组合路径：

```python
import json

from runtime import create_harness
from runtime.resources import AgentResources, ResourceConfig

messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "hello"},
        ],
    }
]

with AgentResources(ResourceConfig()) as resources:
    harness = create_harness(resources)
    run = resources.runs.create_run(
        "run-id",
        "session-id",
        json.dumps(messages, ensure_ascii=False),
    )
    for _event in harness.execute_run(messages, "session-id", run.run_id, workflow=None):
        pass
    snapshot = resources.runs.get_run(run.run_id)
```

- `execute_run(messages, session_id, run_id, workflow=None)` → `Iterator[RunEvent]`；选择 Philips 时 `create_run(..., workflow=...)` 与 execute 参数应一致，并由调用方使用全新 session。
- 稳定导出（`runtime/__init__.py`）：`AgentResources`、`ResourceConfig`、`HarnessRuntime`、`create_harness`、`RunEvent`、`RunSnapshot`、`SqliteRunLedger`。
- 测试注入：`create_app(resource_config=..., harness_factory=...)` + `FakeBrainFactory`（`tests/test_support.py`）。
- 验证：`cd backend && python -m tests.test_api` 等 assert 脚本（**非 pytest**）。

## 4. Brain 调用约定

`HarnessRuntime.execute_run` 统一驱动形式（已确认）：

```python
brain.stream(
    {"messages": normalized_messages},
    config={"configurable": {"thread_id": session_id}},
    stream_mode=["messages", "custom", "updates"],
    subgraphs=True,
    version="v2",
    control=RunControl(),
)
```

约束：

- payload 只含当前请求 `messages[]`，不重放本地 session 历史。
- `thread_id = session_id`（checkpointer 键）；查询维度始终是 `run_id`。
- `text` 原样；`artifact` → `ARTIFACT_REFERENCE_HINT` 后再入 Brain。
- 三 channel 全部消费（见 §1）；raw v2 chunk 整体落库（可 spill）。
- `BrainFactory.create(..., workflow=workflow)` 明确接收 workflow。Philips 使用 `ToolStrategy(PhilipsWgqRecognitionResult)`，从 `updates` 捕获后再次 Pydantic 校验；缺失/非法即 `failed`。
- **workflow 工具收窄必须用 denylist**：Philips 排除帝肯工具（`save_tecan_extraction` / `generate_tecan_import`），**保留**共享 MinerU 工具 `parse_documents` / `extract_archives` 与 `lookup_philips_wgq_master_data`，不装 SubAgent；禁止只 allowlist 业务工具导致手册中的通用工具从模型工具表消失。验证：`python -m tests.test_workflow_setup`。
- `runtime/middleware.py` 的 `StructuredOutputCompatibility.wrap_model_call` 只在 `ToolStrategy` 请求中通过 `request.override(model=...)` 关闭该次 Anthropic thinking，以兼容强制 tool choice；工厂原始模型与通用/Tecan adaptive thinking 不变。兼容 middleware 不写 graph state。
- **`StructuredOutputRecovery`（硬性约定）**：`after_model` 从纯文本 JSON 恢复 `structured_response`；失败（含空文本、空 `data: {}` 壳）则 `jump_to: "model"`（默认最多 `DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES = 2`；总模型轮次约 `1 + max_retries`）；空壳用 `EMPTY_DATA_SHELL_HINT` + `PHILIPS_MINIMAL_DATA_SKELETON` 形状提示，**不**编造业务字段；耗尽或无法继续时**必须** `jump_to: "end"`，且 `@hook_config(can_jump_to=["model", "end"])`。禁止只返回 `None`——在仅有 `ToolStrategy`、无业务 tool 的图上会触发 model↔model 无限循环。**空壳耗尽**：写入 schema 合法的 all-null `data` + `partial_success` + runtime problem → harness 可投影 `succeeded`；**其它失败模式**耗尽后无 `structured_response` → harness 标 `failed`。验证：`cd backend && python -m tests.test_harness`。
- `control=RunControl()`：`request_cancel` → drain → `GraphDrained` → `cancelled`。
- 生产工厂：`DeepAgentsBrainFactory`（MiniMax via `init_chat_model("anthropic:...")` + `create_deep_agent`）；主 agent 名 `MAIN_AGENT_NAME = "dsagents-main"`。
- `runtime_middlewares()` 固定顺序返回新建实例：`StructuredOutputRecovery` → `ToolTelemetry` → `NoProgressMiddleware` → `StructuredOutputCompatibility`；主 Agent 经 `memory_backend=` 追加受限 `MemoryMiddleware`（**共 5 个**）。两个 Tecan 声明式 SubAgent **不继承**主 Agent middleware，须经无 memory 的 `runtime_middlewares()` 显式注入（**各 4 个**）；勿同时使用 `create_deep_agent(memory=...)`。`runtime.agent` 保留 middleware 符号导入兼容性。
- `register_harness_profile("anthropic", ...)` 禁用默认 general-purpose subagent（锁定 `deepagents==0.6.12` 无构造参数式 `harness_profile`）。
- 旧 `Hands` / `ToolStatus*` 已删除；工具遥测由 `ToolTelemetry` → `tool_execution` 三态承担。

## 5. 存储 / artifacts / provider 边界

### 5.1 存储

`AgentResources` 暴露：

| 属性 | 类型 | 落点 |
|------|------|------|
| `resources.runs` | `SqliteRunLedger` | `data/dsagents_runs.db` |
| `resources.checkpointer` | `SqliteSaver` | `data/dsagents_checkpoints.db`（`thread_id=session_id`） |
| `resources.store` | `SqliteStore` | `data/dsagents_store.db`（`namespace=("dsagents",)`） |

- fresh schema，无迁移；`runs` 保存可选 `workflow` / `result_json`；中国时区本地时间 `YYYY-MM-DD HH:MM:SS`；大 payload 外溢 `data/internal/run-events/`（默认 `max_inline_bytes=262_144`）。
- 三库互不共享连接，无跨库事务。表结构与 `CompositeBackend` 路由见 backend ARCHITECTURE / STRUCTURE。

### 5.2 Artifacts

| 物理 | 虚拟前缀 | 写入方 |
|------|----------|--------|
| `data/artifacts/uploads/` | `/artifacts/uploads/` | `POST /upload` |
| `data/artifacts/downloads/` | `/artifacts/downloads/` | MinerU、解压、Tecan JSON/Excel（唯一下载名） |
| `backend/skills/` | `/skills/` | 只读 Skill 源（主 Agent write deny `/skills/**`） |

路径解析：`integrations/artifacts.py`（拒绝 `..`）。Tecan generator 默认 `allow_local=False`；`parse_documents` 为测试/程序内保留 `allow_local`。Tecan 模板在 `/skills/tecanimport/assets/`，生成时复制填充。Philips Tracking `.xlsx` 由专用工具只读，不生成 Excel。取消/失败不回滚 downloads。

### 5.3 Skills / 业务工具

- Philips 业务 Tool：`lookup_philips_wgq_master_data(product_ids, tracking_artifact?)`。它只返回稳定主数据与 `problems`，不返回历史数量、价格、单号、金额或重量。
- Philips 最终业务结果由 Pydantic 结构化响应承担（`run.result`）；Tecan 继续使用 `{"status":"generated",...}` / `{"code":"input_problems",...}` 工具结果。
- 5 工具静态注册（`runtime/tools.py`）：`parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`、`save_tecan_extraction`、`generate_tecan_import`。
- Philips workflow 工具表 = 全量 catalog **减去** denylist 帝肯工具（见 §4）；Tecan 走通用路径 + Skill 驱动。
- 声明式 SubAgent 仅 `tecan-extractor-a/b`；Philips 无 A/B/C、投票或 decisions。
- 完整工具入参见 [`INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) 业务工具与 Skill 边界节。

### 5.4 LLM / MinerU / Oracle provider

| 边界 | 实现 | 键名（仅名） | 证据 |
|------|------|--------------|------|
| 生产 LLM | MiniMax via Anthropic 兼容 `ChatAnthropic` + `create_deep_agent` | `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` | `runtime/agent.py`、`runtime/middleware.py` |
| 测试 LLM | `FakeBrain` / `FakeBrainFactory` | — | `tests/test_support.py` |
| MinerU | `requests`：提交任务 → 轮询 → JSON/ZIP | `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS`（必需）；`MINERU_EFFORT`（可空） | `integrations/mineru.py` |
| Oracle（可选） | `oracledb` thick mode；缺配置/失败优雅降级 | `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS` | Philips `scripts/tools.py`；Tecan 不消费 |

- `backend/.env` 在 `runtime/agent.py` 与 `integrations/mineru.py` import 时 `load_dotenv`；长期文档不记录真实值。
- prompt-cache：DeepAgents 尾栈 `AnthropicPromptCachingMiddleware`（非本仓自定义）；固定前缀勿注入 run_id/时间等动态内容。
- usage：`model_usage` 事件 + API `_usage_summary`（cache hit rate、MiniMax-M3 tier CNY；不可计价模型金额 `null`）。
- Oracle：Instant Client **不在仓库**。配置缺失、初始化/查询失败或未命中写入 Philips `problems`，不覆盖 Tracking、不丢弃 PDF 数据；未识别字段保持 `null`。部署清单见 [`CONCERNS.md`](backend/.planning/codebase/CONCERNS.md) Operational Prerequisites。

完整键表与观测面见 [`INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md)。

## 6. 已删除接口

不要引用下列已删除入口与形状：

**HTTP / 程序入口**

- `POST /files`、`POST /sessions/messages`、`POST /sessions/messages/stream`、`POST /sessions/messages/runs`
- `GET /sessions/{session_id}/runs`
- `from session import run_session`、`python -m backend.*`
- 旧顶层辅助模块导入（`from harness import ...` / `from hands import ...` 等）
- 旧 `backend/dsagents/` 包壳导入路径

**事件 / 业务工具 / 状态机**

- 事件：`tool_call` / `tool_status` / `tool_result`
- 业务：`build_*_canonical` / `save_*_adjudication` / `generate_*_documents`
- Philips：旧包、旧抽取/生成工具、extractor 投票、Excel 模板/写入及旧输入兼容参数
- 状态机：`needs_input` / `needs_c` / `needs_adjudication`
- Tecan：`info_source_preference` / `pn_info_source_overrides`（信息来源冲突一律 `input_problems`）
- Protocol：旧 `Hands` / `ToolStatusHands` / `ToolStatusMiddleware`

## 7. 未证实关系与任务入口

- 当前系统文档**未确认**其它子项目或跨系统调用方；调用方应只依赖本文件四端点与轮询语义，勿假设 SSE 或 session API。
- 证据与运维风险见 [`CONCERNS.md`](backend/.planning/codebase/CONCERNS.md)、[`SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §7–§8。

任务排查建议：

| 目标 | 动作 |
|------|------|
| 改 HTTP 契约 | 先改本文件 §1/§2 → `INTEGRATIONS.md`（APIs & External Services）→ `api.py`；勿重新引入 SSE / 旧 `message` 字段 |
| 替换 LLM | 实现 `BrainFactory` 并经 `create_harness` 注入；同步 stream 解析与 profile |
| 新增 Skill / 工具 | `skills/<name>/` + `default_tool_catalog()` 静态注册 + `package-data` |
| 改 Philips 识别 | `docs/philips-wgq-inbound-recognition-prd.md` → Skill/schema/主数据工具 → workflow/result HTTP 合同；保持单一工具、无 SubAgent/Excel |
| 改 Tecan 生成 | `SKILL.md` / `references/` → `scripts/tools.py` / `documents.py`；保持 2 Tool + `input_problems` |
| 改 structured recovery | `runtime/middleware.py` `StructuredOutputRecovery`；保留 `can_jump_to` 含 `"end"`、空壳纠错、耗尽 skeleton/`jump_to: "end"`；`python -m tests.test_harness` |
| 改 workflow 工具裁剪 | denylist 排除他业务工具，保留 `parse_documents` / `extract_archives`；`python -m tests.test_workflow_setup` |
| 加鉴权 / CORS | 当前缺失（已确认）；需显式补中间件 |
| 跨进程部署 | 单飞锁仅进程内；多 worker 前需跨进程锁或单进程约束 |
| schema / 数据切换 | 停服并清空整个 `backend/data/`（无迁移） |
| 验证 | `cd backend && python -m tests.*`（**非 pytest**）；常用：`test_api`（cancel/usage）、`test_harness`、`test_workflow_setup` |
