# DsAgents 系统地图

> 刷新日期：2026-07-22。用于快速定位调用链；细节以 `backend/.planning/codebase/` 为准。

## 一页总览

| 区域 | 入口 | 当前职责 |
|---|---|---|
| HTTP | `backend/api.py` | 四端点、run/session 校验、后台执行、轮询、cancel、OMS best-effort 索引 |
| 执行 | `backend/runtime/execution.py` | `HarnessRuntime.execute_run`、stream→七类 events、结果投影 |
| Agent | `backend/runtime/agent.py` | `DeepAgentsBrainFactory`、Philips ToolStrategy、denylist、关闭默认子代理 |
| Middleware | `backend/runtime/middleware.py` | Philips recovery、telemetry、loop 检测、thinking compatibility、memory |
| 业务合同 | `backend/skills/channel_contract.py` | 共享 24 字段 `OrderItem`、problems、outcome |
| Philips | `skills/philips-*` | 固定 workflow、Skill、schema、Tracking/Oracle lookup |
| Tecan | `skills/tecan-*` | Skill/references、XLSX inspection、最终 JSON finalizer |
| 持久化 | `runtime/runs.py` / `resources.py` | run ledger、checkpointer、store 三 SQLite |

## 从 HTTP 到最终结果

```text
POST /upload
  → /artifacts/uploads/<name>

POST /runs {workflow?, session_id?, messages[]}
  → api.py 校验并创建 run（queued）
  → OMS JSONL best-effort
  → background HarnessRuntime.execute_run(...)
      → DeepAgentsBrainFactory.create(...)
      → brain.stream(thread_id=session_id)
      → messages/custom/updates 归一化为 7 类 run_events
      → Philips ToolStrategy 或 Tecan finalizer 产出 run.result
      → status=succeeded | failed | cancelled

GET /runs/{run_id}
  → {run, workflow, result, events, latest_content_event, usage}
```

`run.result` 是 OMS 消费的唯一业务通道。`reply` 是自然语言摘要，不是业务数据源。

## Agent 装配与状态图

```text
AgentResources
  ├─ backend (artifacts + /skills + /memories)
  ├─ checkpointer (thread_id=session_id)
  ├─ store
  └─ runs (run/event ledger)

DeepAgentsBrainFactory
  ├─ general-purpose subagent disabled
  ├─ subagents=[]
  ├─ static ToolCatalog (5)
  ├─ Philips: ToolStrategy + PHILIPS_WORKFLOW_PROMPT + denylist
  └─ generic/Tecan: Skill-driven, no forced response format
```

没有额外业务消息状态、任务状态表或 Tecan SubAgent。单票归集在一个 Agent run 中完成；外部终态由 run ledger、执行上下文由 checkpointer 保存。

## Middleware 顺序

| 顺序 | middleware | 适用范围 |
|---|---|---|
| 0 | `StructuredOutputRecovery` | 仅 Philips schema；`after_model` 回收/纠正结构化输出 |
| 1 | `ToolTelemetry` | 所有 Agent |
| 2 | `NoProgressMiddleware` | 所有 Agent |
| 3 | `StructuredOutputCompatibility` | 所有 Agent；仅 ToolStrategy 时实际变换模型请求 |
| 4 | `MemoryMiddleware` | 有 memory backend 的主 Agent |

`StructuredOutputRecovery` 选择 node-style `after_model`：它需要读取同回合 `ToolMessage.tool_call_id`、写回 `structured_response`，并使用 `jump_to` 控制有限重试。Tecan 的业务 outcome 不需要跨 hook 状态，留在 finalizer 工具。

## 工具地图

| 工具 | 归属 | 作用 |
|---|---|---|
| `parse_documents` | MinerU | 解析 PDF 材料 |
| `extract_archives` | MinerU | 通用 archive 解压 |
| `lookup_philips_wgq_master_data` | Philips | Tracking/Oracle 唯一主数据补齐 |
| `inspect_supply_chain_workbooks` | Tecan 包（共享使用） | 只读 XLSX→JSON artifact |
| `finalize_tecan_overseas_recognition` | Tecan | Pydantic 校验/返回 Tecan 最终 JSON |

Philips workflow 从五工具中 denylist 移除 Tecan finalizer；其余共享材料工具保持可用。Tecan 不输出 Excel，`openpyxl` 只读用户材料。

## 渠道路径

```text
Philips workflow
  → Skill 读取本轮 PDF/XLSX
  → parse_documents / inspect_supply_chain_workbooks
  → 唯一 Tracking 时 lookup_philips_wgq_master_data
  → PhilipsWgqRecognitionResult
  → run.result

Tecan explicit Skill request
  → Skill 读取本轮 PDF/XLSX
  → parse_documents / inspect_supply_chain_workbooks
  → 同票归集与字段裁决
  → finalize_tecan_overseas_recognition
  → run.result
```

两者的 header 不同，`items[]` 使用同一 24 字段。`input_problems` 仍返回完整 header、已证实 items/可空数组和复核 problems；不输出 `shipment`、候选或 Excel。

## 关键边界与验证

- HTTP 只有 Philips workflow 字面量；Tecan 不增加 workflow 或 API。
- `run_events` 固定 7 类，OMS JSONL 不是第八类事件。
- `session_id` 是图 thread/单飞锁，非业务状态 API。
- Oracle 是 Philips 可选补齐；失败优雅降级。
- 本地验证：`cd backend; python -m tests.test_tools`、`test_run_ledger`、`test_harness`、`test_api`、`test_workflow_setup`、`test_philips_wgq_inbound_recognition`、`test_tecan_import`；根目录另跑 `git diff --check`。
