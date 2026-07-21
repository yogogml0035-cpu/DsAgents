# Philips 外高桥识别说明（已迁移）

本文件保留路径兼容性。自 2026-07-22 起，Philips 与 Tecan 共用的业务验收口径、24 字段商品合同和终态语义统一维护在 [渠道供应链业务抽取 JSON 合同](channel-supply-chain-json-prd.md)。

Philips 专属实现请同时阅读：

- `backend/skills/philips-wgq-inbound-recognition/SKILL.md`
- `backend/skills/philipswgqinboundrecognition/schema.py`
- `backend/skills/philipswgqinboundrecognition/scripts/tools.py`

仍然适用的 Philips 边界：唯一 HTTP workflow 为 `philips_wgq_inbound_recognition`，最终 JSON 只经 `run.result` 交付；不生成 Excel，不输出 `shipment`，同票 PDF/XLSX 由 Skill 动态归集。
