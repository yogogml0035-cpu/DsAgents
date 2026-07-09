# INTERFACES

> 系统级接口边界。已确认契约直接陈述；证据不足或推断的标 **需确认**。底层契约细节（完整请求/响应 JSON 形状、表结构、配置键清单）以 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) 为准。
> 本轮刷新（2026-07-09）已核对当前 HEAD `1e8cf94` 后的工作树：`extract_archives` 工具 + `parse_documents` 默认保存 task 级 JSON、按需保存 ZIP；artifact 存储命名重构；文档解析改为批量处理。

## 1. HTTP API 边界

三个端点（入口模块 `api.py`，`create_app(*, resource_config=None, harness_factory=create_harness)` 返回 `FastAPI(lifespan=lifespan)`，模块级 `app = create_app()`；预期 `uvicorn api:app` 拉起，默认 `--port 8500`）。**当前无 SSE / `StreamingResponse`**，事件获取靠轮询。

| 方法 / 路径 | 入参 | 行为 | 返回 |
|---|---|---|---|
| `POST /runs` | `{"session_id": str\|null, "messages": [{"role": str, "content": [{"type":"text","text":str} \| {"type":"artifact","path":str}]}...]}` | `session_id` 为空生成 `uuid4().hex`；同 session 已有运行中 run → `409`；写 ledger 后起 daemon 线程执行 | `200 {"run_id","session_id","status":"queued"}`；校验失败 `422`；冲突 `409 {"error":"该会话正在运行","active_run_id"}` |
| `GET /runs/{run_id}` | query `after_event_id: int\|null` | 读 run 快照 + run events（支持增量游标）+ 当前 run 全局最新非 `status` 事件 | `200 {"run":{...},"events":[...],"latest_content_event":{...}\|null}`；未知 run `404 {"error":"Unknown run: ..."}` |
| `POST /upload` | multipart `files: UploadFile[]`（字段名固定 `files`，支持 1 个或多个） | 落到 `data/artifacts/uploads/<uuid>_<cleaned_name>`；只保存文件，不解析 | `200 {"files":[{"file_path":"/artifacts/uploads/...","name":"<原名>","mime_type":"<mime-or-application/octet-stream>","size":123}]}` |

- `POST /runs` **不再支持**旧 `{"message":"..."}` 请求体；Pydantic/FastAPI 直接返回校验错误。
- `artifact` block 是**项目 API 语义**，不是直接发给 LangChain 的标准多模态 block。进入 Brain 前会被转成文本提示：`Uploaded artifact: /artifacts/uploads/...`，再由 agent 决定何时用 `read_file` 或 `parse_documents`。
- `after_event_id` 游标：为空返回全部事件；有值只返回 `event_id > after_event_id` 的增量事件。
- `latest_content_event`：始终返回当前 run 全局最新的非 `status` 事件；没有非 `status` 事件时为 `null`；**不受** `after_event_id` 影响。
- 事件类型固定七类：`status` / `thinking` / `text_delta` / `assistant_message` / `tool_call` / `tool_status` / `tool_result`。
- 成功 run 的最终 `latest_content_event` 通常是 `assistant_message`；`tool_call` / `tool_result` / `assistant_message` 都由 `raw.type=="values"` 的 snapshot 派生，`values` 本身不是公开事件类型；最终 AIMessage 同时含 `thinking` 与 `text` block 时，`assistant_message.payload` 会带上最后一个 `thinking` 文本和最终 `text`。
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
- raw 完整 v2 chunk 整体落库（`run_events.raw_*`）。
- LangGraph classic `stream(..., stream_mode=["messages","custom","values"], version="v2")` 仍是当前契约；`thread_id=session_id` 只作为 checkpointer 上下文键，不参与 run event 查询。

## 4. 存储接口边界

`AgentResources`（`resources.py`）暴露三类持久资源：

- `resources.runs`：`SqliteRunLedger`（标准库 `sqlite3`；支持 `create_run` / `get_run` / `get_run_events` / `get_latest_content_event`）
- `resources.store`：LangGraph `SqliteStore`
- `resources.checkpointer`：LangGraph `SqliteSaver`

固定三条**逻辑** SQLite 通道（`runs`/`store`/`checkpoints`，文件按需创建），完整文件→通道→写入方映射与表结构详见 [`backend/.planning/codebase/ARCHITECTURE.md`](backend/.planning/codebase/ARCHITECTURE.md) §7。run 输入快照字段现为 `input_messages_json`（保存 `messages[]` JSON 字符串）。大 run event payload/raw（默认 `max_inline_bytes=262_144`）外溢到 `data/internal/run-events/*.json`（按需创建）；`CompositeBackend` 路由规则同样见该文档。

## 5. LLM provider 边界

| 边界 | 实现 | 证据 |
|---|---|---|
| 生产 brain | `DeepAgentsBrainFactory`：`init_chat_model("anthropic:<MODEL>", api_key=<KEY>, base_url=<URL>, thinking={"type":"adaptive"})` → `ChatAnthropic`；注入 `create_deep_agent(...)` | `harness.py` |
| 本地测试 brain | `FakeBrain` / `FakeBrainFactory`（模拟 v2 stream chunk，不触达真实 provider） | `backend/tests/test_support.py` |

- 生产 Brain 强耦合 Anthropic 客户端协议与 `thinking={"type":"adaptive"}`；实际端点可指向 MiniMax（OpenAI/Anthropic 兼容）。
- 环境变量（仅键名 / 用途，不含值）：`MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 由 `harness.py` 在导入时 `load_dotenv` 读取。
- 完整键清单见 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) §2/§5。

## 6. 未证实的跨系统关系 / 需确认

当前系统文档未确认其它子项目或跨系统调用方；已删除的旧 session 接口见 §1。

证据与建议见 [`backend/.planning/codebase/CONCERNS.md`](backend/.planning/codebase/CONCERNS.md) 与 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md)。

## 7. 任务排查建议 / 可扩展集成入口

- **改 HTTP 契约**：先改本文件 §1，再回看 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) §1 与 `api.py`；不要重新引入 SSE、`POST /files` 或旧 `message` 字段。
- **替换 LLM provider**：实现新的 `BrainFactory`（`harness.py` 的 `Brain` Protocol），通过 `create_harness` 注入；当前生产 brain 强耦合 Anthropic 协议与 `thinking` 参数，切换 provider 需同步调整 stream chunk 解析。
- **新增工具**：实现 `ToolHandler`（callable），注册进 `default_tool_catalog()`（`tools.py`）；`Hands` 中间件会自动发 `tool_status` 事件。
- **新增持久化通道**：在 `resources.py` 的 `CompositeBackend` 路由表追加前缀；不要直接在 harness 写文件。
- **加鉴权 / CORS**：当前 `api.py` 未注册任何 auth/CORS middleware（已确认缺失）；若需开放给浏览器，需显式补 `CORSMiddleware` 和对应配置键。
- **写系统文档**：只记录配置键、边界和消费者，不把本地 `.env` 的真实值、连接串或服务地址写回长期文档。
- **跨进程部署**：单飞锁仅进程内语义；多 worker 部署前需引入跨进程锁或单进程约束。
- **验证入口**：HTTP 行为变更已被 `backend/tests/test_api.py` 覆盖；backend 代码变更按影响范围运行对应 `cd backend && python -m tests.test_xxx` 脚本。
