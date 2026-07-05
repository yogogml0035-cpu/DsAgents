# INTEGRATIONS

## 1. HTTP 集成

### `POST /runs`

- 请求：`{"message": "...", "session_id": null | "..."}`
- `session_id` 为空时生成新的 `uuid`
- 创建 run ledger 记录后起后台线程
- 返回：`{"run_id","session_id","status":"queued"}`

### `GET /runs/{run_id}`

- 读取 `runs` 快照
- 读取 `run_events`
- 支持 `after_event_id` 增量游标
- 未知 run 返回 `404 {"error":"Unknown run: ..."}`

### `POST /files`

- 上传后落到 `artifacts/uploads/`
- 返回虚拟路径 `/artifacts/uploads/...`

## 2. LangGraph 集成

Harness 统一使用 classic streaming API：

```python
brain.stream(
    {"messages": [{"role": "user", "content": message}]},
    config={"configurable": {"thread_id": session_id}},
    stream_mode=["messages", "custom", "values"],
    version="v2",
)
```

事实：

- payload 只含当前 user message
- `thread_id=session_id`
- `messages` / `custom` / `values` 三条 channel 都被消费
- raw 保存完整 v2 chunk，而不是只存 `chunk["data"]`

## 3. Middleware 集成

`hands.py` 只保留 `ToolStatusMiddleware`：

- `started`
- `completed`
- `error`

事件通过 `get_stream_writer()` 进入 `custom` channel，随后由 harness 记为 `tool_status` run event。

## 4. 存储集成

`AgentResources` 启动时装配：

- `resources.runs = SqliteRunLedger(...)`
- `resources.store = SqliteStore(...)`
- `resources.checkpointer = SqliteSaver(...)`
- `resources.backend = CompositeBackend(...)`

`SqliteRunLedger`：

- 小 JSON 直接入 `dsagents_runs.db`
- 大 JSON 外溢到 `artifacts/run-events/*.json`
- 启动恢复会把遗留 `queued/running` 标记为 `failed`

## 5. 并发集成

HTTP 层的单飞语义是：

- 锁粒度：`session_id`
- 实现：`threading.Lock`
- 范围：当前 FastAPI 进程内

没有：

- Redis 锁
- 数据库锁
- 队列
- worker 恢复器

## 6. Provider 集成

- MiniMax：由 `harness.py` 读取 `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL`
- MinerU：由 `tools.py` 读取 `MINERU_*`

`.env` 不再依赖已删除的 `session.py`，改为由 `harness.py` 与 `tools.py` 自行加载。
