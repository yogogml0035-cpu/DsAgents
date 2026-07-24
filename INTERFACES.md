# DsAgents 接口与边界

> 本轮刷新：2026-07-22。具体 backend 实现事实见 `backend/.planning/codebase/INTEGRATIONS.md` 与 `ARCHITECTURE.md`；调用链见 [coding_maps/SYSTEM_MAP.md](coding_maps/SYSTEM_MAP.md)。

## HTTP 合同

| 端点 | 请求 | 成功返回 | 主要边界 |
|------|------|----------|----------|
| `POST /upload` | multipart `files` | `{files:[{file_path,name,mime_type,size}]}` | 只存上传文件，返回 `/artifacts/uploads/...` |
| `POST /runs` | `{workflow?,session_id?,messages[]}` | `{run_id,session_id,status:"queued"}` | 后台 daemon 线程执行；无 SSE |
| `GET /runs/{run_id}` | 可选 `after_event_id` | `{run,workflow,result,events,latest_content_event,usage}` | 轮询唯一查询面 |
| `POST /runs/{run_id}/cancel` | 无 body | cancel 状态（见下） | 协作式 `RunControl` drain，不强杀外部 HTTP/Oracle |

### 请求约束

- `messages[]` 项为 `{role, content:[{type:"text",text}|{type:"artifact",path}]}`，请求模型 `extra="forbid"`。旧 `{message:"..."}` 体不支持。
- `workflow` 只允许 `WGQ`、`DK` 或省略。
- WGQ / DK workflow **不能**携带客户端 `session_id`（服务端总是分配新 session）；违者 **422**。通用请求保留普通 session 语义。
- 同 `session_id` 并发第二跑 → HTTP **409** + `active_run_id`（进程内锁）。
- 未知 `run_id` → **404**。
- cancel：活跃 run → **202** `{"status":"cancelling"}`；已 `cancelling`/`cancelled` → **200** 回显；已终态（`succeeded`/`failed`）再 cancel → **409**。
- **无** HTTP Auth、**无** Webhook、**无** session CRUD、**无** 下载端点。

### 调用模式

```text
POST /upload → 取得 /artifacts/uploads/...
POST /runs   → 立即 {run_id, session_id, status:"queued"}
GET  /runs/{run_id}?after_event_id=...  （轮询至终态）
POST /runs/{run_id}/cancel              （可选）
```

建议使用 `after_event_id` 增量拉取。终态业务数据读顶层 `result`（与 `run.result` 相同），不要只看 `reply`。

## run、事件与结果

`GET /runs/{run_id}` 的 `run` 是 ledger 快照投影。OMS **只消费** `result`，不依赖 `reply`、Excel、候选工具结果或审计文本。

### 状态机

```text
queued → running → succeeded | failed | cancelled
queued → cancelled
running → cancelling → cancelled
```

启动 lifespan：`fail_incomplete_runs("执行已中断，请重试")` 将残留 `queued` / `running` / `cancelling` 标为 `failed`（不自动续跑）。

### 固定 7 类事件

| 类型 | 含义 |
|------|------|
| `status` | queued/running/succeeded/failed/cancelling/cancelled 投影；终态可带 `reply` / `error` / `result` |
| `tool_execution` | 工具调用观测（开始/完成/错误或 tool_calls 意图） |
| `tool_progress` | MinerU / 解压进度 |
| `thinking` | 主 Agent thinking delta |
| `text_delta` | 主 Agent 文本 delta（subagent 文本过滤） |
| `assistant_message` | 最终助手消息摘要 |
| `model_usage` | 单次模型调用 token 观测；API 层可聚合并可选 MiniMax-M3 CNY 估价 |

`run_events` append-only；`runs` 只保存投影。超过 `max_inline_bytes`（默认 256KiB）的大 payload 落盘到 `data/internal/run-events/`。`latest_content_event` 排除 `status` 与 `model_usage`。`session_id` 不是对外状态资源。

### 终态业务结果路径

| 路径 | 触发 | 投影到 `run.result` | 缺结果时 |
|------|------|---------------------|----------|
| WGQ（Philips） | `workflow=WGQ` | `ToolStrategy` → `structured_response` → `PhilipsWgqRecognitionResult` | run **`failed`** |
| DK（Tecan） | `workflow=DK` | `finalize_tecan_overseas_recognition` ToolMessage → `TecanOverseasRecognitionResult` | run **`failed`** |
| 通用 Tecan 请求 | 无 workflow + 明确 Skill 请求 | 同一 finalizer 路径 | 可 `succeeded` 且 `result=null` |
| 普通阅读 | 无 workflow、无 finalizer | 可为 `null` | 仍 `succeeded` |

