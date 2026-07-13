# backend 架构与约定

`backend/` 是 DsAgents 的唯一产品子项目，安装根是 `backend/`，唯一产品包是 `dsagents/`。实现细节以 `backend/.planning/codebase/` 事实文档为准。

## 核心模型

- **run-first**：run 是唯一执行与查询单位；`run_events` 是 append-only 事件源，`runs` 是投影快照。
- **短期上下文**：当前请求的 `messages[]` 发送给 Brain；LangGraph 以 `thread_id=session_id` 维护上下文，应用不再维护 session 事件回放。
- **能力边界**：`Brain` / `BrainFactory` 是可注入 `Protocol`；工具是 callable + `ToolCatalog`；资源和 ledger 是具体类。
- **业务 Skill**：Philips 外高桥和 Tecan 业务分别位于 `backend/dsagents/skills/<skill>/`，每个 Skill 通过两个业务工具完成抽取保存与一站式生成。
- **显式 artifacts**：上传、MinerU 结果、抽取 JSON、canonical JSON 和 Excel 都通过 `/artifacts/...` 路径传递；生成文件唯一命名，不覆盖输入或已有产物。

## 主执行链

1. `POST /upload` 保存文件并返回 `/artifacts/uploads/...`。
2. `POST /runs` 校验 `messages[]`，写入 queued run，启动后台线程。
3. `HarnessRuntime.execute_run` 将 `artifact` block 归一化为文本路径提示，创建 Brain 并以 `stream_mode=["messages", "custom", "updates"]` 消费 v2 stream。
4. `messages` 产生 `model_usage`、主 Agent 的 `thinking` / `text_delta`；`custom` 产生工具执行和 MinerU 进度；`updates` 产生 `assistant_message` 与工具调用事件。
5. 结束时写入 `succeeded`、`failed` 或 `cancelled` 状态；`GET /runs/{run_id}` 返回快照、增量事件、最新内容事件和全量用量汇总。

## 当前模块职责

- `backend/dsagents/api.py`：FastAPI 工厂、四个 HTTP 端点、上传、同 `session_id` 单飞锁和启动恢复。
- `backend/dsagents/runtime/agent.py`：Brain 工厂、DeepAgents 装配、四个声明式 extractor SubAgent、`ToolTelemetry` 与 `NoProgressMiddleware`。
- `backend/dsagents/runtime/execution.py`：stream chunk 到 `RunEvent` 的规范化、协作式 cancel 和默认 harness 工厂。
- `backend/dsagents/runtime/runs.py`：SQLite run ledger、事件追加、快照投影、用量聚合和大 payload 外溢。
- `backend/dsagents/runtime/resources.py`：`AgentResources`、SQLite store/checkpointer 和 `/memories/`、`/artifacts/`、`/large_tool_results/`、`/skills/` 路由。
- `backend/dsagents/runtime/tools.py`：静态注册 6 个工具；不自动扫描 Skill，不提供插件平台。
- `backend/dsagents/integrations/`：artifact 路径/唯一命名/JSON helper 与 MinerU HTTP/ZIP 集成。
- `backend/dsagents/skills/`：业务字段合同、规则、模板、抽取保存和 Excel 生成实现。

## 数据与边界

- `backend/data/` 按需创建 `dsagents_runs.db`、`dsagents_checkpoints.db`、`dsagents_store.db`；新 schema 无迁移，部署切换需清空整个数据目录。
- 同一 `session_id` 的单飞锁只在进程内有效；多 worker 不提供跨进程互斥。
- `POST /runs/{run_id}/cancel` 是协作式 drain，不回滚已经生成的文件，也不提供跨进程强杀。
- 业务问题统一返回 `input_problems`；当前 run 结束，下一 run 重新显式传入所需 artifact 路径。
- Philips 法定单位查询可选使用 Oracle thick mode；缺少 `ORACLE_CLIENT_LIB_DIR` 或查询失败时生成流程降级为人工校验。

完整接口、provider 和存储边界见根级 `INTERFACES.md`；测试入口见 `backend/.planning/codebase/TESTING.md`。
