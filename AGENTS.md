# AGENTS.md

DsAgents Agent Harness：保持纤薄的 Agent 运行时基座，把 `Session`/`Harness`/`Hands`/`Resources`/`Tools` 稳定为五个模块边界；DeepAgents 是可插拔 `Brain`。本文件只做入口与导航，规则与细节见下方详情文档。

## 关键约定

- **包管理**：`pip` + 根级 `requirements.txt`。
- **离线自检**（必须用 `-m`）：`python -m backend.self_check`。当前无 pytest 套件。
- **真实会话**：`python -m backend "<message>" --session-id <可选>`。
- **文档型变更校验**：`git diff --check`。

## 详细文档

- [Harness 原则与运行时规则](docs/harness-rules.md)（五边界、Simplicity Constraint、MinerU 固定参数、日志护栏）
- [系统级架构图](ARCHITECTURE.md)
- [接口边界与调用关系](INTERFACES.md)
- [跨子项目系统地图](coding_maps/SYSTEM_MAP.md)
- backend 实现事实：`backend/.planning/codebase/`
