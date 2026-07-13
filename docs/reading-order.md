# 按任务分类的阅读顺序

> 根级 AGENTS.md 的详情文档之一。每个任务先读对应事实文档（路径相对仓库根），再到系统层定位边界。

## 任务阅读指南

| 任务类型 | 先读 |
|----------|------|
| **改 backend 代码（业务/存储/runner）** | `backend/.planning/codebase/ARCHITECTURE.md`、`STRUCTURE.md`；改持久化回看 `docs/conventions.md` 的 run-first 原则 |
| **改文档解析工具 / DeepAgents Brain** | `backend/.planning/codebase/INTEGRATIONS.md`、`STACK.md`；provider 边界见 `INTERFACES.md` §6 |
| **改 Philips/Tecan Skill 或 Excel** | 对应 `backend/dsagents/skills/*/SKILL.md` 与 `references/`、对应 `scripts/tools.py` / `scripts/documents.py`、`backend/.planning/codebase/INTEGRATIONS.md` §7、对应业务测试 |
| **改集成 / Provider** | `INTERFACES.md`、`backend/.planning/codebase/INTEGRATIONS.md`；未证实关系见 `INTERFACES.md` §7 |
| **加新子项目（如 frontend）** | `docs/conventions.md`（核心原则）、`ARCHITECTURE.md` §1、`coding_maps/SYSTEM_MAP.md` §4/§6 |

## 当前里程碑

run-first DeepAgents runtime 已交付通用文档解析与 Philips/Tecan 技能化 artifact 工作流。HTTP 当前为 upload、run、poll、cancel 四类端点；业务 A/B/C、裁决、canonical 和 Excel 不增加接口或数据库状态。subagent 模型 token 被隔离，task 事件仍保留。实现状态详见 `backend/.planning/codebase/ARCHITECTURE.md`。
