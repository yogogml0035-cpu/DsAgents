# INTERFACES

> 系统级接口边界。已确认契约直接陈述；证据不足或推断的标 **需确认**。底层契约细节（完整请求/响应 JSON 形状、表结构、配置键清单）以 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) 为准。
> 本轮刷新（2026-07-11）已对齐 backend 全部事实文档（同日刷新，HEAD `7126b83`）：三个 HTTP 端点契约不变（`RunRequest` 用 `ConfigDict(extra="forbid")`），Skills/Subagents、八个 Philips/Tecan artifact 工具合同、MinerU 任务式接口、上传/artifacts 边界与 Oracle 优雅降级均与代码一致。

## 1. HTTP API 边界

三个端点（入口模块 `api.py`，`create_app(*, resource_config=None, harness_factory=create_harness)` 返回 `FastAPI(lifespan=lifespan)`，模块级 `app = create_app()`；预期 `uvicorn api:app` 拉起，默认 `--port 8500`）。**当前无 SSE / `StreamingResponse`**，事件获取靠轮询。

| 方法 / 路径 | 入参 | 行为 | 返回 |
|---|---|---|---|
| `POST /runs` | `{"session_id": str\|null, "messages": [{"role": str, "content": [{"type":"text","text":str} \| {"type":"artifact","path":str}]}...]}` | `session_id` 为空生成 `uuid4().hex`；同 session 已有运行中 run → `409`；写 ledger 后起 daemon 线程执行 | `200 {"run_id","session_id","status":"queued"}`；校验失败 `422`；冲突 `409 {"error":"该会话正在运行","active_run_id"}` |
| `GET /runs/{run_id}` | query `after_event_id: int\|null` | 读 run 快照 + run events（支持增量游标）+ 当前 run 全局最新非 `status`/非 `model_usage` 事件 + 该 run 全部 `model_usage` 事件汇总出的 `usage` | `200 {"run":{...},"events":[...],"latest_content_event":{...}\|null,"usage":{...}\|null}`；未知 run `404 {"error":"Unknown run: ..."}`。`usage` 含 `model_calls`、四类 token 总量、`cache_hit_rate`（无输入为 `null`）、`estimated_cost_cny` / `estimated_savings_cny`（按调用 input ≤/>512k 分 tier 后汇总，`pricing_as_of` 标日期）、估算说明与 `by_agent` 分项；模型不可计价时金额为 `null`，token 仍完整；无模型调用时 `usage` 为 `null` |
| `POST /upload` | multipart `files: UploadFile[]`（字段名固定 `files`，支持 1 个或多个） | 落到 `data/artifacts/uploads/<cleaned-stem>_<upload-ts>(_n).ext`；只保存文件，不解析 | `200 {"files":[{"file_path":"/artifacts/uploads/...","name":"<原名>","mime_type":"<mime-or-application/octet-stream>","size":123}]}` |

- `POST /runs` **不再支持**旧 `{"message":"..."}` 请求体（`RunRequest` 用 `ConfigDict(extra="forbid")`）；Pydantic/FastAPI 直接返回校验错误（`422`）。
- `artifact` block 是**项目 API 语义**，不是直接发给 LangChain 的标准多模态 block。进入 Brain 前会被转成文本提示：`Uploaded artifact: /artifacts/uploads/...`，再由 agent 决定何时用 `read_file` 或 `parse_documents`。
- `after_event_id` 游标：为空返回全部事件；有值只返回 `event_id > after_event_id` 的增量事件。
- `latest_content_event`：始终返回当前 run 全局最新的非 `status`/非 `model_usage` 事件；没有此类事件时为 `null`；**不受** `after_event_id` 影响。
- `usage`：始终返回该 run 全部 `model_usage` 事件汇总出的 usage 块；**同样不受** `after_event_id` 影响（`after_event_id` 只裁剪 `events[]`）。
- 事件类型固定八类：`status` / `thinking` / `text_delta` / `assistant_message` / `tool_call` / `tool_status` / `tool_result` / `model_usage`（前七类为业务事件，`model_usage` 为 prompt-cache/成本观测事件）。
- 成功 run 的最终 `latest_content_event` 通常是 `assistant_message`；`tool_call` / `tool_result` / `assistant_message` 都由 `raw.type=="values"` 的 snapshot 派生，`thinking` / `text_delta` / `model_usage` 由 `raw.type=="messages"` 派生（`model_usage` 在 subagent 文本过滤之前提取，覆盖主 agent 与 subagent 调用）；`values` 本身不是公开事件类型；最终 AIMessage 同时含 `thinking` 与 `text` block 时，`assistant_message.payload` 会带上最后一个 `thinking` 文本和最终 `text`。
- 临时 subagent 的 thinking/text token 不进入公开事件；`task` 调用、工具结果和 artifact 路径仍可从现有事件读取。
- 常见办公文件和任意图片都可以通过 `POST /upload` 保存；能否被解析或理解取决于 DeepAgents `read_file`、`parse_documents`、MinerU 和模型多模态能力。

