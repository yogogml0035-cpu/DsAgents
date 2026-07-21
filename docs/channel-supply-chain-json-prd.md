# 渠道供应链业务抽取 JSON 合同

> 2026-07-22 起，该文档取代旧 Philips 专用 PRD，作为 Philips 外高桥与 Tecan 境外业务的共同验收口径。

## 目标与范围

- 从同票 PDF/XLSX 材料抽取唯一、干净、可供 OMS 消费的订单 JSON。
- 覆盖材料角色识别、同票归集、字段补齐、冲突裁决和 `success` / `partial_success` / `input_problems`。
- 不覆盖普通阅读 Skill 的 JSON 强制输出、OMS 自动保存、Excel 生成、`shipment` 总件数/总毛重、ZIP/DOCX/图片内容解析。

## 材料归集

- 接受任意数量、任意组合的 PDF/XLSX；按内容识别发票、运单、装箱单、订单/合同和主数据，不按文件名或数量猜测。
- 所有有效材料须能唯一归为同一票；多票混入或身份不唯一为 `input_problems`。
- 不支持材料不读内容并写入待确认问题；其它有效材料已足够时继续。
- 发票行按上传顺序、再按原始行顺序。相同 12NC 默认不合并，只有同一业务行的重复副本可去重。
- 同票多发票或多运单以英文逗号稳定连接并全部保留。

## JSON 结构

- Philips 与 Tecan 的 `header` 字段集独立。
- `items[]` 统一 24 字段：`invoice_number`、`invoice_date`、`so_item`、`product_id`、`new_or_used`、`chinese_name`、`specification`、`quantity`、`unit`、`currency`、`unit_price`、`total_price`、`trade_terms`、`origin_country`、`customs_code`、`declaration_elements`、`legal_quantity_1`、`legal_unit_1`、`legal_quantity_2`、`legal_unit_2`、`gross_weight`、`net_weight`、`business_unit`、`pre_or_post_sales`。
- 每个已返回商品行必须包含所有字段；未知为 `null`。本期不输出 `shipment`。
- 正式字段不放未裁决候选；候选线索只写 `problems[{source,location,issue,action}]`。

## 商品事实与格式

- 核心商品事实为票次身份、料号、数量、单位、币种和可确认金额。
- 主数据仅能用唯一、明确的非语义标识补齐；不得按名称相似或多候选猜测，也不得覆盖本票事实。
- 数量、金额、重量仅在同商品行、同币种内按确定性规则计算；冲突或舍入歧义为 `input_problems`。
- 数量、金额、重量为无千分位、非科学计数法字符串；日期 `YYYY-MM-DD`；编号保留前导零；`currency` ISO 三位大写、`trade_terms` 大写、`new_or_used` 为“新/旧”、`pre_or_post_sales` 为“售前/售后”。

## 终态语义

| outcome | 业务含义 |
|---|---|
| `success` | 最终业务数据没有未解决缺失；已解决冲突、候选失败或无关文件不降级 |
| `partial_success` | 核心商品事实已确认，补充字段可安全缺失为 `null` 且逐项说明 |
| `input_problems` | 票次身份或核心事实不能确认；仍返回 JSON、完整 header、已证实字段和复核线索，无法安全形成商品行时 `items: []` |

OMS 只读取 `run.result`。后续渠道 Skill 应复用同一终态语义和字段证据规则，不新增 Excel 或候选输出通道。
