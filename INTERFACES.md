# DsAgents 接口与边界

> 本轮刷新：2026-07-22。具体 backend 实现事实见 `backend/.planning/codebase/INTEGRATIONS.md`。

## HTTP 合同

| 端点 | 请求 | 成功返回 | 主要边界 |
|---|---|---|---|
| `POST /upload` | multipart `files` | `{files:[{file_path,name,mime_type,size}]}` | 只存上传文件，返回 `/artifacts/uploads/...` |
| `POST /runs` | `{workflow?,session_id?,messages[]}` | `{run_id,session_id,status:"queued"}` | 后台执行；无 SSE |
| `GET /runs/{run_id}` | 可选 `after_event_id` | `{run,workflow,result,events,latest_content_event,usage}` | 轮询唯一查询面 |
| `POST /runs/{run_id}/cancel` | 无 body | cancel 状态 | 协作式 drain，不强杀外部调用 |

`messages[]` 项为 `{role, content:[{type:"text",text}|{type:"artifact",path}]}`，请求模型 `extra="forbid"`。旧 `{message:"..."}` 体不支持。

`workflow` 只允许 `philips_wgq_inbound_recognition` 或省略。Philips workflow 不能携带客户端 `session_id`，服务端总是分配新 session；通用/Tecan Skill 请求保留普通 session 语义。

## run、事件与结果

`GET /runs/{run_id}` 的 `run` 是 ledger 快照，顶层 `result` 与 `run.result` 相同。OMS 只消费 `result`，不依赖 `reply`、Excel、候选工具结果或审计文本。

固定事件类型：

| 类型 | 含义 |
|---|---|
| `status` | queued/running/succeeded/failed/cancelling/cancelled 投影 |
| `tool_execution` | 工具调用观测 |
| `tool_progress` | MinerU / 解压进度 |
| `thinking` | 主 Agent thinking delta |
| `text_delta` | 主 Agent 文本 delta |
| `assistant_message` | 最终助手消息摘要 |
| `model_usage` | 模型调用 token 观测 |

`run_events` append-only；`runs` 只保存投影。`session_id` 不是对外状态资源。

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
- 每个 `items[]` 行都包含 24 字段：`invoice_number`、`invoice_date`、`so_item`、`product_id`、`new_or_used`、`chinese_name`、`specification`、`quantity`、`unit`、`currency`、`unit_price`、`total_price`、`trade_terms`、`origin_country`、`customs_code`、`declaration_elements`、`legal_quantity_1`、`legal_unit_1`、`legal_quantity_2`、`legal_unit_2`、`gross_weight`、`net_weight`、`business_unit`、`pre_or_post_sales`。
- 不输出 `shipment`、Excel、候选值、置信度或审计轨迹。未知值为 `null`，不使用空字符串。
- 数量、金额、重量是无千分位、非科学计数法字符串；日期为 `YYYY-MM-DD`；编号保留原样/前导零；`currency` 为大写三位，`trade_terms` 大写。

### header 差异

| Philips `OrderHeader` | Tecan `TecanHeader` |
|---|---|
| `om,dn,po,so,original_waybill_number,buyer,seller,shipper,consignee,payment_terms,contract_number,salesperson,invoice_number,etd,trade_terms,port_of_departure,port_of_arrival` | `po,dn,original_waybill_number,buyer,seller,shipper,consignee,payment_terms,contract_number,invoice_number,invoice_date,trade_terms,port_of_departure,port_of_arrival` |

### outcome 与 run 终态

| outcome / 条件 | 含义 | run 状态 |
|---|---|---|
| `success` | 无未解决业务字段缺失；已解决冲突或无关材料不降级 | `succeeded` |
| `partial_success` | 核心商品事实已确认，补充字段为 `null` 且列入 problems | `succeeded` |
| `input_problems` | 票次或核心事实不能确认；只带已证实字段和复核线索 | `succeeded` |
| Philips structured response 缺失/非法、工具或运行时异常 | 无有效业务终态 | `failed` |

Philips 空 data 壳的 recovery 耗尽会生成 all-null `partial_success` runtime fallback；它是防止图循环的技术兜底，不是业务 Skill 的正常裁决模板。

正常渠道 schema 会将“有缺失却声明 `success`”归正为 `partial_success`，补入尚未在 `problems` 明确列出的缺失路径，并将“字段已完整却声明 `partial_success`”归正为 `success`；因此无关附件或已解决冲突不能让最终结果被误降级。

## 渠道材料边界

- PDF 调用 `parse_documents`；XLSX 调用 `inspect_supply_chain_workbooks`，后者只读并返回 JSON artifact。
- 两渠道均按内容而非文件名识别发票、运单、装箱单、订单/合同和主数据；材料必须安全归为同一票，否则 `input_problems`。
- 发票行按上传顺序/原始行顺序；相同 12NC 默认不合并；多发票、多运单按材料出现顺序用英文逗号连接。
- 本票事实优先于主数据；仅唯一非语义标识允许补齐。冲突、舍入歧义和多候选不得写入正式字段。
- ZIP、DOCX、图片不解析内容，在 `problems` 说明；其余材料足够时继续。

## 工具与运行时边界

静态工具目录恰有五项：`parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`、`inspect_supply_chain_workbooks`、`finalize_tecan_overseas_recognition`。

- Philips workflow 使用 `ToolStrategy(PhilipsWgqRecognitionResult)`；执行层从 `updates` 读取并再次 Pydantic 校验。
- Tecan 由 `/skills/tecan-import/SKILL.md` 引导；Agent 必须调用 finalizer，执行层只读取该名字的 ToolMessage 并写 `run.result`。
- 不设 Tecan HTTP workflow、A/B/C SubAgent、业务任务状态或全局 Tecan middleware。
- Philips 工具表采用 denylist，只排除 Tecan finalizer，保留共享 MinerU/XLSX 和 Philips lookup。

## 存储、artifact 和 OMS

- `/artifacts/...` 是唯一跨层文件路径；上传、JSON artifact、解压/解析输出均通过 `integrations.artifacts` 处理。
- SQLite run/checkpoint/store 三库分离。`session_id` 单飞锁和 cancel control 均仅进程内。
- OMS JSONL 在 HTTP create_run 成功后 best-effort 写入 `backend/log/oms_log.log`，不属于 `run_events`、无查询 API、不含业务 result，失败不阻塞 run 创建。
- Oracle 只服务 Philips 主数据补齐；thick mode 依赖 `ORACLE_CLIENT_LIB_DIR`，缺失/失败转 problems/null。

## 程序内入口

```python
with AgentResources(...) as resources:
    harness = create_harness(resources)
    for event in harness.execute_run(messages, session_id, run_id, workflow=None):
        ...
```

程序内调用不写 OMS 旁路索引。业务 JSON 的唯一读取路径仍是 ledger 中的 `run.result`。
