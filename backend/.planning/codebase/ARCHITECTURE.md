# ARCHITECTURE — backend（dsagents）

> Analysis Date: 2026-07-22。事实来源是 `backend/api.py`、`runtime/`、`integrations/` 和 `skills/`，不是历史构建产物。

## Pattern Overview

DsAgents 是一个单子项目 Agent runtime。它以 **run-first** 模型接收消息与 artifact，驱动可注入的 Brain，把 LangGraph stream 归一化为七类 run events，并把最终业务 JSON 投影到 `runs.result_json`。

- HTTP 只提供 `POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`；客户端轮询，不提供 SSE 或 session API。
- `run_events` 是 append-only 事实流，`runs` 是投影快照；业务方以 `run_id` 查询，不以 `session_id` 作为执行单位。
- `session_id` 只提供 LangGraph `thread_id` 和进程内单飞锁。checkpointer 保存图上下文，store 承载 Agent memory；两者不替代 run ledger。
- 唯一 API workflow 是 `philips_wgq_inbound_recognition`。Tecan 用通用 run + Skill 识别，未新增 Tecan workflow、状态表或 HTTP 端点。

## 分层与依赖方向

| 层 | 位置 | 职责 |
|---|---|---|
| HTTP | `api.py` | 请求校验、上传、创建 run、后台线程、轮询和 cancel |
| 运行时 | `runtime/` | Brain 装配、stream 投影、middleware、run ledger、资源和工具目录 |
| 集成 | `integrations/` | artifact、MinerU、Oracle、OMS JSONL |
| 业务 Skill | `skills/` | Philips/Tecan 流程提示、最终 JSON schema、主数据/XLSX 工具 |

依赖单向为 `api → runtime → integrations/skills`；Skill 不调用 HTTP 层。`Brain`、`BrainFactory` 是仅有的 `Protocol` 注入点；工具是普通 callable，经 `ToolCatalog` 静态注册。

## 执行数据流

```text
POST /upload
  → backend/data/artifacts/uploads/ → /artifacts/uploads/...

POST /runs
  → SqliteRunLedger.create_run(status=queued)
  → best-effort oms_log.run_created
  → 后台 HarnessRuntime.execute_run(..., workflow)
      → DeepAgentsBrainFactory.create(...)
      → brain.stream(thread_id=session_id)
      → 7 类规范化事件 + runs 投影
      → succeeded / failed / cancelled

GET /runs/{run_id}
  → run 快照、result、增量 events、latest_content_event、usage
```

`execute_run` 会把 Philips ToolStrategy 的 `structured_response` 再次 Pydantic 校验；对 Tecan，只接受名为 `finalize_tecan_overseas_recognition` 的 ToolMessage 内容并校验为 `TecanOverseasRecognitionResult`。普通阅读类 run 不会被强制为业务 JSON。

## 渠道供应链 JSON 合同

共享合同位于 `skills/channel_contract.py`：

- `OrderItem` 固定 24 字段，Philips 与 Tecan 共用；每个已返回行全字段存在，未知为 `null`。
- 数量、金额、重量规范化为无千分位、非科学计数法十进制字符串；日期 JSON 为 `YYYY-MM-DD`；编号保留原始前导零。
- `currency` 大写三位，`trade_terms` 大写，`new_or_used` 为“新/旧”，`pre_or_post_sales` 为“售前/售后”。空白单据字段规范化为 `null`。
- `RecognitionProblem` 是 `{source, location, issue, action}`；`input_problems` 至少一条。schema 会把含未解决 null 的 `success` 降为 `partial_success`，补入未在 `problems` 明确列出的缺失路径，也会把字段已完整的 `partial_success` 归正为 `success`；普通 partial 必须保有已确认核心事实和至少一项缺失。
- Philips `OrderHeader` 与 Tecan `TecanHeader` 各自保留不同字段集；不再有 `shipment`。

业务提示词而非额外状态机负责材料角色、同票归集、发票顺序、同 12NC 不合并、主数据仅补缺、候选不进入正式字段和冲突转 `input_problems`。这类判断依赖同一 run 内已读材料，没有跨 run 任务状态的消费者。

## Agent、状态与 middleware

