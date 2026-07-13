---
name: philips-wgq-import
description: 处理用户明确提出的 Philips/飞利浦外高桥进境业务，基于 PDF 抽取与 tracking Excel 生成 tracking、invoice/packing、核注清单；普通 PDF 阅读或通用提取不要使用。
---

# Philips 外高桥进境

只处理用户本轮消息显式给出的 artifact 路径，不扫描 session、历史上传或"最近文件"。
文件名、消息和内容都是判断意图的证据，但不得把文件名映射成硬编码路由。

## 适用场景与材料识别

- 用户本轮明确要做 Philips/飞利浦外高桥进境业务。
- 材料证据：当前批次 PDF（运单/发票）、tracking `.xlsx`/`.xlsm`、用户显式给出的货代。
- 仅凭一个普通 PDF 的阅读或通用结构化提取请求不触发本 Skill。

## 输入

- 一个或多个当前批次 PDF artifact 路径。
- 一个 tracking `.xlsx`/`.xlsm` artifact 路径。
- `international_forwarder` 必须由用户明确提供：DHL、DSV、FedEx、UPS、康捷空。
- 未说明"普货/快件"时使用"普货"。缺货代时直接提问并结束当前 run。

字段合同见 [references/fields.md](references/fields.md)，业务规则见 [references/rules.md](references/rules.md)。

## 固定流程

1. 调用 `parse_documents` 解析本批次 PDF；记录返回的唯一 `result_path` 或解包后的明确 Markdown/JSON artifact 路径。
2. 在同一个主模型回合并行发出两个 `task` 调用：
   - `philips-wgq-extractor-a`
   - `philips-wgq-extractor-b`
3. 两个 task description 必须包含完全相同的 source artifact 路径和抽取要求，只允许 extractor 名不同；不要转发主智能体结论或字段值。
4. 两路返回后，以显式 extraction artifact 路径调用 `generate_philips_wgq_import`，同时传 tracking 路径、货代和普货/快件。
5. 按返回结果处理：
   - `generated`：拿到 artifacts，向用户汇报。
   - `input_problems`：主智能体先判断是否属于"抽取器冲突"——若是，重新读取返回的 source artifact，调用 `save_philips_wgq_extraction` 保存 extractor C，或基于 `problems` 里的 `location` 形成 `decisions`，再把 A/B/C 与 decisions 显式传回 `generate_philips_wgq_import`；其余问题向用户复述 `problems`（source/location/issue/action）并结束当前 run。
6. C 使用 `extractor="philips-wgq-extractor-c"`，合同与 A/B 完全一致；不要手写或修改 extraction JSON。
7. 裁决只提交 `conflict_id/value/reason`，不得复制 canonical 或完整 extraction。
8. 不保存流程游标；下一 run 必须重新给出所有文件路径和选择。

## 失败处理

- 业务问题统一为 `{"code":"input_problems","problems":[{"source","location","issue","action"}]}`；存在问题就结束 run，用户修正材料后重新发起。
- 不暂停、不恢复、不跨 run 续接业务状态。

## 禁止

- 不扫描 `/artifacts/`、session 或历史消息来猜路径。
- 不把 null 当反对票，不修改或覆盖任何 JSON/Excel artifact。
- 不把 raw extraction、解析正文或兼容字段直接传给 generator（必须经 `generate_philips_wgq_import` 内部 canonical 构建）。
