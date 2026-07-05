# SYSTEM_MAP

## 1. 子项目职责

| 子项目 | 目录 | 当前职责 |
|--------|------|----------|
| backend | `backend/` | run-first agent runtime：提交 run、轮询 run、上传文件、维护 LangGraph checkpointer/store 与本地 run ledger |

## 2. 后端模块图

| 模块 | 文件 | 读/写边界 |
|------|------|-----------|
| Run Ledger | `backend/run_ledger.py` | 读写 `dsagents_runs.db` 与 `artifacts/run-events/` |
| Harness | `backend/harness.py` | 调 `brain.stream(...)`，把 raw chunk 规范化为 run 事件 |
| Hands | `backend/hands.py` | 只发 `tool_status` custom event |
| Resources | `backend/resources.py` | 装配 run ledger、LangGraph store、LangGraph checkpointer、`CompositeBackend` |
| Tools | `backend/tools.py` | `parse_document` 与工具目录 |
| HTTP | `backend/api.py` | `POST /runs`、`GET /runs/{run_id}`、`POST /files` |

## 3. 主调用链

```text
POST /runs
  -> create_run(run_id, session_id, input_message)
  -> background thread
  -> HarnessRuntime.execute_run(message, session_id, run_id)
      -> brain.stream(
           {"messages": [{"role":"user","content": message}]},
           config={"configurable":{"thread_id": session_id}},
           stream_mode=["messages","custom","values"],
           version="v2",
         )
      -> run_events:
           status
           thinking
           text_delta
           tool_status
           values
      -> final status: succeeded | failed

GET /runs/{run_id}
  -> read runs row
  -> read run_events (all or after_event_id cursor)

POST /files
  -> backend/data/artifacts/uploads/<uuid>_<filename>
  -> /artifacts/uploads/<uuid>_<filename>
```

## 4. 存储图

| 文件 | 用途 |
|------|------|
| `backend/data/dsagents_runs.db` | run ledger：`runs` + `run_events` |
| `backend/data/dsagents_store.db` | LangGraph store |
| `backend/data/dsagents_checkpoints.db` | LangGraph checkpointer |
| `backend/data/artifacts/run-events/*.json` | 超大 raw/payload 外溢 |
| `backend/data/artifacts/uploads/*` | 上传文件 |

## 5. 当前接口面

- `POST /runs`
- `GET /runs/{run_id}`
- `POST /files`

明确删除：

- `POST /sessions/messages`
- `POST /sessions/messages/stream`
- `POST /sessions/messages/runs`
- `GET /sessions/{session_id}/runs`
- `from session import run_session`

## 6. 修改前阅读建议

- 改 run 状态/事件：先读 `backend/run_ledger.py`
- 改模型流式行为：先读 `backend/harness.py`
- 改 tool 进度事件：先读 `backend/hands.py`
- 改资源路径或 SQLite：先读 `backend/resources.py`
- 改 HTTP 契约：先读 `backend/api.py` 和 [INTERFACES.md](../INTERFACES.md)

## 7. 当前风险

- 单飞锁只在单进程内生效；多 worker 部署需额外方案。
- run raw chunk 永久保留，调试友好，但会增加本地存储占用。
- 程序内没有稳定的一步式 helper；默认使用 HTTP `POST /runs`。
