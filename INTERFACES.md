# INTERFACES

> 系统级接口边界。已确认契约直接陈述；证据不足或推断的标 **需确认**。底层契约细节（完整请求/响应 JSON 形状、表结构、配置键清单、工具入参）以 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) 为准。
> 本轮刷新（2026-07-14）对齐 backend 全部事实文档（同日刷新）与 `SYSTEM_MAP`：四 HTTP 端点、7 类事件、协作 cancel、`Brain` Protocol 调用形、每 Skill 2 业务 Tool、三 SQLite + artifacts 边界保持；校正 Oracle / CONCERNS 交叉引用，并收束 provider 与已删除接口入口。

HTTP 与业务 Skill 的文件边界只接受显式 `/artifacts/...` 路径；`parse_documents` 的程序内调用为测试便利保留 `allow_local`，不改变对外 API 契约。

## 1. HTTP API 边界

四个端点（入口 `api.py`；`create_app(*, resource_config=None, harness_factory=create_harness)`；模块级 `app = create_app()`；`uv run uvicorn api:app --host 0.0.0.0 --port 8500`）。**无 SSE** / `StreamingResponse` / `text/event-stream`，事件靠轮询。

| 方法 / 路径 | 入参 | 行为 | 返回 |
|---|---|---|---|
| `POST /runs` | `{"session_id": str\|null, "messages": [{"role", "content": [text\|artifact block]}...]}`（`RunRequest`，`ConfigDict(extra="forbid")`） | `session_id` 空则 `uuid4().hex`；`run_id` 服务端生成；同 session 单飞锁 → 写 ledger → daemon 执行 | `200 {"run_id","session_id","status":"queued"}`；校验失败 `422`；冲突 `409 {"error":"该会话正在运行","active_run_id"}` |
| `GET /runs/{run_id}` | query `after_event_id: int\|null` | run 快照 + 增量 events + 全局最新非 `status`/非 `model_usage` 内容事件 + 全部 `model_usage` 汇总的 `usage` | `200 {"run","events","latest_content_event","usage"}`；未知 `404`。`usage` 含 token、`cache_hit_rate`、可选 CNY 估算（仅 `MiniMax-M3` 可计价）、`by_agent`；无模型调用时 `usage` 为 `null` |
| `POST /runs/{run_id}/cancel` | path `run_id` | 见 §2 | `404` 未知 / `409` 终态 / `200` 已 cancelling\|cancelled / `202` 活跃 drain |
| `POST /upload` | multipart 字段名 `files`（可多文件） | 同请求共用 `batch_timestamp`；落到 `/artifacts/uploads/<cleaned-stem>_<ts>(_n).ext`；只保存不解析 | `200 {"files":[{"file_path","name","mime_type","size"}]}` |

补充约定：

- **不再支持**旧 `{"message":"..."}` 体（`extra="forbid"` → `422`）。
- `artifact` block 是**项目 API 语义**，进入 Brain 前由 `HarnessRuntime` 转为 `ARTIFACT_REFERENCE_HINT` 文本路径提示，再由 agent 决定 `read_file` / `parse_documents`。
- `after_event_id` **只裁剪** `events[]`，不影响 `latest_content_event` 与顶层 `usage`。
- 事件类型固定 7 类：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`（`model_usage` 不计入 `latest_content_event`）。
- 派生通道：`messages` → usage/thinking/text_delta；`custom` → tool_execution（`ToolTelemetry`）+ tool_progress（MinerU）；`updates` → assistant_message / tool_execution。SubAgent 文本 token 不进公开 thinking/text；其 `model_usage` 仍计入。
- 时间字段：UTC ISO-8601 毫秒。当前**无**鉴权、**无** CORS。
- 启动 lifespan：装配资源 → `fail_incomplete_runs("执行已中断，请重试")`。

完整 JSON 形状与 `usage` 字段表见 [`INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) 的 **APIs & External Services** / **Data Storage** 章节。

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
    for _event in harness.execute_run(messages, "session-id", run.run_id):
        pass
    snapshot = resources.runs.get_run(run.run_id)
