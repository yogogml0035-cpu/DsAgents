# 确定性业务规则

- `product_id` 去空格和连字符；超过 12 位时 `normalized_product_id` 取末 12 位。
- 同 `normalized_product_id` 合并：数量、总价、毛重求和；单价必须一致；PO 去空格、按出现顺序去重并用 `/` 拼接。
- 国家名称/代码、币种、数字格式先规范化再比较。
- shipment 毛重保留原小数，Excel 写数值；按商品毛重、tracking 历史或数量权重分摊并保持总量。
- 净重按 tracking 历史 `当前毛重 × 历史净重 / 历史毛重` 向上取整；无有效历史时标人工校验。
- tracking、invoice/packing、核注清单都只消费同一 canonical。
- Oracle 只读 `ORACLE_DSN/USERNAME/PASSWORD/CLIENT_LIB_DIR/TIMEOUT_SECONDS`；配置缺失、无记录或查询失败均继续生成并标人工校验。
- tracking 历史和申报要素按内容表头读取；不修改上传原件。
