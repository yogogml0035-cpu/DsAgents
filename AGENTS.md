# AGENTS — DsAgents

DsAgents 是一个 **agent 运行时底座**：把能力（Brain、执行器、工具）做成可插拔，而不被硬编码到某个 runner、容器、模型或工作流。当前为单子项目仓库，唯一产品子项目是后端模块 `backend/`（扁平顶层模块，绝对导入 `from hands import ...`）。

## 关键约定

- **包管理器**：`uv`（`cd backend && uv sync`）。`backend/` 是可安装包 `dsagents`，非常规 Python 包（无 `__init__.py`，**无** `python -m backend.*`）。
- **导入入口**：`from session import run_session`（需在 `backend/` 下或把 `backend/` 加入 `PYTHONPATH`）。
- **能力可插拔**：Brain / 执行器 / 工具可插拔；Session 是 append-only 事件源（不是上下文窗口）；运行时保持薄；真实错误透传；优先删减范围而非增加旋钮。
- **文档分层**：根级只放导航与全局原则；实现细节留在更下层。改代码后同步事实层。
- **文档语言**：简体中文（保留代码标识符 / 路径 / 命令 / 配置键 / IP/端口原文）；不外泄密钥；证据不足标注"需确认"。

## 自检

```bash
python backend/self_check.py        # FakeBrain，结尾打印 self-check passed
```

## 详细文档

- [项目总览（定位 / 技术栈 / 文档分层 / 运行时规则）](docs/project-overview.md)
- [核心原则与维护规则](docs/conventions.md)
- [命令与入口](docs/commands.md)
- [按任务分类的阅读顺序 + 当前里程碑](docs/reading-order.md)
- [backend 架构与约定（根级视角）](docs/backend.md)

## 系统级文档

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — 系统边界、子系统职责、理解路径、稳定目录职责
- [`INTERFACES.md`](INTERFACES.md) — 已确认接口边界、未证实跨系统关系、provider 边界、可扩展集成入口
- [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) — 子项目职责表、完整调用链、provider 边界、按任务阅读指南、集成风险清单

## 子项目事实

- [`backend/.planning/codebase/`](backend/.planning/codebase/) — backend 内部架构事实（ARCHITECTURE / STRUCTURE / STACK / INTEGRATIONS / CONVENTIONS / TESTING / CONCERNS），是 backend 实现细节的**事实来源**。

> 改 backend 代码后，先更新 `backend/.planning/codebase/` 对应文档，再视影响回看 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`。
