# AGENTS — DsAgents

DsAgents 是一个 **agent 运行时底座**：把能力（Brain、执行器、工具）做成可插拔，而不被硬编码到某个 runner、容器、模型或工作流。当前为单子项目仓库，唯一产品子项目是后端模块 `backend/`（Python 包 `dsagents/`，绝对导入 `from dsagents.runtime import ...`）。Philips/Tecan 业务代码归属各自内置 Skill 包（`dsagents/skills/philipswgqimport`、`dsagents/skills/tecanimport`），不再有扁平业务模块或独立 `capabilities/`。

> 导航入口，不堆积实现细节；底层事实以 `backend/.planning/codebase/` 为准。

## 关键约定

- **包管理器**：`uv`（**非 pip**）。安装：`cd backend && uv sync`；禁止 `pip install -e .` 绕过 `uv.lock`。
- **技术栈定位**：Python / FastAPI / DeepAgents / LangGraph / SQLite；完整依赖、配置键和 provider 边界不要塞进本文件，先看 `backend/.planning/codebase/STACK.md` 与 `INTEGRATIONS.md`。
- **外部部署依赖**：Philips 法定单位查询走 `oracledb` thick mode，需 `ORACLE_CLIENT_LIB_DIR` 指向外部 Oracle instant client 目录（仓库不存放，生产须镜像挂载/主机预装）；缺失则优雅降级，详见 `backend/.planning/codebase/CONCERNS.md` §8。
- **run-first 架构**：run 是唯一执行/查询单位；`run_events` append-only，`runs` 是投影快照；`session_id` 只作 LangGraph `thread_id` 和进程内单飞锁键，不再是一等持久化对象；新 schema 使用 UTC ISO-8601 毫秒时间，部署切换清空整个 `backend/data/`、不迁移。
- **运行入口**：HTTP 用 `POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel` 与 `POST /upload`，启动 `uvicorn dsagents.api:app`；程序内用 `AgentResources` + `create_harness(resources).execute_run(messages, session_id, run_id)`；没有 `from session import run_session` 或 `python -m backend.*`。
- **Protocol 使用边界**：`typing.Protocol` 只用于可注入能力边界（`Brain` / `BrainFactory`）；工具保持 callable + `ToolCatalog`，资源 / ledger 保持具体类；不要为单实现小功能新增 Protocol/ABC。
- **验证入口**：仅文档变更至少跑 `git diff --check`；backend 代码变更按影响范围直接跑对应测试脚本，例如 `cd backend && python -m tests.test_api`。没有总控自检脚本。
- **测试目录**：backend 测试放 `backend/tests/`，脚本以 `test_*.py` 命名，可执行脚本保留 `run()` 入口；共享替身/工具放 `test_support.py`。
- **真实集成测试**：会触达真实 HTTP 服务、模型、MinerU 或外部网络的脚本必须显式命名/标注，并默认与普通本地脚本分开运行；不要把真实调用混进普通回归脚本。
- **文档语言**：简体中文（保留代码标识符 / 路径 / 命令 / 配置键 / IP/端口原文）；密钥与本地 `.env` 值不写进文档；证据不足标注"需确认"。

> 核心运行时原则（能力可插拔、run 是事件源、保持运行时薄、真实错误透传、优先删减范围）与文档维护规则见 [`docs/conventions.md`](docs/conventions.md)，每次改动 backend 前请先读。

## 按需文档入口

- [项目总览](docs/project-overview.md) — 定位 / 技术栈 / 文档分层 / 运行时规则
- [核心原则与维护规则](docs/conventions.md) — 全局人工约束（改动前必读）
- [命令与入口](docs/commands.md) — 开发 / 测试 / HTTP / 导入调用
- [按任务分类的阅读顺序](docs/reading-order.md) — 任务类型 → 应先读哪些文档
- [backend 架构与约定](docs/backend.md) — 根级视角的 backend 摘要

## 系统级文档入口

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — 系统边界、子系统职责、理解路径、稳定目录职责
- [`INTERFACES.md`](INTERFACES.md) — 已确认接口边界、未证实跨系统关系、provider 边界、可扩展集成入口
- [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) — 子项目职责表、完整调用链、provider 边界、按任务阅读指南、集成风险清单

## 子项目事实

- [`backend/.planning/codebase/`](backend/.planning/codebase/) — backend 内部架构事实（ARCHITECTURE / STRUCTURE / STACK / INTEGRATIONS / CONVENTIONS / TESTING / CONCERNS），是 backend 实现细节的**事实来源**；改 backend 代码后先更新它，再视影响回看 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`。
