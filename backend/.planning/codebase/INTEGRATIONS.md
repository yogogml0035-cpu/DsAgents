# INTEGRATIONS — backend 外部集成事实

> Analysis Date: 2026-07-22。本文只记录当前接口边界和降级行为，不记录密钥或连接串值。

## LLM、LangGraph 与 DeepAgents

- `runtime.agent.DeepAgentsBrainFactory` 用 `init_chat_model(...)` 创建可注入的 `BaseChatModel`，再交给 `create_deep_agent(...)`。
- `AgentResources` 向 Agent 注入 backend、LangGraph SQLite checkpointer 和 store；每次 run 使用 `thread_id=session_id`。
- Philips workflow 加 `ToolStrategy(PhilipsWgqRecognitionResult)`；`StructuredOutputCompatibility.wrap_model_call` 仅在该 ToolStrategy 请求上关闭 incompatible thinking。
- Tecan 没有 ToolStrategy 或 SubAgent 图：Skill 要求 Agent 调用 `finalize_tecan_overseas_recognition`，执行层只从该 ToolMessage 捕获经校验的最终 JSON。
- DeepAgents 默认 general-purpose subagent 已通过 harness profile 禁用；不存在生产 Tecan A/B/C 子代理。

## MinerU 材料解析

| 工具 | 输入 | 输出/用途 |
|---|---|---|
| `parse_documents` | 显式 PDF artifact | MinerU 解析结果 artifact；供渠道 Skill 识别材料角色 |
| `extract_archives` | 显式 archive artifact | 解压后的 artifact；通用能力，渠道 PRD 本身不解析 ZIP 内容 |
| `inspect_supply_chain_workbooks` | 显式 `.xlsx` artifact 列表 | 每工作簿一个只读 JSON artifact，含 sheet/rows；不生成 Excel |

Philips 与 Tecan Skill 都要求只处理本轮显式 artifact，动态识别发票、运单、装箱单、订单/合同和主数据。ZIP、DOCX、图片不进入渠道内容抽取，材料足够时以 `problems` 说明后继续。

## Philips 主数据与 Oracle

- `lookup_philips_wgq_master_data(product_ids, tracking_artifact?)` 先读唯一确认的 Tracking XLSX，再以 Oracle 只补稳定缺失字段。
- 本票事实优先，lookup 不返回/覆盖数量、价格、金额、重量、单号或运单。
- Oracle 环境不完整、thick client 初始化失败、查询失败或未命中都转换为 `problems`/null，不阻塞已可确认的业务结果。
- thick mode 前提为 `ORACLE_CLIENT_LIB_DIR`；风险与部署检查见 `CONCERNS.md`。

## 渠道最终合同

| 渠道 | 触发 | 最终 schema | 投影 |
|---|---|---|---|
| Philips WGQ | `workflow=philips_wgq_inbound_recognition` | `PhilipsWgqRecognitionResult` | ToolStrategy `structured_response` → `run.result` |
| Tecan 境外 | 用户明确请求 Tecan Skill | `TecanOverseasRecognitionResult` | finalizer ToolMessage → `run.result` |

两个 schema 共用 `OrderItem` 24 字段，不含 `shipment`。`success`、`partial_success`、`input_problems` 都是业务 outcome；后者仍是 `succeeded` run，只要最终 schema 合法。正常 JSON 不携带候选噪声、审计轨迹、Excel 路径或 OMS 保存结果。

## SQLite、artifacts 与 OMS

- 三 SQLite：`dsagents_runs.db`（run/event 投影）、`dsagents_checkpoints.db`（LangGraph）、`dsagents_store.db`（memory/store），连接不共享。
- `/artifacts/...` 是 HTTP/Agent/工具的稳定虚拟路径；`integrations.artifacts` 解析路径并写 JSON artifact。运行时生成 artifact 不覆盖上传源文件。
- `runtime.oms_log.append_run_created_log` 仅在 HTTP `create_run` 成功后 best-effort 追加 `backend/log/oms_log.log`。它不是 run event、没有查询 API，不保存 prompt/thinking/tool raw/result，写失败不影响 200 queued。
- ledger 与 OMS 使用 UTC+8 本地 `YYYY-MM-DD HH:MM:SS`。

## HTTP 接口

| 端点 | 输入 | 返回 |
|---|---|---|
| `POST /upload` | multipart files | `/artifacts/uploads/...` 文件元数据 |
| `POST /runs` | `workflow?`、`session_id?`、`messages[]`（text/artifact） | `{run_id, session_id, status:"queued"}` |
| `GET /runs/{run_id}` | 可选 `after_event_id` | run、workflow、result、events、latest content、usage |
| `POST /runs/{run_id}/cancel` | 无 body | 协作 cancel 状态 |

`workflow` 目前仅允许 Philips 字面量或省略。Philips workflow 必须使用服务端新 session；普通/Tecan run 保留通用 session 语义。不存在 SSE、session 管理、OMS 自动保存或下载端点。

## 可观测性与降级

- 固定七类事件由 `HarnessRuntime` 从 `messages`、`custom`、`updates` stream 投影；业务 JSON 不依赖 `reply`。
- 有效 final JSON 的 `input_problems` 不当作异常；结构化 Philips 响应缺失、工具异常或模型异常才使 run `failed`。
- 进程内 cancel 只能请求 LangGraph drain，无法强杀模型或外部 HTTP；多 worker 无跨进程互斥。
