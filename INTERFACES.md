# INTERFACES

> 系统级接口边界。已确认契约直接陈述；证据不足或推断的标 **需确认**。底层契约细节（完整请求/响应 JSON 形状、表结构、配置键清单）以 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) 为准。

## 1. HTTP API 边界

三个端点（入口模块 `api.py`，`app = create_app()`，预期 `uvicorn api:app` 拉起）。**当前无 SSE / `StreamingResponse`**，事件获取靠轮询。

| 方法 / 路径 | 入参 | 行为 | 返回 |
|---|---|---|---|
| `POST /runs` | `{"message": str, "session_id": str\|null}` | `session_id` 为空生成 `uuid4().hex`；同 session 已有运行中 run → `409`；写 ledger 后起 daemon 线程执行 | `200 {"run_id","session_id","status":"queued"}`；冲突 `409 {"error":"该会话正在运行","active_run_id"}` |
| `GET /runs/{run_id}` | query `after_event_id: int\|null` | 读 run 快照 + run events（支持增量游标） | `200 {"run":{...},"events":[...]}`；未知 run `404 {"error":"Unknown run: ..."}` |
| `POST /files` | multipart `file: UploadFile` | 落到 `data/artifacts/uploads/<uuid>_<cleaned_name>`；返回虚拟路径 | `200 {"file_path":"/artifacts/uploads/..."}` |

- `after_event_id` 游标：为空返回全部事件；有值只返回 `event_id > after_event_id` 的增量事件。
- 事件类型固定五类：`status` / `thinking` / `text_delta` / `tool_status` / `values`。
- 完整请求/响应 JSON 形状与 lifespan 行为见 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) §1。

明确**已删除**的旧接口（不要引用）：`POST /sessions/messages`、`POST /sessions/messages/stream`、`POST /sessions/messages/runs`、`GET /sessions/{session_id}/runs`、`from session import run_session`。

## 2. 程序内接口

仓库不再提供 `from session import run_session` 这类 one-shot 入口。当前程序内调用路径是组合式：

```python
from resources import AgentResources, ResourceConfig
from harness import create_harness

with AgentResources(ResourceConfig()) as resources:
    harness = create_harness(resources)
    run = resources.runs.create_run("run-id", "session-id", "hello")
    for _event in harness.execute_run("hello", "session-id", run.run_id):
        pass
    snapshot = resources.runs.get_run(run.run_id)
```

`harness.execute_run(message, session_id, run_id)` 返回 `Iterator[RunEvent]`。`self_check.py` 走这条路径。

## 3. Brain 调用约定

`HarnessRuntime.execute_run` 统一用下面的调用形式驱动 Brain（已确认）：

```python
brain.stream(
    {"messages": [{"role": "user", "content": message}]},
    config={"configurable": {"thread_id": session_id}},
    stream_mode=["messages", "custom", "values"],
    version="v2",
)
```

约束：

- payload 只含当前 user message（多轮记忆依赖 checkpointer/store，不在 payload 重放）。
- `thread_id = session_id`（短期上下文键）。
- 三 channel 全部消费：`messages` → `thinking`/`text_delta`；`custom` → `tool_status`（来自 `ToolStatusMiddleware` 经 `get_stream_writer()`）；`values` → `values`（末位 assistant 文本作 reply）。
- raw 完整 v2 chunk 整体落库（`run_events.raw_*`）。

## 4. 存储接口边界

`AgentResources`（`resources.py`）暴露三类持久资源：

- `resources.runs`：`SqliteRunLedger`（标准库 `sqlite3`）
- `resources.store`：LangGraph `SqliteStore`
- `resources.checkpointer`：LangGraph `SqliteSaver`

固定三条**活跃** SQLite 通道（`runs`/`store`/`checkpoints`），完整文件→通道→写入方映射与表结构详见 [`backend/.planning/codebase/ARCHITECTURE.md`](backend/.planning/codebase/ARCHITECTURE.md) §7。大 run event payload/raw（默认 `max_inline_bytes=262_144`）外溢到 `data/artifacts/run-events/*.json`；`CompositeBackend` 路由规则同样见该文档。

