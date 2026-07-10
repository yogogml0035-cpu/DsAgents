# 字段合同

`save_tecan_extraction` 使用与 Philips 相同的 envelope：

- `extractor`：`tecan-extractor-a|b|c`
- `source_artifact`：本轮 MinerU 结果的显式 `/artifacts/...` 路径
- `logistics`：只允许 `pieces`、`gross_weight`
- `items`：必须是空数组

每个物流字段只能是 `{value, confidence}`；`confidence` 只允许 `high|medium|low`。缺失值必须是 `{value:null, confidence:"low"}`。不要增加 schema version、risk wrapper、别名或 PDF 之外字段。

AWB 以 `No. of Pieces RCP` / `Gross Weight` 为主要锚点；海运表格按货柜去重求和；德文版以 `Anzahl` / `Gewicht kg` 为锚点。尺寸、体积、箱号、封号、Rate、Chargeable Weight 不是目标值。