`DeepAgentsBrainFactory` 注册 harness profile 关闭默认 general-purpose subagent，并始终传入 `subagents=[]`。当前不设 Tecan A/B/C extractor：单一 Agent 能在同一上下文中归集一票材料，避免子任务消息、候选合并和业务状态出现双源。

`runtime_middlewares()` 每次创建新实例：

1. `StructuredOutputRecovery`（仅传入 Philips schema 时装配）
2. `ToolTelemetry`
3. `NoProgressMiddleware`
4. `StructuredOutputCompatibility`
5. `MemoryMiddleware`（仅主 Agent 且有 `memory_backend`）

选择 class-based node hook 的原因是 `StructuredOutputRecovery.after_model` 要检查/更新完整消息状态，并用 `jump_to: "model"` 或 `"end"` 控制图。它的 `can_jump_to` 包含 `end`；空壳耗尽时保留既有 all-null `partial_success` 技术 fallback，其他未恢复结构化结果使 Philips run 失败。Tecan 终态校验不需要跨 hook state，因此在 finalizer 工具中完成，不新增全局 middleware。

## workflow 与工具表

`default_tool_catalog()` 的五工具为：

| 类型 | 工具 |
|---|---|
| 通用材料 | `parse_documents`、`extract_archives` |
| Philips 主数据 | `lookup_philips_wgq_master_data` |
| XLSX 输入 | `inspect_supply_chain_workbooks` |
| Tecan 终态 | `finalize_tecan_overseas_recognition` |

Philips workflow 用 denylist 排除 `finalize_tecan_overseas_recognition`，保留共享 MinerU、Philips lookup 和共享 XLSX 检查器。不得把 workflow 缩成仅业务工具的 allowlist；`/memories/AGENTS.md` 的通用材料指引必须仍可执行。

## Skill 成对目录

| 资源目录（挂载 `/skills/`） | Python 包 | 用途 |
|---|---|---|
| `skills/philips-wgq-inbound-recognition/` | `skills/philipswgqinboundrecognition/` | 固定 workflow、schema、Tracking/Oracle 主数据 |
| `skills/tecan-import/` | `skills/tecanimport/` | 通用路径业务提示、references、XLSX inspection、最终 JSON 校验 |

Tecan 不再携带 Excel 模板或生成器。`openpyxl` 只读取用户上传的 `.xlsx` 并写出中间 JSON artifact 供 Agent 识别材料角色。

## Outcome 与错误边界

| 条件 | run 状态 | `run.result` |
|---|---|---|
| 合法 Philips/Tecan final JSON，含 `success` / `partial_success` / `input_problems` | `succeeded` | 完整业务 JSON |
| Philips 缺失或非法 structured response | `failed` | `null` |
| 未调用 Tecan finalizer 的通用阅读 run | `succeeded` | `null` |
| 模型、工具或运行时异常 | `failed` | `null` |
| 协作 cancel | `cancelled` | 保留此前投影，不伪造结果 |

`input_problems` 是业务可复核结果，不等于执行失败：`data` 仍必须包含完整 header 和已证实 items，无法安全形成行时可以是空数组。OMS 只消费 `run.result`，不解析 `reply` 或工具候选文本。

## 事件、持久化与运维

- 事件固定为 `status`、`tool_execution`、`tool_progress`、`thinking`、`text_delta`、`assistant_message`、`model_usage`。
- 三个 SQLite 文件分别用于 runs、checkpoints、store；无自动 schema migration。
- 同 `session_id` 单飞和 `run_controls` 均为进程内，不承诺多 worker 互斥或强杀。
- `runtime/oms_log.py` 仅在 HTTP run 创建成功后 best-effort 写 `backend/log/oms_log.log`，不是第八类 event，也无查询 API。
- ledger 与 OMS 时间戳统一 UTC+8 本地 `YYYY-MM-DD HH:MM:SS`。

## 验证入口

参见 `TESTING.md` 的七个本地 assert 脚本。修改 backend 后先更新本目录，再更新根级 `ARCHITECTURE.md`、`INTERFACES.md`、`coding_maps/SYSTEM_MAP.md` 并执行 `git diff --check`。
