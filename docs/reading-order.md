# 按任务分类的阅读顺序

> 根级 AGENTS.md 的详情文档之一。每个任务先读对应事实文档（路径相对仓库根），再到系统层定位边界。

## 任务阅读指南

| 任务类型 | 先读 |
|----------|------|
| **改 backend 代码（业务/存储/runner）** | `backend/.planning/codebase/ARCHITECTURE.md`、`STRUCTURE.md`；改持久化回看 `docs/conventions.md` 的 run-first 原则 |
| **改 HTTP 契约 / cancel / usage** | `INTERFACES.md` §1–§2、`backend/.planning/codebase/INTEGRATIONS.md`（APIs 与 Data Storage）、`backend/api.py`；验证 `python -m tests.test_api` |
| **改文档解析工具 / DeepAgents Brain** | `backend/.planning/codebase/INTEGRATIONS.md`、`STACK.md`；provider 边界见 `INTERFACES.md` §5.4 |
| **改 Philips 外高桥识别** | `docs/philips-wgq-inbound-recognition-prd.md`、`backend/skills/philipswgqinboundrecognition/{SKILL.md,schema.py,scripts/tools.py}`、`backend/.planning/codebase/INTEGRATIONS.md`、`tests/test_philips_wgq_inbound_recognition.py`；工具表与 denylist 另验 `tests/test_workflow_setup.py` |
| **改 workflow 工具收窄 / structured recovery** | `backend/runtime/agent.py`、`backend/runtime/middleware.py`、`docs/conventions.md`（denylist、jump_to、空壳 skeleton）、`INTERFACES.md` §4；验证 `python -m tests.test_workflow_setup` 与 `python -m tests.test_harness`（含空壳耗尽 → `partial_success` skeleton） |
| **改 Tecan Skill / Excel** | `backend/skills/tecanimport/SKILL.md` 与 `references/`、`scripts/tools.py` / `scripts/documents.py`、对应 assets 与 `tests/test_tecan_import.py` |
| **改集成 / Provider** | `INTERFACES.md` §5、`backend/.planning/codebase/INTEGRATIONS.md`；未证实关系见 `INTERFACES.md` §7 |
| **改事件 schema / stream 规范化** | `backend/.planning/codebase/ARCHITECTURE.md`（Data Flow）、`runtime/execution.py`、`runtime/observability.py`；对照 `INTERFACES.md` §1 的 7 类事件 |
| **加新子项目（如 frontend）** | `docs/conventions.md`（核心原则）、`ARCHITECTURE.md` §1–§2、`coding_maps/SYSTEM_MAP.md` §1/§2/§6/§7 |
| **排查风险 / 部署前提** | `ARCHITECTURE.md` §7、`coding_maps/SYSTEM_MAP.md` §8、`backend/.planning/codebase/CONCERNS.md` |

## 当前里程碑

run-first DeepAgents runtime 已交付通用文档解析、Philips 外高桥结构化识别与 Tecan artifact/Excel 工作流。HTTP 仍为 upload、run、poll、cancel 四类端点；Philips 通过固定 workflow 和 `run.result` 返回业务 JSON，无 A/B/C、Excel 或额外状态机；Tecan 保留原有 SubAgent；OMS 旁路索引与 UTC+8 时间戳已对齐。实现状态详见 `backend/.planning/codebase/ARCHITECTURE.md`（Analysis Date: 2026-07-17，`last_mapped_commit` d012362）。