```

- `execute_run(messages, session_id, run_id)` → `Iterator[RunEvent]`。
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
- `control=RunControl()`：`request_cancel` → drain → `GraphDrained` → `cancelled`。
- 生产工厂：`DeepAgentsBrainFactory`（MiniMax via `init_chat_model("anthropic:...")` + `create_deep_agent`）；主 agent 名 `MAIN_AGENT_NAME = "dsagents-main"`。
- 运行时恰好两个 middleware：`ToolTelemetry`、`NoProgressMiddleware`；声明式 SubAgent 须经 `runtime_middlewares()` 显式注入。
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

- fresh schema，无迁移；UTC ISO-8601 毫秒；大 payload 外溢 `data/internal/run-events/`（默认 `max_inline_bytes=262_144`）。
- 三库互不共享连接，无跨库事务。表结构与 `CompositeBackend` 路由见 backend ARCHITECTURE / STRUCTURE。

### 5.2 Artifacts

| 物理 | 虚拟前缀 | 写入方 |
|------|----------|--------|
| `data/artifacts/uploads/` | `/artifacts/uploads/` | `POST /upload` |
| `data/artifacts/downloads/` | `/artifacts/downloads/` | MinerU、解压、Skill JSON/Excel（唯一下载名） |
| `backend/skills/` | `/skills/` | 只读 Skill 源（主 Agent write deny `/skills/**`） |

路径解析：`integrations/artifacts.py`（拒绝 `..`）。业务 generator 默认 `allow_local=False`；`parse_documents` 为测试/程序内保留 `allow_local`。模板在 `/skills/<skill>/assets/`，生成时复制填充。取消/失败不回滚 downloads。

### 5.3 Skills / 业务工具

- 每 Skill **2** 业务 Tool：`save_*_extraction` + `generate_*_import`。
- 业务错误：`{"code":"input_problems","problems":[{"source","location","issue","action"}]}`；成功：`{"status":"generated","canonical_artifact","artifacts","manual_checks"}`。
- 6 工具静态注册（`runtime/tools.py`）：
  - `parse_documents`、`extract_archives`（`integrations/mineru.py`）
  - `save_philips_wgq_extraction`、`generate_philips_wgq_import`（Philips）
  - `save_tecan_extraction`、`generate_tecan_import`（Tecan）
- 4 个声明式 SubAgent：`philips-wgq-extractor-a/b`、`tecan-extractor-a/b`；各只获 extraction 工具 + 写 deny。
- 完整工具入参见 [`INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) 业务工具节。

### 5.4 LLM / MinerU / Oracle provider

| 边界 | 实现 | 键名（仅名） | 证据 |
|------|------|--------------|------|
| 生产 LLM | MiniMax via Anthropic 兼容 `ChatAnthropic` + `create_deep_agent` | `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` | `runtime/agent.py` |
| 测试 LLM | `FakeBrain` / `FakeBrainFactory` | — | `tests/test_support.py` |
| MinerU | `requests`：提交任务 → 轮询 → JSON/ZIP | `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS`（必需）；`MINERU_EFFORT`（可空） | `integrations/mineru.py` |
| Oracle（可选） | `oracledb` thick mode；缺配置/失败优雅降级 | `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS` | Philips `scripts/tools.py`；Tecan 不消费 |

- `backend/.env` 在 `runtime/agent.py` 与 `integrations/mineru.py` import 时 `load_dotenv`；长期文档不记录真实值。
- prompt-cache：DeepAgents 尾栈 `AnthropicPromptCachingMiddleware`（非本仓自定义）；固定前缀勿注入 run_id/时间等动态内容。
- usage：`model_usage` 事件 + API `_usage_summary`（cache hit rate、MiniMax-M3 tier CNY；不可计价模型金额 `null`）。
- Oracle：Instant Client **不在仓库**；缺失时核注清单单位字段降级为「需确认」+ `manual_checks`，不崩溃。部署清单见 [`CONCERNS.md`](backend/.planning/codebase/CONCERNS.md) Operational Risks「Oracle thick mode」。

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
- 状态机：`needs_input` / `needs_c` / `needs_adjudication`
- Tecan：`info_source_preference` / `pn_info_source_overrides`（信息来源冲突一律 `input_problems`）
- Protocol：旧 `Hands` / `ToolStatusHands` / `ToolStatusMiddleware`

## 7. 未证实关系与任务入口

- 当前系统文档**未确认**其它子项目或跨系统调用方；调用方应只依赖本文件四端点与轮询语义，勿假设 SSE 或 session API。
- 证据与运维风险见 [`CONCERNS.md`](backend/.planning/codebase/CONCERNS.md)、[`SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §8。

任务排查建议：

| 目标 | 动作 |
|------|------|
| 改 HTTP 契约 | 先改本文件 §1/§2 → `INTEGRATIONS.md`（APIs & External Services）→ `api.py`；勿重新引入 SSE / 旧 `message` 字段 |
| 替换 LLM | 实现 `BrainFactory` 并经 `create_harness` 注入；同步 stream 解析与 profile |
| 新增 Skill / 工具 | `skills/<name>/` + `default_tool_catalog()` 静态注册 + `package-data` |
| 改业务生成 | `SKILL.md` / `references/` → `scripts/tools.py` / `documents.py`；保持 2 Tool + `input_problems` |
| 加鉴权 / CORS | 当前缺失（已确认）；需显式补中间件 |
| 跨进程部署 | 单飞锁仅进程内；多 worker 前需跨进程锁或单进程约束 |
| schema / 数据切换 | 停服并清空整个 `backend/data/`（无迁移） |
| 验证 | `cd backend && python -m tests.test_api`（含 cancel/usage）；按影响跑其它本地脚本 |
