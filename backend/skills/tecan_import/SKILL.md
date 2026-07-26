---
name: tecan-import
description: 仅用于 API workflow=DK 或用户明确要求 Tecan/帝肯境外供应链订单 JSON 抽取；从本轮 PDF/XLSX 同票材料归集为 OMS 可消费的终态 JSON，不生成 Excel。
---

# Tecan 境外供应链抽取

只用于 DK 或用户明确的 Tecan 境外供应链请求。渠道公共的材料批处理、单票归集、12NC 补齐和结构化终态由 runtime workflow 提示规定。

## Tecan 识别规则

1. 按内容识别商业发票、运单、装箱单、订单/合同和主数据；DK 不传 `tracking_artifact`。
2. 用运单号、发票号、PO/DN 与买卖/收发货关系归集同票。
3. 多发票/多运单以英文逗号稳定连接；发票行保持上传和原行顺序。
4. 共享主数据只补稳定字段的 `null`，不得覆盖本票交易/运输事实。

## Tecan header 与字段裁决

`header`：`po`、`dn`、`original_waybill_number`、`buyer`、`seller`、`shipper`、`consignee`、`payment_terms`、`contract_number`、`invoice_number`、`invoice_date`、`trade_terms`、`port_of_departure`、`port_of_arrival`。

字段和格式见 [references/fields.md](references/fields.md)。无 workflow 的明确 Tecan 请求仍调用 `finalize_tecan_overseas_recognition` 提交终态；DK workflow 使用自身结构化 schema。
