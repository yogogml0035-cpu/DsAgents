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

最小可运行 DeepAgents 解析演示已交付（通用文档解析工具 / DeepAgents 工厂 / CompositeBackend / run-first 执行核心 / 薄 HTTP run/upload 适配层）。HTTP 层为 **run 中心模型**：`POST /upload` 保存一个或多个文件并返回 `/artifacts/uploads/...` 路径；`POST /runs` 只接收 `messages[]`，每条消息 `content` 是 `text` / `artifact` blocks；`GET /runs/{run_id}` 负责轮询（支持 `after_event_id` 增量，返回 `latest_content_event`）。`artifact` block 是项目 API 语义，进入 Brain 前会被转成文本路径提示。当前**无 SSE**，事件靠轮询；`values` snapshot 只保留在 raw 中，公开事件使用 `tool_call` / `tool_result` / `assistant_message` 等规范化类型。旧 session 模块/表/端点已在 commit `8890292` 移除。实现状态详见 `backend/.planning/codebase/ARCHITECTURE.md`。
