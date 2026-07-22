# Tecan JSON 字段合同

`finalize_tecan_overseas_recognition(result)` 接收并校验：

```json
{"outcome":"success|partial_success|input_problems","data":{"header":{},"items":[]},"problems":[{"source":"","location":"","issue":"","action":""}]}
```

Tecan `header`：`po`、`dn`、`original_waybill_number`、`buyer`、`seller`、`shipper`、`consignee`、`payment_terms`、`contract_number`、`invoice_number`、`invoice_date`、`trade_terms`、`port_of_departure`、`port_of_arrival`。

所有 `items[]` 行均须出现这 24 个字段：`invoice_number`、`invoice_date`、`so_item`、`product_id`、`new_or_used`、`chinese_name`、`specification`、`quantity`、`unit`、`currency`、`unit_price`、`total_price`、`trade_terms`、`origin_country`、`customs_code`、`declaration_elements`、`legal_quantity_1`、`legal_unit_1`、`legal_quantity_2`、`legal_unit_2`、`gross_weight`、`net_weight`、`business_unit`、`pre_or_post_sales`。

- 未知为 `null`；`input_problems` 仍给完整 header，`items` 可为空。
- 数量、金额、重量为无千分位、非科学计数法字符串；日期为 `YYYY-MM-DD`。
- 编号保留原始字符与前导零；`currency` 是 ISO 三位大写，`trade_terms` 大写；`new_or_used` 仅“新/旧”，`pre_or_post_sales` 仅“售前/售后”。
