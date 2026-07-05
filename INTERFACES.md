# INTERFACES

## 1. HTTP API

### 1.1 `POST /runs`

- 请求 JSON：`{"message": "...", "session_id": null | "..."}`
- 行为：
  - `session_id` 为空时，服务端生成新的 `uuid.uuid4().hex`
  - 同一 `session_id` 若已有运行中的 run，返回 `409`
  - 接受请求后立刻写入 run ledger，并在后台线程执行
- 成功响应：

```json
{
  "run_id": "...",
  "session_id": "...",
  "status": "queued"
}
```

### 1.2 `GET /runs/{run_id}`

- 可选查询参数：`after_event_id=<int>`
- 成功响应：

```json
{
  "run": {
    "run_id": "...",
    "session_id": "...",
    "input_message": "...",
    "status": "queued|running|succeeded|failed",
    "created_at": "...",
    "updated_at": "...",
    "reply": null,
    "error": null
  },
  "events": [
    {
      "event_id": 1,
      "run_id": "...",
      "type": "status|thinking|text_delta|tool_status|values",
      "created_at": "...",
      "payload": {},
      "raw": {}
    }
  ]
}
```

- `after_event_id` 为空：返回该 run 的全部事件。
- `after_event_id` 有值：只返回 `event_id > after_event_id` 的增量事件。
- 未知 run：`404 {"error":"Unknown run: ..."}`。

### 1.3 `POST /files`

- `multipart/form-data` 上传字段名：`file`
- 文件保存到 `backend/data/artifacts/uploads/<uuid>_<filename>`
- 响应：

```json
{
  "file_path": "/artifacts/uploads/<uuid>_<filename>"
}
```

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

## 3. Brain 调用约定

`HarnessRuntime.execute_run` 统一用下面的调用形式驱动 Brain：

```python
brain.stream(
    {"messages": [{"role": "user", "content": message}]},
    config={"configurable": {"thread_id": session_id}},
    stream_mode=["messages", "custom", "values"],
    version="v2",
)
```

约束：

- payload 只包含当前 user message。
- 短期上下文完全依赖 LangGraph `checkpointer` + `thread_id=session_id`。
- `custom` channel 专门承接 `ToolStatusMiddleware` 通过 `get_stream_writer()` 发出的 `tool_status`。

## 4. 存储接口

`resources.py` 暴露三类持久资源：

- `resources.runs`：`SqliteRunLedger`
- `resources.store`：`SqliteStore`
- `resources.checkpointer`：`SqliteSaver`

run ledger 的数据库文件固定为：

- `backend/data/dsagents_runs.db`

大 event payload/raw 的外溢目录固定为：

- `backend/data/artifacts/run-events/`

## 5. 并发与恢复

- 运行中单飞：`api.py` 只在进程内按 `session_id` 持有 `threading.Lock`
- 启动恢复：服务启动时将遗留 `queued` / `running` run 标记为 `failed("执行已中断，请重试")`
- 不提供：
  - 旧 `/sessions/messages*`
  - `GET /sessions/{session_id}/runs`
  - 跨进程锁
  - 队列
  - 清理策略
  - 历史数据迁移
