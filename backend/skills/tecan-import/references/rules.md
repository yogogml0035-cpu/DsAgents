# Excel 与业务规则

- 订单表由 `PN`、`Order Qty`、`Amount` 内容表头识别；`Net Price` 缺失时按 `Amount / Order Qty` 推导。
- 金额可带货币符号、千分位或 Excel number format；整张订单必须是单一币种。
- 信息表由 `料号`、`英文品名`、`原产国`、`净重|参考净重` 识别。
- `Sheet1` 是优先来源；缺少完整唯一记录时继续跨 worksheet 查找，并在 canonical 标注实际来源。
- 同一订单 PN 的信息来源冲突必须由用户选择；未用于本订单的 PN 冲突不阻断。
- 行净重 = 单位净重 × 订单数量；总毛重按行净重比例分摊，最后一行吸收舍入差，保持总量守恒。
- 最终 Excel 的币种跟随订单，件数和毛重只来自 canonical。
