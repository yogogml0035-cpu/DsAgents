---
name: philips-wgq-inbound-recognition
description: 仅用于 API workflow=philips_wgq_inbound_recognition 的飞利浦外高桥进境 PDF 识别、跨单据合并和 OMS 结构化结果；普通 PDF 阅读、旧 Excel 生成流程或其他 workflow 不使用。
---

# 飞利浦外高桥进境智能识别

API 已选择本工作流。只处理本轮消息显式给出的 artifact，不扫描历史上传、session 或“最近文件”。最终必须提交 `PhilipsWgqRecognitionResult` 结构化响应；自然语言 `reply` 只作简短摘要，不能承载业务 JSON。

**契约：tool 参数与 `run.result` 一律使用英文字段名。** 禁止中文 key、XML 或 `$text` 包装。OMS 外高桥中文列名由调用方映射。

## 输入边界

- 接受 1–10 个 PDF，另可有 1 个 Tracking `.xlsx`。
- **用户上传**的 ZIP、DOCX、图片和其他 Excel 不作业务主输入：不解析、不解包；记入 `problems`，但只要有效 PDF 能形成唯一票次就继续。
- 与上条区分：`parse_documents` 对 **PDF** 返回的 `archive_path`（MinerU ZIP 产物）不是用户业务 ZIP；见固定流程第 1 步。
- 没有有效 PDF、超过 PDF 上限、运单与发票无法关联，或混入两个以上真实票次时，返回 `input_problems` 且 `data=null`。
- 当前消息已经给出全部路径；不得调用 `ls`、`glob` 或 `task` 重新发现材料，也不得委派 SubAgent。

## 固定流程

1. 从当前 artifact 清单筛出 PDF 与可选 Tracking；一次调用 `parse_documents` 解析全部 PDF，优先读取其 `result_path`。仅当返回 `archive_path`（MinerU 对 PDF 的 ZIP 产物）时调用 `extract_archives`，再对解压出的文本/Markdown `read_file`；不要把该 ZIP 当 UTF-8 文本直接 `read_file`。
2. 一次完成全部 PDF 的运单、发票和商品行抽取。以共同运单号、发票号、PO、SO、DN、OM 及一致的收发货关系判断是否属于同一真实票次；不要评分或自动拆成 `orders[]`。
   - **同一 HAWB/运单 + 一致收发货方 + 多张商业发票**（常见 UPS 普货：一份 AWB + 一份 INV 内含两张 Invoice/PO/DN）：按**一票 consolidated** 处理，不要 `input_problems`。
   - `header.invoice_number` / `po` / `dn` 可将多值用英文逗号拼接；`items` 按发票出现顺序展开为多行（每发票商品行保留）。
3. 商品行按发票上传顺序、再按原始行顺序输出；重复 12NC 保留为不同商品行，不合并。
4. 用 PDF 得到的 12NC 调用一次 `lookup_philips_wgq_master_data(product_ids, tracking_artifact)`。Tracking 只能作为该工具的参数；严禁用 `read_file`、`parse_documents`、`grep` 或其他工具查看 `.xlsx`。
5. 工具返回后立即按下列优先级提交最终结构化响应，不再尝试补查缺失主数据。所有固定字段都要出现，未识别值为 `null`。

## 英文字段名

`shipment`：`pieces`、`total_gross_weight`。

`header`：`om`、`dn`、`po`、`so`、`original_waybill_number`、`buyer`、`seller`、`shipper`、`consignee`、`payment_terms`、`contract_number`、`salesperson`、`invoice_number`、`etd`、`port_of_departure`、`port_of_arrival`。

`items[]`：`so_item`、`product_id`、`new_or_used`、`chinese_name`、`specification`、`quantity`、`unit`、`currency`、`unit_price`、`total_price`、`origin_country`、`customs_code`、`declaration_elements`、`legal_quantity_1`、`legal_unit_1`、`legal_quantity_2`、`legal_unit_2`、`gross_weight`、`net_weight`、`business_unit`、`pre_or_post_sales`。

主数据工具返回同样使用 `product_id` 与上列稳定字段英文名。

## 数据优先级

- 运单号、发票号、PO/SO/DN/OM、数量、单位、币种、单价、总价、件数、重量、买卖方和收发货方：只取本批 PDF；Tracking/Oracle 不得覆盖。
- 中文品名、规格型号、原产国、海关编码、法定单位、新旧：Tracking，再 Oracle，再 `null`（`chinese_name` 等）。
- 申报要素、BU：Tracking，再 `null`（`declaration_elements`、`business_unit`）；不得猜 Oracle 表列。
- 法一/法二数量：只取本批文件明确值或文件内已确认换算依据，否则 `null`。
- 商品级毛重、净重：只取当前文件明确值；不得读取历史重量、分摊 shipment 总重量或把总重量复制到每行。

数量、金额、重量使用无千分位十进制字符串；`etd` 仅在明确时输出 `YYYY-MM-DD`。不得输出“未找到”“需确认”等占位字符串。

## outcome

- `success`：形成唯一票次且 `data` 可回填。`problems` 可为空，也可记录非阻断提示（字段缺失、主数据未命中、PDF 仅有 LBS 小计等）；**不必**为了有 problems 而改成 `partial_success`。
- `partial_success`：可选；形成唯一票次且有可回填数据，并**至少**一条 `problems`（例如希望显式标记“部分完成”时使用）。
- `input_problems`：无法安全形成唯一票次；`data=null`，不得返回可能混票的数据。

补充源失败不能丢弃 PDF 结果。`problems` 每项都填写 `source/location/issue/action`。

## 结构化提交硬约束

最终必须通过 `PhilipsWgqRecognitionResult` 结构化工具提交；`reply` 只作摘要，**不能**代替 tool 参数。

**提交顺序（降空壳）：** ① 文本中先写恰好一个完整 ```json```（含完整 `data`，未知 `null`）；② 再调用工具且 **args 与该 JSON 相同**；运行时可从合法文本 JSON 恢复。**`data: {}` 永远无效**；只改 `problems` 不算修正。

- **禁止** `data: {}`、省略 `data`、或只填顶层 `outcome`/`problems` 却空业务体。
- `success` / `partial_success`：`data` 须含 `shipment`（`pieces`、`total_gross_weight`）、`header`（全部固定英文字段）、`items`（非空，每行全字段）；未知 `null`。
- `input_problems`：`data` 为 JSON `null`（非 `{}`），且 `problems` 至少一条。
- 校验失败须用完整嵌套重提；禁止重复空 `data: {}`。文本恢复仍以 schema 为准。

最小合法形状（null 换识别值；多商品复制 item）：`{"outcome":"success","data":{"shipment":{"pieces":null,"total_gross_weight":null},"header":{"om":null,"dn":null,"po":null,"so":null,"original_waybill_number":null,"buyer":null,"seller":null,"shipper":null,"consignee":null,"payment_terms":null,"contract_number":null,"salesperson":null,"invoice_number":null,"etd":null,"port_of_departure":null,"port_of_arrival":null},"items":[{"so_item":null,"product_id":null,"new_or_used":null,"chinese_name":null,"specification":null,"quantity":null,"unit":null,"currency":null,"unit_price":null,"total_price":null,"origin_country":null,"customs_code":null,"declaration_elements":null,"legal_quantity_1":null,"legal_unit_1":null,"legal_quantity_2":null,"legal_unit_2":null,"gross_weight":null,"net_weight":null,"business_unit":null,"pre_or_post_sales":null}]},"problems":[]}`
