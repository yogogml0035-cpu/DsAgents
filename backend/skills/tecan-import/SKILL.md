---
name: tecan-import
description: 处理用户明确提出的 Tecan/帝肯进口业务，结合空运 PDF、订单 Excel 和配件/设备信息 Excel 生成一个发票箱单；普通 PDF 阅读或通用提取不要使用。
---

# Tecan 帝肯进口

只处理用户本轮消息显式给出的 artifact 路径，不扫描 session、历史上传或"最近任务"。
文件名、消息和内容可作为模型判断证据，但订单/信息表最终由表头和内容校验。

## 适用场景与材料识别

- 用户本轮明确要做 Tecan/帝肯进口业务。
- 材料证据：空运 PDF、订单 `.xlsx`、配件/设备信息 `.xlsx`。
- 仅凭一个普通 PDF 的阅读或通用结构化提取请求不触发本 Skill。

## 输入

- 一个空运 PDF artifact 路径。
- 一个订单 `.xlsx` artifact 路径。
- 一个或多个信息 `.xlsx` artifact 路径。

字段合同见 [references/fields.md](references/fields.md)，Excel 规则见 [references/rules.md](references/rules.md)。

## 固定流程

1. 调用 `parse_documents` 解析空运 PDF，记录唯一明确的 source artifact 路径。
2. 在同一个主模型回合并行发出两个 `task` 调用：`tecan-extractor-a` 和 `tecan-extractor-b`。
3. 两个 task description 必须包含完全相同的 source artifact 路径和抽取要求，只允许 extractor 名不同；只抽 `pieces/gross_weight`，不要继承或转发主智能体结论。
4. 以 A/B 显式 artifact 路径、订单路径和信息表路径调用 `generate_tecan_import`。
5. 按返回结果处理：
   - `generated`：拿到 artifacts，向用户汇报。
   - `input_problems`：主智能体先判断是否属于"抽取器冲突"——若是，重新读取返回的 source artifact，调用 `save_tecan_extraction` 保存 extractor C，或基于 `problems` 里的 `location` 形成 `decisions`，再把 A/B/C 与 decisions 显式传回 `generate_tecan_import`；其余问题向用户复述 `problems`（source/location/issue/action）并结束当前 run。
6. 下一 run 必须重新显式传入 PDF 解析结果、A/B/C、订单、信息表路径；不保存冲突任务或恢复游标。
7. 信息表对一个料号存在多套不一致记录时，作为 `input_problems` 返回；用户清理信息表使每个料号只保留一套数据后重新发起，不修改源 Excel。
8. 只调用 `generate_tecan_import(...)` 生成一个 Excel。

## 失败处理

- 业务问题统一为 `{"code":"input_problems","problems":[{"source","location","issue","action"}]}`；存在问题就结束 run，用户修正材料后重新发起。
- 不暂停、不恢复、不跨 run 续接业务状态。

## 禁止

- 不使用状态型追问工具、HITL、流程游标、当前业务标记或 Skill 锁。
- 不扫描 `/artifacts/`、session 或历史消息来猜路径。
- 不把 null 当反对票，不覆盖或编辑 JSON/Excel artifact。
- generator 不接受 raw extraction、Markdown 或兼容 fallback（必须经 `generate_tecan_import` 内部 canonical 构建）。