合法业务 JSON（含 `input_problems`）→ run **`succeeded`**。运行时异常 / `NoProgressLoop` / Philips 缺失 structured_response → **`failed`**。用户 cancel + `GraphDrained` → **`cancelled`**。

## 渠道最终 JSON

### 共用形状

```json
{
  "outcome": "success | partial_success | input_problems",
  "data": {"header": {}, "items": []},
  "problems": [{"source": "", "location": "", "issue": "", "action": ""}]
}
```

- `data` 始终为完整对象，包含各渠道自己的固定 header 字段和 `items`；`input_problems` 可以有空 `items`，但不能把 `data` 简化为 `null` 或 `{}`。
- 每个 `items[]` 行都包含 **24** 字段：`invoice_number`、`invoice_date`、`so_item`、`product_id`、`new_or_used`、`chinese_name`、`specification`、`quantity`、`unit`、`currency`、`unit_price`、`total_price`、`trade_terms`、`origin_country`、`customs_code`、`declaration_elements`、`legal_quantity_1`、`legal_unit_1`、`legal_quantity_2`、`legal_unit_2`、`gross_weight`、`net_weight`、`business_unit`、`pre_or_post_sales`。
- 不输出 `shipment`、Excel、候选值、置信度或审计轨迹。未知值为 `null`，不使用空字符串。
- 数量、金额、重量是无千分位、非科学计数法字符串；日期为 `YYYY-MM-DD`；编号保留原样/前导零；`currency` 为大写三位 ISO，`trade_terms` 大写；`new_or_used` ∈ {新, 旧}；`pre_or_post_sales` ∈ {售前, 售后}。
- `problems[]` 每项 `{source, location, issue, action}` 均非空。

### header 差异

| Philips `OrderHeader` | Tecan `TecanHeader` |
|-----------------------|---------------------|
| `om,dn,po,so,original_waybill_number,buyer,seller,shipper,consignee,payment_terms,contract_number,salesperson,invoice_number,etd,trade_terms,port_of_departure,port_of_arrival` | `po,dn,original_waybill_number,buyer,seller,shipper,consignee,payment_terms,contract_number,invoice_number,invoice_date,trade_terms,port_of_departure,port_of_arrival` |

Philips header 含 `om`/`so`/`salesperson`/`etd` 等、无 `invoice_date`；Tecan header 含 `invoice_date`、无上述 Philips 专属字段。

### outcome 与 run 终态

| outcome / 条件 | 含义 | run 状态 |
|----------------|------|----------|
| `success` | 无未解决业务字段缺失；已解决冲突或无关材料不降级 | `succeeded` |
| `partial_success` | 核心商品事实已确认，补充字段为 `null` 且列入 problems | `succeeded` |
| `input_problems` | 票次或核心事实不能确认；只带已证实字段和复核线索 | `succeeded` |
| WGQ structured response 缺失/非法、DK finalizer 缺失、工具或运行时异常 | 无有效业务终态 | `failed` |

共享校验（`validate_channel_outcome`）会将「有缺失却声明 `success`」归正为 `partial_success`（并补 problem），将「字段已完整却声明 `partial_success`」归正为 `success`。

Philips 空 data 壳的 Recovery 耗尽会生成 all-null `partial_success` runtime fallback；它是防止图循环的**技术兜底**，不是业务 Skill 的正常裁决模板。其它解析/校验失败耗尽 → 无 `structured_response` → harness `failed`。

## 渠道材料边界

- PDF 调用 `parse_documents`；XLSX 调用 `inspect_supply_chain_workbooks`（只读 → JSON artifact）。
- 两渠道均按内容而非文件名识别材料角色；材料必须安全归为同一票，否则 `input_problems`。
- 发票行按上传顺序 / 原始行顺序；相同 12NC 默认不合并；多发票、多运单按材料出现顺序用英文逗号连接。
- 本票事实优先于主数据；仅唯一非语义标识允许补齐。冲突、舍入歧义和多候选不得写入正式字段。
- ZIP、DOCX、图片不解析内容，在 `problems` 说明；其余材料足够时继续。`parse_documents` 返回 ZIP 时先 `extract_archives` 再读文本。

## 工具与运行时边界

静态工具目录恰有 **5** 项：

| 工具 | 角色 |
|------|------|
| `parse_documents` | MinerU 批量解析 → downloads JSON 或 ZIP |
| `extract_archives` | 本地解压 ZIP artifact |
| `lookup_philips_wgq_master_data` | WGQ Tracking XLSX + WGQ / DK 共用 Oracle 补齐 12NC 主数据 |
| `inspect_supply_chain_workbooks` | 共享 XLSX 只读检查 → JSON artifact |
| `finalize_tecan_overseas_recognition` | Tecan 终态 schema 校验并返回 JSON 字符串 |

