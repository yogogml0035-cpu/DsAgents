---
name: philips-wgq-inbound-recognition
description: 仅用于 API workflow=WGQ 的飞利浦外高桥同票 PDF/XLSX 订单 JSON 抽取；不生成 Excel，也不用于普通文件阅读。
---

# 飞利浦外高桥供应链抽取

只用于 WGQ。渠道公共的材料批处理、单票归集、12NC 补齐和结构化终态由 runtime workflow 提示规定。

## WGQ 识别规则

1. 按内容识别 WGQ 发票、运单、装箱单、订单/合同和 Tracking；不按文件名猜测。
2. 用运单号、发票号、PO/SO/DN/OM 与买卖/收发货关系确认唯一票次。
3. 同 HAWB/运单且收发货方一致的多商业发票是同一票：`header.invoice_number`、`original_waybill_number`、`po`、`dn` 用英文逗号按材料顺序连接。
4. 仅将唯一确认的 Tracking 传给 `lookup_philips_wgq_master_data`。

先按 [references/freight-forwarders.md](references/freight-forwarders.md) 识别 DHL、DSV、FedEx、UPS 与康捷空的版式；它只帮助定位字段，不能替代单据标签和交叉证据。

## WGQ header 与字段裁决

`header`：`om`、`dn`、`po`、`so`、`original_waybill_number`、`buyer`、`seller`、`shipper`、`consignee`、`payment_terms`、`contract_number`、`salesperson`、`invoice_number`、`etd`、`trade_terms`、`port_of_departure`、`port_of_arrival`。

- 本票交易/运输事实优先；Tracking/Oracle 只补稳定字段，不能覆盖数量、金额、重量、单号或运单。
- 12NC 仅可由唯一明确的非语义标识补齐；不得按名称相似或多候选猜测。
- `etd` 只取可确认的离港日期；WGQ `om`、`so` 和 `salesperson` 不从 Tecan 字段推断。
