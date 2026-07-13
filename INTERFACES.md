# INTERFACES

> 系统级接口边界。已确认契约直接陈述；证据不足或推断的标 **需确认**。底层契约细节（完整请求/响应 JSON 形状、表结构、配置键清单）以 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) 为准。
> 本轮刷新（2026-07-13）已对齐 backend 全部事实文档（同日刷新）：旧 `backend/dsagents/` 包壳与旧顶层辅助模块已删除，源码顶层保留 `api.py` 并改为 `runtime/`、`integrations/`、`skills/` 三个顶层包；HTTP 改为四端点（含 `POST /runs/{run_id}/cancel`），事件 schema 改为 7 类（`tool_call`/`tool_status`/`tool_result` 删除），Brain/BrainFactory 仍是 Protocol，`ToolTelemetry`/`NoProgressMiddleware` 取代旧 Hands，业务 Tool 收敛为每 Skill 2 个，`info_source_preference` 删除。

HTTP 和业务 Skill 的文件边界只接受显式 `/artifacts/...` 路径；`parse_documents` 的程序内调用为测试便利保留本地路径兼容，不改变对外 API 契约。

## 1. HTTP API 边界

四个端点（入口模块 `api.py`，`create_app(*, resource_config=None, harness_factory=create_harness)` 返回 `FastAPI(lifespan=lifespan)`，模块级 `app = create_app()`；预期 `uv run uvicorn api:app --host 0.0.0.0 --port 8500` 拉起）。**当前无 SSE / `StreamingResponse` / `text/event-stream`**，事件获取靠轮询。

| 方法 / 路径 | 入参 | 行为 | 返回 |
|---|---|---|---|
| `POST /runs` | `{"session_id": str\|null, "messages": [{"role": str, "content": [{"type":"text","text":str} \| {"type":"artifact","path":str}]}...]}`（`RunRequest`，`ConfigDict(extra="forbid")`） | `session_id` 为空生成 `uuid4().hex`；`run_id = uuid4().hex`；获取单飞锁 → 写 ledger → 起 daemon 线程执行 | `200 {"run_id","session_id","status":"queued"}`；校验失败 `422`；冲突 `409 {"error":"该会话正在运行","active_run_id"}` |
| `GET /runs/{run_id}` | query `after_event_id: int\|null` | 读 run 快照 + run events（支持增量游标）+ 当前 run 全局最新非 `status`/非 `model_usage` 事件 + 该 run 全部 `model_usage` 汇总出的 `usage` | `200 {"run":{...},"events":[...],"latest_content_event":{...}\|null,"usage":{...}\|null}`；未知 run `404 {"error":"Unknown run: ..."}`。`usage` 含 `model_calls`、四类 token 总量、`cache_hit_rate`（无输入为 `null`）、`estimated_cost_cny`/`estimated_savings_cny`（按每个调用 input ≤/>512k 分 tier 后汇总，`pricing_as_of` 标日期）、估算说明与 `by_agent` 分项；模型不可计价时金额为 `null`，token 仍完整；无模型调用时 `usage` 为 `null` |
| `POST /runs/{run_id}/cancel` | path `run_id` | 见 §2 取消流 | `404`（未知 run）/ `409`（终态 `succeeded`/`failed`）/ `200`（已 `cancelling`/`cancelled`）/ `202`（活跃 drain） |
| `POST /upload` | multipart `files: list[UploadFile] = File(...)`（字段名固定 `files`，支持 1 个或多个） | 同一请求共用一个 `batch_timestamp`；落到 `/artifacts/uploads/<cleaned-stem>_<upload-ts>(_n).ext`；只有真实物理重名时才追加序号；`name` 返回清洗后的原始文件名；只保存不解析 | `200 {"files":[{"file_path":"/artifacts/uploads/...","name":"<原名>","mime_type":"<mime-or-application/octet-stream>","size":123}]}` |

