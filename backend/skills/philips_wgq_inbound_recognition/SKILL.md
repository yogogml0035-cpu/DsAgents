---
name: philips-wgq-inbound-recognition
description: 仅用于 API workflow=WGQ 的飞利浦外高桥同票 PDF/XLSX 订单 JSON 抽取；不生成 Excel，也不用于普通文件阅读。
---

# 飞利浦外高桥供应链抽取

只处理本轮显式 artifact。最终必须通过 `PhilipsWgqRecognitionResult` 结构化工具提交；`run.result` 是唯一 OMS 合同，文本只作摘要。

## 材料归集

1. 将可支持的 PDF 一次调用 `parse_documents`；将全部 XLSX 一次调用 `inspect_supply_chain_workbooks`，再按内容识别 Tracking 与主数据材料。只将唯一确认的 Tracking 传给 `lookup_philips_wgq_master_data`。
2. 按内容识别发票、运单、装箱单、订单/合同和 Tracking；不要求固定文件数，不按文件名猜测。
3. 以运单号、发票号、PO/SO/DN/OM 与买卖/收发货关系确认唯一票次。无法唯一归票或混入多票时提交 `input_problems`；保留已证实字段，无法安全形成商品行时 `items: []`。
4. 同 HAWB/运单且收发货方一致的多商业发票是同一票：`header.invoice_number`、`original_waybill_number`、`po`、`dn` 用英文逗号按材料顺序连接，items 按发票上传顺序与原始行顺序展开。相同 12NC 默认不合并，只删除同一业务行的重复副本。
5. ZIP/DOCX/图片不解析，列入 `problems`；其它材料足以确认时继续。

先按 [references/freight-forwarders.md](references/freight-forwarders.md) 识别 DHL、DSV、FedEx、UPS 与康捷空的版式；它只帮助定位字段，不能替代单据标签和交叉证据。

## 字段和证据

`header`：`om`、`dn`、`po`、`so`、`original_waybill_number`、`buyer`、`seller`、`shipper`、`consignee`、`payment_terms`、`contract_number`、`salesperson`、`invoice_number`、`etd`、`trade_terms`、`port_of_departure`、`port_of_arrival`。

每项 `items[]` 必须有 24 字段：`invoice_number`、`invoice_date`、`so_item`、`product_id`、`new_or_used`、`chinese_name`、`specification`、`quantity`、`unit`、`currency`、`unit_price`、`total_price`、`trade_terms`、`origin_country`、`customs_code`、`declaration_elements`、`legal_quantity_1`、`legal_unit_1`、`legal_quantity_2`、`legal_unit_2`、`gross_weight`、`net_weight`、`business_unit`、`pre_or_post_sales`。

- 本票交易/运输事实优先；Tracking/Oracle 只补稳定字段，不能覆盖数量、金额、重量、单号或运单。
- 12NC 仅可由唯一明确的非语义标识补齐；不得按名称相似或多候选猜测。
- 同商品行、同币种内可确定性计算数量/金额/重量；舍入歧义或冲突为 `input_problems`。不输出 shipment 总件数/总毛重，也不分摊到行。
- 数量、金额、重量是无千分位、非科学计数法字符串；日期 `YYYY-MM-DD`；编号保留前导零；币种 ISO 三位大写，Incoterm 大写，新旧仅“新/旧”，售前售后仅“售前/售后”。

## outcome

- `success`：没有未解决缺失；已解决冲突、候选失败或无关文件不降级。
- `partial_success`：核心商品事实已确认，非核心缺失为 `null` 且逐项写入 `problems`。
- `input_problems`：票次或核心事实不能确认；`data` 仍包含完整 `header` 与 `items`（可为空），正式字段不能放未裁决候选。

正常路径只调用结构化工具，不在文本重复 JSON；`data: {}` 永远无效。
