# CONCERNS

## 1. 单进程锁

- 当前并发保护只覆盖单个 FastAPI 进程
- 多 worker / 多进程部署时，同一 `session_id` 可能并发运行

## 2. raw chunk 长期留存

- run ledger 会长期保存完整 raw chunk
- 这对调试有利，但会增加本地存储占用，也可能保留较多模型/错误细节

## 3. 无清理策略

- `dsagents_runs.db` 与 `artifacts/run-events/` 只增不删
- 当前版本明确不做 TTL、归档或压缩

## 4. 无鉴权

- HTTP 层仍是最小演示形态
- 当前没有鉴权、用户隔离或 CORS 策略层

## 5. 程序内 one-shot 入口已删除

- 旧 `from session import run_session` 已不存在
- 如需库式调用，只能显式组合 `AgentResources`、`create_harness` 与 `resources.runs`