明确**已删除**的旧接口（不要引用）：`POST /files`、`POST /sessions/messages`、`POST /sessions/messages/stream`、`POST /sessions/messages/runs`、`GET /sessions/{session_id}/runs`、`from session import run_session`。

## 2. 程序内接口

仓库不再提供 `from session import run_session` 这类 one-shot 入口。当前程序内调用路径是组合式：

```python
import json

from resources import AgentResources, ResourceConfig
from harness import create_harness

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

`harness.execute_run(messages, session_id, run_id)` 返回 `Iterator[RunEvent]`。实际断言分布在 `backend/tests/test_*.py`，按影响范围直接运行对应脚本。

## 3. Brain 调用约定

`HarnessRuntime.execute_run` 统一用下面的调用形式驱动 Brain（已确认）：

```python
brain.stream(
    {"messages": normalized_messages},
    config={"configurable": {"thread_id": session_id}},
    stream_mode=["messages", "custom", "values"],
    version="v2",
)
```

约束：

- payload 只含当前请求里的 `messages[]`，不重放本地 session 历史。
- `thread_id = session_id`（短期上下文键）。
- `text` block 原样保留；`artifact` block 转成文本路径提示后再传给 Brain。
- 三 channel 全部消费：`messages` → `thinking`/`text_delta`；`custom` → `tool_status`（来自 `ToolStatusMiddleware` 经 `get_stream_writer()`）；`values` snapshot → `tool_call` / `tool_result` / `assistant_message`（保留同条 AIMessage 最后一个 `thinking` 文本和最终 `text`），同时末位 assistant 文本仍作 `runs.reply` 候选。
- `messages` metadata 中 `lc_agent_name` 非主 agent 时跳过 token 事件；该过滤不改变 snapshot 派生事件。
- raw 完整 v2 chunk 整体落库（`run_events.raw_*`）。
- LangGraph classic `stream(..., stream_mode=["messages","custom","values"], version="v2")` 仍是当前契约；`thread_id=session_id` 只作为 checkpointer 上下文键，不参与 run event 查询。

## 4. Skills / 业务工具边界

- `DeepAgentsBrainFactory` 挂载 `/skills/`，注册 Philips/Tecan 各两个临时 extractor；业务意图由模型依据用户目标选择，不按文件名硬路由，普通 PDF 请求不触发业务 Skill。
- 两个业务各注册四个 callable：保存 extraction、构建 canonical、保存 adjudication、生成文档。A/B/C 使用同一 extraction 合同，builder 返回 `canonical` / `needs_c` / `needs_adjudication` / `needs_input`。
- Philips extraction 固定为 `workflow/extractor/source_artifact/logistics/items`，物流四字段、商品九字段均使用 `{value, confidence}`；Tecan 使用相同 envelope，但物流只含 `pieces/gross_weight` 且 `items=[]`。
- canonical 固定含 `workflow/source_artifacts/logistics/items/manual_checks` 加业务专属字段；不接受旧 envelope。generator 唯一业务参数是 canonical artifact 路径。
- 所有输入路径必须显式指向 `/artifacts/...`；业务 JSON/Excel 写到 `/artifacts/downloads/` 的唯一新文件。流程中间状态不增加 HTTP、数据库或恢复接口。
- 8 个业务工具名清单（全部由 `tools.py` 的 `default_tool_catalog()` 注册）：
  - Philips：`save_philips_wgq_extraction`、`save_philips_wgq_adjudication`、`build_philips_wgq_canonical`、`generate_philips_wgq_documents`
  - Tecan：`save_tecan_extraction`、`save_tecan_adjudication`、`build_tecan_canonical`、`generate_tecan_documents`
- 完整的工具入参契约（关键入参、返回结构、状态机四态）见 `backend/.planning/codebase/INTEGRATIONS.md` §6。

## 5. 存储接口边界

`AgentResources`（`resources.py`）暴露三类持久资源：

- `resources.runs`：`SqliteRunLedger`（标准库 `sqlite3`；支持 `create_run` / `get_run` / `get_run_events` / `get_latest_content_event`）
- `resources.store`：LangGraph `SqliteStore`
- `resources.checkpointer`：LangGraph `SqliteSaver`

固定三条**逻辑** SQLite 通道（`runs`/`store`/`checkpoints`，文件按需创建），完整文件→通道→写入方映射与表结构详见 [`backend/.planning/codebase/ARCHITECTURE.md`](backend/.planning/codebase/ARCHITECTURE.md) §7。run 输入快照字段现为 `input_messages_json`（保存 `messages[]` JSON 字符串）。大 run event payload/raw（默认 `max_inline_bytes=262_144`）外溢到 `data/internal/run-events/*.json`（按需创建）；`CompositeBackend` 路由规则同样见该文档。

## 6. LLM provider 边界

| 边界 | 实现 | 证据 |
|---|---|---|
| 生产 brain | `DeepAgentsBrainFactory` → `init_chat_model("anthropic:<MODEL>", ...)` 构造 Anthropic 兼容 `ChatAnthropic`；`create_deep_agent(...)` 同时注入 `skills=["/skills/"]`、四个临时 extractors、`/skills/**` 写禁令、主 agent 名（`MAIN_AGENT_NAME = "dsagents-main"`）与十个默认工具 | `harness.py` / `subagents.py` / `tools.py` |
| 本地测试 brain | `FakeBrain` / `FakeBrainFactory`（模拟 v2 stream chunk，不触达真实 provider） | `backend/tests/test_support.py` |
| prompt-cache 中间件 | **不新增自定义 cache middleware**；`create_deep_agent` 尾栈自动挂 `AnthropicPromptCachingMiddleware`，因 MiniMax 走 `ChatAnthropic` 对 MiniMax-M3 生效；固定前缀不可注入动态内容（时间/run_id 等） | `harness.py` + langchain_anthropic 库源 |

- 生产 Brain 强耦合 Anthropic 客户端协议与 `thinking={"type":"adaptive"}`；实际端点可指向 MiniMax（OpenAI/Anthropic 兼容）。
- `register_harness_profile("anthropic", HarnessProfile(general_purpose_subagent=...enabled=False))` 在进程级全局禁用 DeepAgents 默认的第五个 general-purpose subagent，只保留 `workflow_subagents()` 的四个 extractor。锁定的 `deepagents==0.6.12` 不支持构造参数形式的 `harness_profile=...`，需用该 profile 注册 API；升级依赖时需重新核对。
- 环境变量（仅键名 / 用途，不含值）：`MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 由 `harness.py` 在导入时 `load_dotenv` 读取。
- 完整键清单见 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) §2/§5。
- Philips Oracle 是独立可选集成，只消费 `ORACLE_*` 键；配置缺失或查询失败继续生成并标人工校验。

**Oracle thick client 部署依赖**：`_oracle_units` 走 `oracledb` thick mode，需 `ORACLE_CLIENT_LIB_DIR` 指向外部 Oracle instant client 目录（仓库已不再存放，约 109MB）；缺失或初始化失败时优雅降级，核注清单缺法定单位字段。详见 `backend/.planning/codebase/CONCERNS.md` §8。

## 7. 未证实的跨系统关系 / 需确认

当前系统文档未确认其它子项目或跨系统调用方；已删除的旧 session 接口见 §1。

证据与建议见 [`backend/.planning/codebase/CONCERNS.md`](backend/.planning/codebase/CONCERNS.md) 与 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md)。

## 8. 任务排查建议 / 可扩展集成入口

- **改 HTTP 契约**：先改本文件 §1，再回看 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) §1 与 `api.py`；不要重新引入 SSE、`POST /files` 或旧 `message` 字段。
- **替换 LLM provider**：实现新的 `BrainFactory`（`harness.py` 的 `Brain` Protocol），通过 `create_harness` 注入；当前生产 brain 强耦合 Anthropic 协议与 `thinking` 参数，切换 provider 需同步调整 stream chunk 解析。
- **新增工具**：实现 `ToolHandler`（callable），注册进 `default_tool_catalog()`（`tools.py`）；`Hands` 中间件会自动发 `tool_status` 事件。
- **改业务 Skill/工具**：先看 `backend/skills/<workflow>/SKILL.md` 与对应业务模块；维持显式 artifact 路径、当前严格合同和生成器单参数边界。
- **新增持久化通道**：在 `resources.py` 的 `CompositeBackend` 路由表追加前缀；不要直接在 harness 写文件。
- **加鉴权 / CORS**：当前 `api.py` 未注册任何 auth/CORS middleware（已确认缺失）；若需开放给浏览器，需显式补 `CORSMiddleware` 和对应配置键。
- **写系统文档**：只记录配置键、边界和消费者，不把本地 `.env` 的真实值、连接串或服务地址写回长期文档。
- **跨进程部署**：单飞锁仅进程内语义；多 worker 部署前需引入跨进程锁或单进程约束。
- **验证入口**：HTTP 行为变更已被 `backend/tests/test_api.py` 覆盖；backend 代码变更按影响范围运行对应 `cd backend && python -m tests.test_xxx` 脚本。
- **部署 Philips Oracle**：确认 `ORACLE_CLIENT_LIB_DIR` 指向有效 instant client 目录，且 `ORACLE_DSN/USERNAME/PASSWORD` 齐备；验证步骤见 `backend/.planning/codebase/CONCERNS.md` §8。
