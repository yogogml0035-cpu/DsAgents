# backend 架构与约定

## 核心变化

`backend/` 已切到 run-first：

- 发送给 Brain 的 payload 只包含当前 user message。
- `thread_id=session_id` 交给 LangGraph `checkpointer` 维护短期上下文。
- 本地不再维护 session 事件事实源，不再回放 `context_window`，也不再发 `RemoveMessage(REMOVE_ALL_MESSAGES)`。

## 现在的主链

1. `POST /runs` 创建 `run_id`，写 `resources.runs.create_run(...)`。
2. 后台线程调用 `HarnessRuntime.execute_run(...)`。
3. `brain.stream(..., stream_mode=["messages","custom","values"], version="v2")` 产出 raw chunk。
4. `harness.py` 把 chunk 规范化成 `status/thinking/text_delta/tool_status/values`。
5. `run_ledger.py` 记录快照、规范化事件和完整 raw。

## 模块分工

- `run_ledger.py`：`dsagents_runs.db`
- `harness.py`：stream 规范化
- `hands.py`：最小 `ToolStatusMiddleware`
- `resources.py`：run ledger + checkpointer + store + backend
- `api.py`：薄 HTTP 适配
- `tools.py`：`parse_document`

## 数据与边界

- `backend/data/` 是固定数据根；`dsagents_runs.db`、`dsagents_checkpoints.db`、`dsagents_store.db` 都由运行时按需创建。
- 长期文档只记录配置键和边界，不抄录本地 `.env` 的真实值。

## 已删除

旧 session 模块/表/端点已移除（见 [INTERFACES.md](../INTERFACES.md) §1）；commit `8890292`。

## 并发与恢复

- 同一 `session_id` 同时只允许一个 run，靠进程内 `threading.Lock`
- 进程启动时，遗留 `queued` / `running` run 会补记为 `failed("执行已中断，请重试")`
