---
name: philips-wgq-inbound-recognition
description: 仅用于 API workflow=philips_wgq_inbound_recognition 的飞利浦外高桥进境 PDF 识别、跨单据合并和 OMS 结构化结果；普通 PDF 阅读、旧 Excel 生成流程或其他 workflow 不使用。
---

# 飞利浦外高桥进境智能识别

API 已选择本工作流。只处理本轮消息显式给出的 artifact，不扫描历史上传、session 或“最近文件”。最终必须提交 `PhilipsWgqRecognitionResult` 结构化响应；自然语言 `reply` 只作简短摘要，不能承载业务 JSON。

## 输入边界

- 接受 1–10 个 PDF，另可有 1 个 Tracking `.xlsx`。
- ZIP、DOCX、图片和其他 Excel 不解析、不解包；把它们写入 `problems`，但只要有效 PDF 能形成唯一票次就继续。
- 没有有效 PDF、超过 PDF 上限、运单与发票无法关联，或混入两个以上真实票次时，返回 `input_problems` 且 `data=null`。
- 当前消息已经给出全部路径；不得调用 `ls`、`glob` 或 `task` 重新发现材料，也不得委派 SubAgent。

## 固定流程

1. 从当前 artifact 清单筛出 PDF 与可选 Tracking；一次调用 `parse_documents` 解析全部 PDF，读取其 `result_path`。
2. 一次完成全部 PDF 的运单、发票和商品行抽取。以共同运单号、发票号、PO、SO、DN、OM 及一致的收发货关系判断是否属于同一真实票次；不要评分或自动拆成 `orders[]`。
3. 商品行按发票上传顺序、再按原始行顺序输出；重复 12NC 保留为不同商品行，不合并。
4. 用 PDF 得到的 12NC 调用一次 `lookup_philips_wgq_master_data(product_ids, tracking_artifact)`。Tracking 只能作为该工具的参数；严禁用 `read_file`、`parse_documents`、`grep` 或其他工具查看 `.xlsx`。
5. 工具返回后立即按下列优先级提交最终结构化响应，不再尝试补查缺失主数据。所有固定字段都要出现，未识别值为 `null`。

## 数据优先级

- 运单号、发票号、PO/SO/DN/OM、数量、单位、币种、单价、总价、件数、重量、买卖方和收发货方：只取本批 PDF；Tracking/Oracle 不得覆盖。
- 中文品名、规格型号、原产国、海关编码、法定单位、新旧：Tracking，再 Oracle，再 `null`。
- 申报要素、BU：Tracking，再 `null`；不得猜 Oracle 表列。
- 法一/法二数量：只取本批文件明确值或文件内已确认换算依据，否则 `null`。
- 商品级毛重、净重：只取当前文件明确值；不得读取历史重量、分摊 shipment 总重量或把总重量复制到每行。

数量、金额、重量使用无千分位十进制字符串；`ETD` 仅在明确时输出 `YYYY-MM-DD`。不得输出“未找到”“需确认”等占位字符串。

## outcome

- `success`：形成唯一票次且没有需提示的问题，`problems=[]`。
- `partial_success`：形成唯一票次并有可回填数据，但存在无关附件、PDF 个别失败、Tracking/Oracle 失败或未命中、非核心字段问题。
- `input_problems`：无法安全形成唯一票次；`data=null`，不得返回可能混票的数据。

补充源失败不能丢弃 PDF 结果。`problems` 每项都填写 `source/location/issue/action`。
