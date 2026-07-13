# 字段合同

`save_philips_wgq_extraction` 顶层固定为：

- `extractor`：`philips-wgq-extractor-a|b|c`
- `source_artifact`：本轮 MinerU 结果的显式 `/artifacts/...` 路径
- `logistics`：`hawb_number`、`pieces`、`gross_weight`、`shipper_country`
- `items[]`：`product_id_raw`、`description`、`quantity`、`unit_price`、`total_price`、`currency`、`po_number`、`raw_country`、`gross_weight`

每个业务字段只能是 `{value, confidence}`；`confidence` 只允许 `high|medium|low`。缺失值必须是 `{value:null, confidence:"low"}`。不要增加 `schema_version`、risk、evidence、source_file、page、invoice_number、delivery_no 或别名。

商品行按 commercial invoice 原始出现顺序逐条保留且不合并；同料号重复行也不得省略。`net_weight` 不属于 PDF 抽取字段。

`generate_philips_wgq_import` 接收：

- `extraction_artifacts`：A/B（必要时加 C）的显式 extraction artifact 路径列表
- `tracking_artifact`：tracking `.xlsx`/`.xlsm` artifact 路径
- `international_forwarder`：DHL / DSV / FEDEX / UPS / 康捷空
- `customs_mode`：`普货`（默认）或 `快件`
- `decisions`：可选，冲突裁决列表，每项 `{conflict_id, value, reason}`

成功返回 `{"status":"generated","canonical_artifact","artifacts","manual_checks"}`；
业务问题返回 `{"code":"input_problems","problems":[{"source","location","issue","action"}]}`。