## 5. LLM provider 边界

| 边界 | 实现 | 证据 |
|---|---|---|
| 生产 brain | `DeepAgentsBrainFactory`：`init_chat_model("anthropic:<MODEL>", api_key=<KEY>, base_url=<URL>, thinking={"type":"adaptive"})` → `ChatAnthropic`；注入 `create_deep_agent(...)` | `harness.py` |
| 自检 brain | `_FakeBrain` / `_FakeBrainFactory`（模拟 v2 stream chunk，不触达真实 provider） | `self_check.py` |

- 生产 Brain 强耦合 Anthropic 客户端协议与 `thinking={"type":"adaptive"}`；实际端点可指向 MiniMax（OpenAI/Anthropic 兼容）。
- 环境变量（仅键名 / 用途，不含值）：`MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 由 `harness.py` 在导入时 `load_dotenv` 读取。
- 完整键清单见 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) §2/§5。

## 6. 未证实的跨系统关系 / 需确认

| 项 | 状态 | 说明 |
|---|---|---|
| `backend/instantclient/`（Oracle Instant Client 19.31） | 需确认 | 约 109MB 被 git 跟踪；backend 代码无 `oracledb`/`cx_Oracle` import、不读 `ORACLE_*`/`ORACLE_CLIENT_LIB_DIR`。疑似遗留资产或计划中能力 |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | 需确认 | `.env` 配置但代码零引用（死配置，易误导） |
| `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS` | 需确认 | `.env.example` 含键，代码零引用 |
| `LANGSMITH_TRACING` / `LANGSMITH_ENDPOINT` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | 需确认 | `.env` 配置但代码零引用 |
| `CORS_ORIGINS` | 需确认 | `.env` 有键，但 `api.py` 未注册 `CORSMiddleware` → 浏览器跨域实际不被处理 |
| `data/dsagents_sessions.db` 与 `data/artifacts/session-events/` | 需确认 | 磁盘遗留，代码零引用（旧 session 时代孤儿文件） |

证据与建议见 [`backend/.planning/codebase/CONCERNS.md`](backend/.planning/codebase/CONCERNS.md) §1/§3/§9 与 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) §5/§7。

## 7. 任务排查建议 / 可扩展集成入口

- **改 HTTP 契约**：先改本文件 §1，再回看 [`backend/.planning/codebase/INTEGRATIONS.md`](backend/.planning/codebase/INTEGRATIONS.md) §1 与 `api.py`；不要重新引入 SSE 或 session 端点。
- **替换 LLM provider**：实现新的 `BrainFactory`（`harness.py` 的 `Brain` Protocol），通过 `create_harness` 注入；当前生产 brain 强耦合 Anthropic 协议与 `thinking` 参数，切换 provider 需同步调整 stream chunk 解析。
- **新增工具**：实现 `ToolHandler`（callable），注册进 `default_tool_catalog()`（`tools.py`）；`Hands` 中间件会自动发 `tool_status` 事件。
- **新增持久化通道**：在 `resources.py` 的 `CompositeBackend` 路由表追加前缀；不要直接在 harness 写文件。
- **加鉴权 / CORS**：当前 `api.py` 未注册任何 auth/CORS middleware（已确认缺失）；若需开放给浏览器，需显式补 `CORSMiddleware` 并激活 `CORS_ORIGINS`。
- **跨进程部署**：单飞锁仅进程内语义；多 worker 部署前需引入跨进程锁或单进程约束。
- **验证入口**：HTTP 行为变更已被 `self_check.py` 的 `_check_api` / `_check_startup_recovery`（`TestClient`）覆盖；backend 代码变更跑 `python backend/self_check.py` 必须看到结尾 `self-check passed`。
