# 按任务分类的阅读顺序

> 根级 AGENTS.md 的详情文档之一。每个任务先读对应事实文档（路径相对仓库根），再到系统层定位边界。

## 任务阅读指南

| 任务类型 | 先读 |
|----------|------|
| **改 backend 代码（业务/存储/runner）** | `backend/.planning/codebase/ARCHITECTURE.md`、`STRUCTURE.md`；改持久化回看 `docs/conventions.md` 的 run-first 原则 |
| **改文档解析工具 / DeepAgents Brain** | `backend/.planning/codebase/INTEGRATIONS.md`、`STACK.md`；provider 边界见 `INTERFACES.md` §1 |
| **改集成 / Provider** | `INTERFACES.md`、`backend/.planning/codebase/INTEGRATIONS.md`；未证实关系见 `INTERFACES.md` §2 |
| **加新子项目（如 frontend）** | `docs/conventions.md`（核心原则）、`ARCHITECTURE.md` §1、`coding_maps/SYSTEM_MAP.md` §4/§6 |

## 当前里程碑

最小可运行 DeepAgents 解析演示已交付（通用文档解析工具 / DeepAgents 工厂 / CompositeBackend / run-first 执行核心 / 薄 HTTP run/upload 适配层）。HTTP 层为 **run 中心模型**：`POST /runs` 创建 `run_id`、写 `runs`/`run_events`，并提供 `GET /runs/{run_id}` 轮询（支持 `after_event_id` 增量，返回 `latest_content_event`）与 `POST /files` 上传。当前**无 SSE**，事件靠轮询。旧 session 模块/表/端点已在 commit `8890292` 移除。实现状态详见 `backend/.planning/codebase/ARCHITECTURE.md`。