- WGQ 使用 `ToolStrategy(PhilipsWgqRecognitionResult)`；执行层从 `updates` 读取并再次 Pydantic 校验。
- DK 由 `/skills/tecan_import/SKILL.md` 引导；Agent 必须调用 finalizer，执行层**只**读取该名字的 ToolMessage 并写 `run.result`。
- DK 对确认的唯一 12NC 必须批量调用 `lookup_philips_wgq_master_data`（不传 `tracking_artifact`）；只以返回稳定字段补齐空值。
- 不设业务 SubAgent、业务任务状态表或全局 Tecan middleware。
- WGQ 工具表采用 **denylist**，排除 `finalize_tecan_overseas_recognition`；DK 当前空 denylist，保留共享 `lookup_philips_wgq_master_data` 与 finalizer；均保留共享 MinerU / XLSX 工具，**禁止**业务-only allowlist。
- 工具在 `runtime/tools.py` **静态**注册；不自动扫描目录。
- Skill 单目录：下划线命名的可 import 包内同时存放资源与代码；`package-data` 必须打包 `SKILL.md` / references。

### StructuredOutputRecovery（Philips 专用）

- 仅 WGQ 的 `structured_schema` 非空；DK / 普通 run 为 `None`。
- `after_model` + `jump_to`；`can_jump_to` 必须含 `"model"` 与 **`"end"`**。
- 耗尽必须显式 `jump_to: "end"`，禁止只返回 `None`。
- 空 data 壳：同回合 `tool_call_id` 恢复或 skeleton 纠错；空壳耗尽 → all-null + `partial_success`（技术兜底）。

## 存储、artifact、provider 与 OMS

### 三 SQLite

| 库 | 默认路径 | 用途 |
|----|----------|------|
| Run ledger | `backend/data/dsagents_runs.db` | runs 快照 + append-only events |
| Checkpoints | `backend/data/dsagents_checkpoints.db` | LangGraph `SqliteSaver`（`thread_id=session_id`） |
| Store | `backend/data/dsagents_store.db` | `/memories/`（`SqliteStore`，namespace `("dsagents",)`） |

三库物理分离、连接不共享；无自动 migration。时间戳统一 **UTC+8** `YYYY-MM-DD HH:MM:SS`。`session_id` 单飞锁与 cancel control 均仅**进程内**。

### Artifacts

- 根：`backend/data/artifacts/`（`uploads/`、`downloads/`）。
- 跨层唯一虚拟路径：`/artifacts/...`（禁止 `..`；默认仅接受该前缀；MinerU 解析侧允许 local 为例外）。
- 上传、JSON artifact、解压/解析输出均经 `integrations.artifacts`。
- Agent 视图：`FilesystemBackend` 挂 `/artifacts/` 与 `/large_tool_results/`；`/skills/**` 写拒绝。

### Provider / 出站

| 集成 | 入口 | 失败策略 |
|------|------|----------|
| MiniMax（Anthropic 兼容） | `DeepAgentsBrainFactory`；`MINIMAX_MODEL` / `API_KEY` / `BASE_URL` | 模型/流异常 → run `failed` |
| MinerU | `integrations/mineru.py`；`MINERU_BASE_URL` / `BACKEND` / `TIMEOUT_SECONDS` | 工具异常/超时；可投影 `tool_progress` |
| Oracle（可选） | WGQ / DK 共享 lookup；`ORACLE_DSN` / `USERNAME` / `PASSWORD`；Windows 随仓库 client，`ORACLE_CLIENT_LIB_DIR` 可覆盖 | problems + null，不拖垮已证实结果 |
| OMS JSONL | `runtime/oms_log.py` → `backend/log/oms_log.log` | best-effort，失败不阻塞已创建 run |

- OMS 在 HTTP `create_run` **成功之后**写 `event=run_created` 行（含 `run_id`、`session_id`、`workflow`、`created_at`、从 messages 抽取的 artifact `files[{name,path}]`）；**不是** `run_events`、**无**查询 API、**不含** prompt/thinking/`run.result`。
- API 层可对 `MiniMax-M3` 聚合 usage 并做 CNY 趋势估价（非账单）；未知模型金额为 null。
- 无第二生产 LLM 接线；无 Auth / Webhooks。

## 程序内入口

```python
with AgentResources(...) as resources:
    harness = create_harness(resources)
    for event in harness.execute_run(messages, session_id, run_id, workflow=None):
        ...
```

- HTTP：`api:app` / `create_app()` + `uvicorn`（示例：`uv run uvicorn api:app --host 0.0.0.0 --port 8500`）。
- 程序内调用**不**写 OMS 旁路索引。
- 业务 JSON 的唯一读取路径仍是 ledger 中的 `run.result`。
