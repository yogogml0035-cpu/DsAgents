# TESTING

## 1. 当前测试形态

当前没有独立 pytest 套件；主要验证入口是：

```powershell
python backend/self_check.py
```

## 2. self_check 覆盖点

`self_check.py` 当前直接覆盖这些事实：

- `DeepAgentsBrainFactory` 仍能从 `MINIMAX_*` 环境变量构造模型
- `parse_document` 在缺失 `MINERU_*` 时 fail-fast
- `SqliteRunLedger` 的快照、事件、启动恢复与大 payload 外溢
- `ToolStatusMiddleware` 只发 `started/completed/error`
- `HarnessRuntime.execute_run` 不再发送 `RemoveMessage`，只发送当前 user message
- `POST /runs` 立即返回 `queued`
- `GET /runs/{run_id}` 可轮询到 `running/succeeded/failed`
- `after_event_id` 只返回增量事件
- 同一 `session_id` 并发提交返回 `409`
- 失败 run 不回滚 thread；下一次同 `session_id` 继续同 thread
- 启动时遗留 `queued/running` run 会被标记为 `failed`
- `/files` 上传与 `/artifacts/...` 虚拟路径解析

## 3. 测试替身

`_FakeBrainFactory` / `_FakeBrain` 用来证明：

- Brain 可替换
- Harness 只依赖 `thread_id=session_id`
- run-first 模型下，payload 第一条就是当前 user message

`_FakeBrain` 还会按 `thread_id` 保存一份最小历史，用来验证失败 run 后同 thread 的后续 run 不会被 runtime 主动回滚。

## 4. 当前缺口

- 没有独立单元测试目录
- 没有 CI
- 没有 lint / type check gate
- 没有针对真实 provider 的长期集成测试
