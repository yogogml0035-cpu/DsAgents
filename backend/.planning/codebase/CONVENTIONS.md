# CONVENTIONS

## 1. 命名

- 模块：`snake_case`，当前顶层模块为 `api/hands/harness/resources/run_ledger/tools/self_check`
- 类：`PascalCase`
- 函数/方法：`snake_case`
- 常量：`UPPER_SNAKE_CASE`

## 2. 类型

- 协议接口使用 `typing.Protocol`
  - `Brain`
  - `BrainFactory`
  - `Hands`
  - `RunLedger`
- 简单值对象使用 `@dataclass(frozen=True)`
  - `RunEvent`
  - `RunSnapshot`
  - `ResourceConfig`
  - `ToolCatalog`

## 3. 运行时约定

- Brain 调用统一走 `stream(..., version="v2")`
- payload 统一只传当前 user message
- 短期上下文统一依赖 `thread_id=session_id`
- run ledger 事件类型固定为：
  - `status`
  - `thinking`
  - `text_delta`
  - `tool_status`
  - `values`

## 4. 持久化约定

- `runs` 行是当前 run 快照
- `run_events` 是 append-only 事件流
- 大 payload/raw 外溢到 `artifacts/run-events/*.json`
- 不做清理策略
- 不做历史迁移

## 5. HTTP 约定

- 当前只保留：
  - `POST /runs`
  - `GET /runs/{run_id}`
  - `POST /files`
- 同一 `session_id` 的运行冲突返回 `409`
- 未知 run 返回 `404 {"error":"Unknown run: ..."}`

## 6. 已明确删除的旧语义

- `session.py`
- `context_window`
- `RemoveMessage(REMOVE_ALL_MESSAGES)`
- `run_turn`
- `stream_turn`
- `TraceHands`
- `/sessions/messages*`
- `GET /sessions/{session_id}/runs`
- `from session import run_session`