- `POST /runs` **不再支持**旧 `{"message":"..."}` 请求体（`RunRequest` 用 `ConfigDict(extra="forbid")`）；Pydantic/FastAPI 直接返回校验错误（`422`）。
- `artifact` block 是**项目 API 语义**，不是直接发给 LangChain 的标准多模态 block。进入 Brain 前由 `HarnessRuntime.execute_run`（`runtime/execution.py`）转成文本提示 `ARTIFACT_REFERENCE_HINT`（`Uploaded artifact: {path}. Use read_file ... or parse_documents ...`），再由 agent 决定何时用 `read_file` 或 `parse_documents`。
- `after_event_id` 游标：为空返回全部事件；有值只返回 `event_id > after_event_id` 的增量事件。**`after_event_id` 只裁剪 `events[]`**，不影响 `latest_content_event`，也不影响顶层 `usage`。
- `latest_content_event`：始终返回当前 run 全局最新的非 `status`/非 `model_usage` 事件；没有此类事件时为 `null`。
- `usage`：始终从该 run 全部 `model_usage` 事件汇总（覆盖主 agent + subagent 调用）。
- 事件类型固定 7 类：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`（前六类为业务事件，`model_usage` 为成本/缓存观测事件，不计入 `latest_content_event`）。
- 成功 run 的最终 `latest_content_event` 通常是 `assistant_message`；`assistant_message` / `tool_execution` 由 `updates` channel 派生（`_update_events`）；`thinking` / `text_delta` / `model_usage` 由 `messages` channel 派生（`model_usage` 在 subagent 文本过滤之前提取）；`tool_execution`（三态）与 `tool_progress` 由 `custom` channel 派生（`ToolTelemetry.wrap_tool_call` 与 MinerU 工具自发）。最终 AIMessage 同时含 `thinking` 与 `text` block 时，`assistant_message.payload` 带上最后一个 `thinking` 文本和最终 `text`。
- 声明式 subagent 的 thinking/text token 不进入公开事件；但 subagent 的模型调用仍计入 `model_usage`（在过滤之前提取）。`tool_execution` 载荷含 scope 路径以重建「主 Agent → SubAgent → Tool」调用链。
- 常见办公文件和任意图片都可以通过 `POST /upload` 保存；能否被解析或理解取决于 DeepAgents `read_file`、`parse_documents`、MinerU 与模型多模态能力。
- 当前**未注册 `CORSMiddleware`**，也没有 auth middleware；四个端点全部匿名可调。
- run / event 响应里的时间字段统一为 UTC ISO-8601 毫秒（如 `2026-07-13T08:18:59.250Z`）。

明确**已删除**的旧接口（不要引用）：`POST /files`、`POST /sessions/messages`、`POST /sessions/messages/stream`、`POST /sessions/messages/runs`、`GET /sessions/{session_id}/runs`、`from session import run_session`、`python -m backend.*`、旧顶层辅助模块导入（`from harness import ...` / `from hands import ...` 等）。

## 2. 取消流（`POST /runs/{run_id}/cancel`）

`api.py` 的 `cancel_run`：

- 未知 run → `404 {"error":"Unknown run: <run_id>"}`。
- 终态（`succeeded`/`failed`）→ `409 {"error":"Run already terminal: <status>","status":<status>}`。
- 已 `cancelling`/`cancelled` → `200 {"status":<status>}`。
- 活跃 run（`queued`/`running`）→ 投影 `cancelling` 事件 → `harness.request_cancel(run_id)` 触发 LangGraph `RunControl` 协作 drain → `GraphDrained` 在 `execute_run` 内投影为 `cancelled`；若 run 尚未进入 `execute_run`（`queued` 或未注册 `RunControl`），直接置 `cancelled`，返回 `202 {"status":"cancelling"}`。

`run_controls: dict[run_id → RunControl]` 是进程内字典，仅用于 cancel。取消不回滚已生成文件，不实现多进程强杀。

## 3. 程序内接口

仓库不提供 one-shot 单函数入口。当前程序内调用路径是组合式：

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

`harness.execute_run(messages, session_id, run_id)` 返回 `Iterator[RunEvent]`。实际断言分布在 `backend/tests/test_*.py`，按影响范围直接运行对应脚本（如 `cd backend && python -m tests.test_api`）。

## 4. Brain 调用约定

`HarnessRuntime.execute_run` 统一用下面的调用形式驱动 Brain（已确认）：

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

- payload 只含当前请求里的 `messages[]`，不重放本地 session 历史。
- `thread_id = session_id`（短期上下文键）。
- `text` block 原样保留；`artifact` block 转成文本路径提示（`ARTIFACT_REFERENCE_HINT`）后再传给 Brain。
- 三 channel 全部消费：`messages` → `model_usage`（subagent 过滤之前）/ `thinking` / `text_delta`（主 agent，subagent 文本按 `lc_agent_name` 过滤）；`custom` → `tool_execution`（`ToolTelemetry`）+ `tool_progress`（MinerU 工具）；`updates` → `assistant_message` / `tool_execution`（`_update_events`）。
- `control=RunControl()`：取消时 `request_cancel(run_id)` 触发 drain，LangGraph 在自身检查点抛 `GraphDrained`，`execute_run` 投影为 `cancelled`。
- raw 完整 v2 chunk 整体落库（`run_events.raw_*`）。
- `thread_id=session_id` 只作为 checkpointer 上下文键，不参与 `run_events` 查询（查询维度始终是 `run_id`）。

## 5. Skills / 业务工具边界

- `DeepAgentsBrainFactory`（`runtime/agent.py`）用 `create_deep_agent(...)` 注入 `skills=["/skills/"]`、四个声明式 SubAgent（`workflow_subagents()`：`philips-wgq-extractor-a/b`、`tecan-extractor-a/b`）、`/skills/**` 写禁令（`FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")`）、主 Agent 的两个 middleware 与主 agent 名（`MAIN_AGENT_NAME = "dsagents-main"`）。业务意图由模型依据用户目标选择，不按文件名硬路由，普通 PDF 请求不触发业务 Skill。
- **每个 Skill 只暴露 2 个业务 Tool**。`save_*_extraction`（抽取保存，返回 `{extractor, artifact_path}`）+ `generate_*_import`（一站式 canonical + 匹配 + 计算 + Excel 写入 + 输出复核）。
- **业务错误形状统一**：`generate_*_import` 遇业务问题返回 `{"code":"input_problems","problems":[{"source","location","issue","action"}]}`，run 结束、用户修正材料后重新显式传路径；成功返回 `{"status":"generated","canonical_artifact","artifacts","manual_checks"}`。
- 所有输入路径必须显式指向 `/artifacts/...`；业务 JSON/Excel 写到 `/artifacts/downloads/` 的唯一新文件（不覆盖旧文件）。流程中间状态不增加 HTTP、数据库或恢复接口。
- 6 个工具名清单（全部由 `runtime/tools.py` 的 `default_tool_catalog()` 静态注册）：
  - MinerU 通用：`parse_documents`（`integrations/mineru.py`）、`extract_archives`（`integrations/mineru.py`）。
  - Philips：`save_philips_wgq_extraction`、`generate_philips_wgq_import`（`skills/philipswgqimport/scripts/tools.py`）。
  - Tecan：`save_tecan_extraction`、`generate_tecan_import`（`skills/tecanimport/scripts/tools.py`）。
- 完整的工具入参契约（关键入参、返回结构）见 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) §6。

明确**已删除**的旧业务工具/状态机（不要引用）：`build_*_canonical` / `save_*_adjudication` / `generate_*_documents` / `needs_input` / `needs_c` / `needs_adjudication` / `info_source_preference` / `pn_info_source_overrides`（Tecan 信息来源冲突一律作为 `input_problems`）。

## 6. 存储接口边界

`AgentResources`（`runtime/resources.py`，context manager）暴露三类持久资源：

- `resources.runs`：`SqliteRunLedger`（标准库 `sqlite3`；fresh schema，无迁移；支持 `create_run` / `get_run` / `get_run_events` / `get_latest_content_event` / `emit_run_status` / `aggregate_model_usage`）。
- `resources.store`：LangGraph `SqliteStore`（`namespace=("dsagents",)`）。
- `resources.checkpointer`：LangGraph `SqliteSaver`（`thread_id=session_id`）。

固定三条**逻辑** SQLite 通道（`runs`/`store`/`checkpoints`，文件按需创建，互不共享连接），完整文件→通道→写入方映射与表结构详见 [`backend/.planning/codebase/ARCHITECTURE.md`](backend/.planning/codebase/ARCHITECTURE.md) §9。run 输入快照字段为 `input_messages_json`（保存 `messages[]` JSON 字符串）。时间字段统一写 UTC ISO-8601 毫秒（`_now_text()` = `datetime.now(timezone.utc)` 毫秒）。大 run event payload/raw（默认 `max_inline_bytes=262_144`）外溢到 `data/internal/run-events/*.json`（按需创建）；`CompositeBackend` 路由规则同样见该文档。

## 7. LLM provider 边界

| 边界 | 实现 | 证据 |
|---|---|---|
| 生产 brain | `DeepAgentsBrainFactory`：`init_chat_model("anthropic:<MODEL>", ...)` → `ChatAnthropic`；`create_deep_agent(...)` 同时注入 `skills=["/skills/"]`、四个声明式 SubAgents、`/skills/**` 写禁令、主 Agent middleware 与主 agent 名（`MAIN_AGENT_NAME = "dsagents-main"`） | `runtime/agent.py` |
| 本地测试 brain | `FakeBrain` / `FakeBrainFactory`（模拟 v2 stream chunk，`updates`+`subgraphs`，不触达真实 provider） | `backend/tests/test_support.py` |
| 系统 prompt | `DEFAULT_SYSTEM_PROMPT` 引导文件工具，并明确只有用户清晰要求业务结果时才使用业务 Skill；普通 PDF 请求不触发业务流程 | `runtime/agent.py` |
| prompt-cache 中间件 | **不新增自定义 cache middleware**。`create_deep_agent` 已在尾栈自动挂 `AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore")`（`deepagents/graph.py`），给 system 末块与末个 tool 打 `cache_control={"type":"ephemeral","ttl":"5m"}`；因为 MiniMax 走 `ChatAnthropic`，该中间件对 MiniMax-M3 生效。固定前缀 = `DEFAULT_SYSTEM_PROMPT` + `default_tool_catalog()` tool schema + SDK 默认 deep-agent prompt，**不要**向其注入时间/run_id 等动态内容 | `runtime/agent.py` + `langchain_anthropic/middleware/prompt_caching.py`（库源） |
| usage 观测出口 | usage 不实现为 Agent middleware，而是复用 `execute_run` 的统一 `messages` 流出口：在 subagent 文本过滤之前从终态 chunk 的 `usage_metadata` 提取（`runtime/observability.py model_usage`），每个模型调用仅在非空时写一次 `model_usage` 事件（含 subagent 调用）；不写入 AgentState/checkpointer/store，不新增表 | `runtime/{execution,observability,runs}.py` + `api._usage_summary` |

### Brain / BrainFactory 边界（模块归属）

- `Brain` / `BrainFactory` 是 `runtime/agent.py` 内定义的 `Protocol`（`stream(payload, config, **kwargs)` / `create(*, resources, middleware, tools)`）；`DeepAgentsBrainFactory` 是其生产实现，`HarnessRuntime`（`runtime/execution.py`）持有并驱动它。
- `create_harness`（`runtime/execution.py`）装配：`tools=default_tool_catalog()`、`brain_factory=DeepAgentsBrainFactory()`；`execute_run` 把 `runtime_middlewares()` 与 `tools.as_list()` 一起传给 `brain_factory.create(...)`。
- 旧 `Hands` Protocol / `ToolStatusHands` / `ToolStatusMiddleware` 已删除；工具遥测改由 `ToolTelemetry`（`wrap_tool_call`）实现，`tool_status` 事件改为 `tool_execution`（三态）。

### Skills / Subagents 边界

- `/skills/` 映射到 `skills/`（两个内置 Skill 包：`philipswgqimport`、`tecanimport`，各含 `SKILL.md` + `references/` + `assets/` 模板 + `scripts/`）；字段/规则只下沉一层 `references/`，模板位于各 Skill 的 `assets/`。
- `philips-wgq-extractor-a/b` 与 `tecan-extractor-a/b` 是创建 DeepAgent 时一次性注册的声明式 SubAgent（`workflow_subagents()`，`runtime/agent.py`）；每个只获得对应 extraction 保存工具，内置文件写入被 `_READ_ONLY_FILES`（`FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")`）拒绝；每个 SubAgent 通过 `_extractor(...)` **显式注入** `runtime_middlewares()`（声明式 SubAgent 不继承主 Agent middleware）。
- A/B 并行、C 回查和裁决由 Skill 指令驱动；业务模块不扫描 session、上传历史或最近文件，所有 `generate_*_import` 只消费显式 artifact 路径。
- `execute_run` 按 stream metadata 的 `lc_agent_name` 丢弃 subagent thinking/text token，只对外暴露主 agent 模型 token；usage 提取在过滤之前完成，故 subagent 模型成本仍计入。
- 锁定的 `deepagents==0.6.12` 没有官方新文档中的 `harness_profile` 构造参数；代码用该版本公开的 profile 注册 API（`register_harness_profile("anthropic", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))`）禁用默认 general-purpose subagent。

环境变量（**仅键名 / 用途，不含值**）：

| 键 | 用途 | 消费者 |
|---|---|---|
| `MINIMAX_MODEL` | 传给 `init_chat_model` 的模型名（`anthropic:` 前缀）；`.env.example` 默认 `MiniMax-M3` | `runtime/agent.py` |
| `MINIMAX_API_KEY` | Anthropic 兼容客户端 API key | `runtime/agent.py` |
| `MINIMAX_BASE_URL` | Anthropic 兼容端点 base URL（实际可指向 MiniMax） | `runtime/agent.py` |

`backend/.env` 由 `runtime/agent.py` 与 `integrations/mineru.py` 在**导入时** `load_dotenv(...)` 加载。完整键清单见 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) §5。

**Oracle thick client 部署依赖**：Philips 法定单位查询（`skills/philipswgqimport/scripts/tools.py` 的 `generate_philips_wgq_import`）走 `oracledb` thick mode，需 `ORACLE_CLIENT_LIB_DIR` 指向外部 Oracle instant client 目录（仓库不存放）；配置缺失或查询失败时优雅降级——单位字段填「需确认」并返回人工校验项，不崩溃。`ORACLE_DSN`/`ORACLE_USERNAME`/`ORACLE_PASSWORD` 三者齐备 + `ORACLE_CLIENT_LIB_DIR` 指向有效 instant client 才会发起查询。Tecan Skill 不消费任何 Oracle 键。详见 `backend/.planning/codebase/CONCERNS.md` §8。

## 8. 未证实的跨系统关系 / 需确认

当前系统文档未确认其它子项目或跨系统调用方；已删除的旧 session 接口见 §1。

证据与建议见 [`backend/.planning/codebase/CONCERNS.md`](backend/.planning/codebase/CONCERNS.md) 与 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md)。

## 9. 任务排查建议 / 可扩展集成入口

- **改 HTTP 契约**：先改本文件 §1/§2，再回看 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) §1 与 `api.py`；不要重新引入 SSE、`POST /files` 或旧 `message` 字段。
- **替换 LLM provider**：实现新的 `BrainFactory`（`runtime/agent.py` 的 `Brain` Protocol），通过 `create_harness` 注入；当前生产 brain 强耦合 Anthropic 协议与 `thinking` 参数，切换 provider 需同步调整 stream chunk 解析。
- **新增工具 / 新增 Skill**：实现 callable ToolHandler，在 `runtime/tools.py` 的 `default_tool_catalog()` 追加一行 import + 一行注册（静态注册，不自动扫描）。新增 Skill = 新增一个 `skills/<skill>/` Skill 包目录（含 `SKILL.md` + `references/` + `assets/` + `scripts/{tools.py,documents.py}`，目录名同时满足 Skill 命名与 Python 包标识符规则）+ 同步在 `pyproject.toml` 的 `[tool.setuptools.package-data]` 追加该 Skill 的 `SKILL.md`/`references`/`assets`。`ToolTelemetry` 会自动为每个工具发 `tool_execution` 三态事件。
- **改业务 Skill/工具**：先看 `skills/<skill>/SKILL.md` 与 `references/`，再改对应 `scripts/tools.py` / `scripts/documents.py`；维持显式 artifact 路径、统一 `input_problems` 形状与「每 Skill 2 个 Tool」边界。
- **新增持久化通道**：在 `runtime/resources.py` 的 `CompositeBackend` 路由表追加前缀；不要直接在 execution 写文件。
- **加鉴权 / CORS**：当前 `api.py` 未注册任何 auth/CORS middleware（已确认缺失）；若需开放给浏览器，需显式补 `CORSMiddleware` 和对应配置键。
- **写系统文档**：只记录配置键、边界和消费者，不把本地 `.env` 的真实值、连接串或服务地址写回长期文档。
- **跨进程部署**：单飞锁仅进程内语义；多 worker 部署前需引入跨进程锁或单进程约束。
- **验证入口**：HTTP 行为变更（含 cancel）已被 `backend/tests/test_api.py` 用 `TestClient` 覆盖；backend 代码变更按影响范围运行对应 `cd backend && python -m tests.<name>` 脚本（**非 pytest**）。
- **部署 Philips Oracle**：确认 `ORACLE_CLIENT_LIB_DIR` 指向有效 instant client 目录，且 `ORACLE_DSN/USERNAME/PASSWORD` 齐备；验证步骤见 `backend/.planning/codebase/CONCERNS.md` §8。
- **部署切换**：新 schema 使用 UTC ISO-8601 毫秒时间，无迁移代码；切换部署时停服务、清空整个 `backend/data/`（runs/events/checkpoints/store/uploads/downloads）。
