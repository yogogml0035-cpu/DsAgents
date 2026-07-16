# backend 架构与约定

`backend/` 是 DsAgents 的唯一产品子项目，安装根是 `backend/`，源码顶层布局是 `api.py` 与 `runtime/`、`integrations/`、`skills/`；发行名仍为 `dsagents`。实现细节以 `backend/.planning/codebase/` 事实文档为准。

## 核心模型

- **run-first**：run 是唯一执行与查询单位；`run_events` 是 append-only 事件源，`runs` 是投影快照。
- **短期上下文**：当前请求的 `messages[]` 发送给 Brain；LangGraph 以 `thread_id=session_id` 维护上下文，应用不再维护 session 事件回放。
- **能力边界**：`Brain` / `BrainFactory` 是可注入 `Protocol`；工具是 callable + `ToolCatalog`；资源和 ledger 是具体类。
- **业务 Skill**：Philips 外高桥使用固定 workflow、Pydantic 结果合同和单一 Tracking/Oracle 主数据工具；Tecan 保留两个业务工具与 Excel 生成。
- **显式 artifacts**：上传与 MinerU 结果通过 `/artifacts/...` 传递；Tecan 的抽取/canonical/Excel 产物沿用显式路径。Philips 直接把验证后的 JSON 投影到 `run.result`，不生成 Excel。

## 主执行链

1. `POST /upload` 保存文件并返回 `/artifacts/uploads/...`。
2. `POST /runs` 校验可选 `workflow` 与 `messages[]`，写入 queued run，启动后台线程；Philips workflow 强制服务端生成新 session。
3. `HarnessRuntime.execute_run` 将 `artifact` block 归一化为文本路径提示，把 workflow 传给 Brain 并以 `stream_mode=["messages", "custom", "updates"]` 消费 v2 stream。
4. `messages` 产生 `model_usage`、主 Agent 的 `thinking` / `text_delta`；`custom` 产生工具执行和 MinerU 进度；`updates` 产生 `assistant_message` 与工具调用事件。
5. Philips 从 `updates` 捕获 `structured_response` 并再次 Pydantic 校验；结束时把验证结果写入终态 status 与 `runs.result_json`。GET 返回快照、顶层 `workflow`/`result`、增量事件、最新内容事件和用量汇总。

## 当前模块职责

- `backend/api.py`：FastAPI 工厂、四个 HTTP 端点、上传、同 `session_id` 单飞锁和启动恢复。
- `backend/runtime/agent.py`：Brain 工厂、Philips ToolStrategy/工具裁剪、两个 Tecan extractor SubAgent 与 middleware 装配。
- `backend/runtime/middleware.py`：`StructuredOutputRecovery`、`ToolTelemetry`、`NoProgressMiddleware`、`StructuredOutputCompatibility`、主 Agent 受限 `MemoryMiddleware` 及 `runtime_middlewares(*, memory_backend=None)`。
- `backend/runtime/execution.py`：stream chunk 到 `RunEvent` 的规范化、结构化响应捕获/复验、协作式 cancel 和默认 harness 工厂。
- `backend/runtime/runs.py`：SQLite run ledger、workflow/result 投影、事件追加、用量聚合和大 payload 外溢。
- `backend/runtime/resources.py`：`AgentResources`、SQLite store/checkpointer 和 `/memories/`、`/artifacts/`、`/large_tool_results/`、`/skills/` 路由；缺失时 seed 共享操作手册 `/memories/AGENTS.md`。
- `backend/runtime/tools.py`：静态注册 5 个工具；不自动扫描 Skill，不提供插件平台。
- `backend/integrations/`：artifact 路径/唯一命名/JSON helper 与 MinerU HTTP/ZIP 集成。
- `backend/skills/`：Philips 响应 schema/主数据规则与 Tecan 字段合同/模板/Excel 实现。

## 数据与边界

- `backend/data/` 按需创建 `dsagents_runs.db`、`dsagents_checkpoints.db`、`dsagents_store.db`；新 schema 无迁移，部署切换需清空整个数据目录。
- 同一 `session_id` 的单飞锁只在进程内有效；多 worker 不提供跨进程互斥。
- `POST /runs/{run_id}/cancel` 是协作式 drain，不回滚已经生成的文件，也不提供跨进程强杀。
- Philips 的业务问题使用 `result.outcome=input_problems`（`data=null`，run 仍 `succeeded`）；结构化响应缺失/非法或运行时异常才令 run `failed`。Tecan 工具继续返回 `code=input_problems`。下一 run 均重新显式传入所需 artifact 路径。
- Philips 主数据优先 Tracking、Oracle 只补缺失稳定字段；配置缺失、查询失败或未命中写 `problems`，已有 PDF/Tracking 数据不丢失。

完整接口、provider 和存储边界见根级 `INTERFACES.md` §5；系统级风险见 `ARCHITECTURE.md` §7 与 `backend/.planning/codebase/CONCERNS.md`；测试入口见 `backend/.planning/codebase/TESTING.md` 与 `docs/commands.md`。
