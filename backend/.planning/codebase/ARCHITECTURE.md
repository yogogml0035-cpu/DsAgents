# ARCHITECTURE

> 事实来源：当前 `backend/` 源码（run-first runtime）

## 1. 目标

`backend/` 是一个 Harness 级 agent runtime。当前版本的核心变化是：

- 短期上下文只交给 LangGraph `checkpointer` + `thread_id=session_id`
- 本地 SQLite 不再承担对话事实源角色
- 本地只保留窄用途 run ledger：输入、状态、规范化事件、完整 raw chunk

## 2. 稳定模块边界

| 模块 | 文件 | 真实职责 |
|------|------|----------|
| Run Ledger | `run_ledger.py` | `dsagents_runs.db` 中的 `runs` / `run_events`，以及大 payload/raw 外溢 |
| Harness | `harness.py` | 组装 Brain / Tools / Middleware，执行 `brain.stream(...)`，规范化 chunk |
| Hands | `hands.py` | 只发 `tool_status` custom event |
| Resources | `resources.py` | 装配 run ledger、LangGraph store、LangGraph checkpointer、`CompositeBackend` |
| Tools | `tools.py` | 当前业务工具 `parse_document` |
| HTTP | `api.py` | `POST /runs`、`GET /runs/{run_id}`、`POST /files` |

## 3. 主执行链

```text
POST /runs
  -> create_run(run_id, session_id, input_message)
  -> background thread
  -> HarnessRuntime.execute_run(message, session_id, run_id)
      -> emit status=running
      -> brain.stream(
           {"messages": [{"role":"user","content": message}]},
           config={"configurable":{"thread_id": session_id}},
           stream_mode=["messages","custom","values"],
           version="v2",
         )
      -> messages => thinking / text_delta
      -> custom   => tool_status
      -> values   => values
      -> final status => succeeded / failed
```

这里没有：

- `context_window`
- session event replay
- `RemoveMessage(REMOVE_ALL_MESSAGES)`
- `run_turn` / `stream_turn`
- model/tool trace 落库

## 4. 存储边界

`backend/data/` 固定包含三条独立持久化通道：

- `dsagents_runs.db`
- `dsagents_store.db`
- `dsagents_checkpoints.db`

其中 `dsagents_runs.db` 的表结构只有：

- `runs(run_id, session_id, input_message, status, created_at, updated_at, reply, error)`
- `run_events(event_id, run_id, type, created_at, payload_json, payload_artifact_path, raw_json, raw_artifact_path)`

大 JSON 外溢到：

- `backend/data/artifacts/run-events/*.json`

## 5. 运行约束

- `POST /runs` 立即返回 `queued`
- `GET /runs/{run_id}` 支持 `after_event_id` 增量拉取
- 同一 `session_id` 的并发保护只靠进程内 `threading.Lock`
- 启动时会把遗留 `queued` / `running` run 标记为 `failed("执行已中断，请重试")`

## 6. 配置加载

`.env` 现在由两个模块在导入时加载：

- `harness.py`
- `tools.py`

这样即使 `session.py` 已删除，MiniMax / MinerU 相关环境变量仍会在正常调用路径中被读取。
