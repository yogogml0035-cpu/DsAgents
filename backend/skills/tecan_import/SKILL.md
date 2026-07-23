---
name: tecan_import
description: 仅用于 API workflow=DK 或用户明确要求 Tecan/帝肯境外供应链订单 JSON 抽取；从本轮 PDF/XLSX 同票材料归集为 OMS 可消费的终态 JSON，不生成 Excel。
---

# Tecan 境外供应链抽取

只处理本轮显式 artifact。最终必须调用 `finalize_tecan_overseas_recognition`；它的返回值就是 `run.result`，自然语言只作简短摘要。

## 材料与流程

1. 将 PDF 一次传给 `parse_documents`；将全部 XLSX 一次传给 `inspect_supply_chain_workbooks`，再读取各 `result_path`。
2. 依据内容动态识别商业发票、运单、装箱单、订单/合同和主数据，不按文件名或固定数量猜测。
3. 用运单号、发票号、PO/DN 与买卖/收发货关系归集同票。材料无法唯一归票或混入多票时使用 `input_problems`，但仍提交已确认的 `data`；无法安全形成商品行时 `items: []`。
4. 发票行按上传顺序、再按原行顺序保留；同 12NC 默认不合并，只去掉同一业务行的重复副本。多发票/多运单以英文逗号稳定连接。
5. 本票交易与运输事实优先；主数据只能通过唯一、明确的非语义标识补齐，禁止按名称相似或多候选猜测。数量、金额、重量只在同一商品行、同币种内按可复核规则计算；冲突或舍入歧义为 `input_problems`。
6. ZIP/DOCX/图片不读取；在 `problems` 说明。其它材料足以确认同票时继续。

## 终态规则

- `success`：没有未解决缺失。已解决冲突、候选失败、无关文件不降级。
- `partial_success`：核心商品事实（票次、料号、数量、单位、币种、可确认金额）已确认，补充字段缺失在 `problems` 明确列出。
- `input_problems`：票次或核心商品事实无法确认；正式字段只放已证实值，未裁决候选仅写入 `problems`。
- 不输出 `shipment`、Excel、候选列表或审计明细。

字段和格式见 [references/fields.md](references/fields.md)。正常路径只调用终态工具，不在文本重复 JSON。
