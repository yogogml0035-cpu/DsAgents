# 字段合同

`save_tecan_extraction` 使用与 Philips 相同的 envelope：

- `extractor`：`tecan-extractor-a|b|c`
- `source_artifact`：本轮 MinerU 结果的显式 `/artifacts/...` 路径
- `logistics`：只允许 `pieces`、`gross_weight`
- `items`：必须是空数组

每个物流字段只能是 `{value, confidence}`；`confidence` 只允许 `high|medium|low`。缺失值必须是 `{value:null, confidence:"low"}`。不要增加 schema version、risk wrapper、别名或 PDF 之外字段。

AWB 以 `No. of Pieces RCP` / `Gross Weight` 为主要锚点；海运表格按货柜去重求和；德文版以 `Anzahl` / `Gewicht kg` 为锚点。尺寸、体积、箱号、封号、Rate、Chargeable Weight 不是目标值。

`generate_tecan_import` 接收：

- `extraction_artifacts`：A/B（必要时加 C）的显式 extraction artifact 路径列表
- `order_artifact`：订单 `.xlsx` artifact 路径
- `information_artifacts`：一个或多个信息 `.xlsx` artifact 路径
- `decisions`：可选，冲突裁决列表，每项 `{conflict_id, value, reason}`

成功返回 `{"status":"generated","canonical_artifact","artifacts","manual_checks"}`；
业务问题返回 `{"code":"input_problems","problems":[{"source","location","issue","action"}]}`。
信息表对一个料号存在多套不一致记录时，作为 `input_problems` 返回；不再提供来源偏好或按料号覆盖参数。
