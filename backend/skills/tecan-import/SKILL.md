---
name: tecan-import
description: 处理用户明确提出的 Tecan/帝肯进口业务，结合空运 PDF、订单 Excel 和配件/设备信息 Excel 生成一个发票箱单；普通 PDF 阅读或通用提取不要使用。
---

# Tecan 帝肯进口

只处理用户本轮消息显式给出的 artifact 路径，不扫描 session、历史上传或“最近任务”。
文件名、消息和内容可作为模型判断证据，但订单/信息表最终由表头和内容校验。

## 输入

- 一个空运 PDF artifact 路径。
- 一个订单 `.xlsx` artifact 路径。
- 一个或多个信息 `.xlsx` artifact 路径。

字段合同见 [references/fields.md](references/fields.md)，Excel 规则见 [references/rules.md](references/rules.md)。

## 固定流程

1. 调用 `parse_documents` 解析空运 PDF，记录唯一明确的 source artifact 路径。
2. 在同一个主模型回合并行发出两个 `task` 调用：`tecan-extractor-a` 和 `tecan-extractor-b`。
3. 两个 task description 必须包含完全相同的 source artifact 路径和抽取要求，只允许 extractor 名不同；只抽 `pieces/gross_weight`，不要继承或转发主智能体结论。
4. 以 A/B 显式 artifact 路径、订单路径和信息表路径调用 `build_tecan_canonical`。
5. 按 builder 的 `status` 处理：
   - `canonical`：进入第 8 步。
   - `needs_c`：主智能体重新读取返回的 `source_artifact`，调用 `save_tecan_extraction` 写 extractor C，再显式传回 builder。
   - `needs_adjudication`：只回查最小 conflict list，调用 `save_tecan_adjudication` 写 decisions artifact，再显式传回 builder。
   - `needs_input`：向用户询问缺失物流值或信息表来源选择并结束当前 run。
6. 下一 run 必须重新显式传入 PDF 解析结果、A/B/C、订单、信息表路径及用户选择；不保存冲突任务或恢复游标。
7. 信息表冲突时可传 `info_source_preference`，或用 `pn_info_source_overrides` 对单个 PN 选择来源；不要修改源 Excel。
8. 只调用 `generate_tecan_documents(canonical_artifact=...)` 生成一个 Excel。

## 禁止

- 不使用状态型追问工具、HITL、流程游标、当前业务标记或 Skill 锁。
- 不扫描 `/artifacts/`、session 或历史消息来猜路径。
- 不把 null 当反对票，不覆盖或编辑 JSON/Excel artifact。
- generator 不接受 raw extraction、Markdown 或兼容 fallback。
