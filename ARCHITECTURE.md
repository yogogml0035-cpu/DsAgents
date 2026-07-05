# ARCHITECTURE

## 1. 系统定位

`DsAgents` 当前只有一个产品子项目：`backend/`。它是一个 **run-first agent runtime**：

- 对话短期上下文不再由仓库自建 session 事件库回放。
- LangGraph `checkpointer` + `thread_id=session_id` 是唯一的 thread-scoped 短期上下文来源。
- 本地 SQLite 只保留一个窄用途 run ledger：记录 `POST /runs` 的输入、状态、规范化事件和完整 raw chunk。

当前对外只有三类稳定能力：

- `POST /runs` / `GET /runs/{run_id}`：提交 run、轮询 run。
- `POST /files`：上传文件到 `/artifacts/uploads/...`。
- 程序内组合：`AgentResources` + `create_harness(resources).execute_run(...)`。

## 2. 模块边界

| 模块 | 文件 | 职责 |
|------|------|------|
| Run Ledger | `backend/run_ledger.py` | `dsagents_runs.db` 的 run 快照与 `run_events` 追加写入；大 payload/raw 外溢到 `backend/data/artifacts/run-events/` |
| Harness | `backend/harness.py` | 组装 Brain、Tools、Middleware；执行 `brain.stream(...)` 并把 `messages/custom/values` 规范化为 run 事件 |
| Hands | `backend/hands.py` | 最小 `ToolStatusMiddleware`；通过 `get_stream_writer()` 发 `tool_status started/completed/error` |
| Resources | `backend/resources.py` | 装配 `SqliteRunLedger`、LangGraph `SqliteSaver` checkpointer、`SqliteStore`、`CompositeBackend` |
| Tools | `backend/tools.py` | 当前唯一业务工具 `parse_document` |
| HTTP | `backend/api.py` | 薄 FastAPI 适配层；进程内 per-session 单飞锁；后台线程执行 run |

`backend/` 仍是扁平顶层模块，不是 Python 包；没有 `__init__.py` / `__main__.py`，模块内继续使用 `from harness import ...` 这类绝对导入。

## 3. 主调用链

```
POST /runs
  ├─ 生成 run_id；session_id 为空则生成 uuid
  ├─ 进程内按 session_id 获取 threading.Lock
  ├─ resources.runs.create_run(run_id, session_id, input_message)
  ├─ 后台线程调用 HarnessRuntime.execute_run(message, session_id, run_id)
  └─ 立即返回 {"run_id","session_id","status":"queued"}

HarnessRuntime.execute_run(...)
  ├─ emit_run_status("running")
  ├─ brain.stream(
  │    {"messages": [{"role":"user","content": message}]},
  │    config={"configurable":{"thread_id": session_id}},
  │    stream_mode=["messages","custom","values"],
  │    version="v2",
  │  )
  ├─ messages chunk → `thinking` / `text_delta`
  ├─ custom chunk   → `tool_status`
  ├─ values chunk   → `values`
  ├─ 成功 → emit_run_status("succeeded", reply=...)
  └─ 失败 → emit_run_status("failed", error=...)
```

这里有两个故意保留的简单化：

- 同一 `session_id` 的并发保护只靠进程内 `threading.Lock`，不做跨进程锁。
- run ledger 永久保留，不做清理策略、迁移脚本或历史兼容层。

## 4. 持久化边界

`backend/data/` 下有三条独立 SQLite 通道：

- `dsagents_runs.db`：run ledger。表只有 `runs` 与 `run_events`。
- `dsagents_store.db`：LangGraph store。
- `dsagents_checkpoints.db`：LangGraph checkpointer。

当前 `runs` 基表直接存：

- `run_id`
- `session_id`
- `input_message`
- `status`
- `created_at`
- `updated_at`
- `reply`
- `error`

`run_events` 只保留五类规范化事件：

- `status`
- `thinking`
- `text_delta`
- `tool_status`
- `values`

每条 run event 同时保存完整 raw。小 JSON 直接入库；大 JSON 外溢到 `backend/data/artifacts/run-events/*.json`，读取时透明回填。

## 5. 关键约束

- 旧 `session.py` / `dsagents_sessions.db` / `context_window` / `RemoveMessage(REMOVE_ALL_MESSAGES)` 已删除。
- 旧 `/sessions/messages*` 和 `GET /sessions/{session_id}/runs` 已删除。
- `.env` 由 `backend/harness.py` 与 `backend/tools.py` 在导入时加载，避免删除 `session.py` 后 MiniMax / MinerU 配置丢失。
- 启动时若发现遗留 `queued` / `running` run，会统一补记为 `failed("执行已中断，请重试")`。

## 6. 当前风险

- run 锁是单进程内语义；多 worker 部署需确认是否接受。
- run ledger 保存完整 raw chunk，便于调试，但也意味着错误与模型原始输出会长期留存。
- 程序内没有单函数 one-shot API；仓库默认入口是 HTTP `POST /runs` 或直接使用 harness 组合。
