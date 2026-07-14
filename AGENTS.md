# AGENTS — DsAgents

DsAgents 是单子项目的 agent 运行时底座：产品代码位于 `backend/`，发行名仍为 `dsagents`，源码顶层为 `api.py`、`runtime/`、`integrations/`、`skills/`，通过可注入 Brain、执行器、工具和资源承载通用运行与 Philips/Tecan 内置 Skill。

## 关键约定

- 包管理器使用 `uv`，先执行 `cd backend && uv sync`；不要用 `pip install -e .` 绕过 `uv.lock`。
- run 是唯一执行与查询单位；`run_events` 是 append-only 事件源，`runs` 是投影快照；`session_id` 只用于 LangGraph `thread_id` 和进程内单飞锁。
- HTTP 入口是 `POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`；程序内入口是 `AgentResources` + `create_harness(...).execute_run(...)`。
- `typing.Protocol` 只用于可注入的 `Brain` / `BrainFactory` 边界；工具使用 callable + `ToolCatalog`，资源与 ledger 使用具体类。
- 工具静态注册、事件固定 7 类、业务问题统一 `input_problems`；不要重新引入已删除的 session API、SSE 或旧顶层辅助模块。
- backend 代码改动后先同步 `backend/.planning/codebase/`，再按影响更新根级系统文档与 `coding_maps/SYSTEM_MAP.md`；文档变更至少运行 `git diff --check`。
- 测试采用可执行 assert 脚本（`cd backend && python -m tests.<name>`，非 pytest）；真实模型、MinerU、Oracle 或外部 HTTP 测试必须与普通本地回归分开。
- 长期文档使用简体中文，保留代码标识符、路径、命令、配置键和 API 名称；不写密钥、本地 `.env` 值或私有连接串。
- Oracle thick mode 依赖外部 `ORACLE_CLIENT_LIB_DIR`；缺失时按文档定义优雅降级，部署前提见 backend 风险文档。

## 文档分层与阅读入口

- 系统定位与理解路径：[ARCHITECTURE.md](ARCHITECTURE.md)
- 接口、provider、存储与 artifacts 边界：[INTERFACES.md](INTERFACES.md)
- 系统级调用链与任务阅读指南：[coding_maps/SYSTEM_MAP.md](coding_maps/SYSTEM_MAP.md)
- 全局原则、命令、任务阅读顺序：[docs/conventions.md](docs/conventions.md)、[docs/commands.md](docs/commands.md)、[docs/reading-order.md](docs/reading-order.md)
- backend 概览与事实来源：[docs/backend.md](docs/backend.md)、[backend/.planning/codebase/](backend/.planning/codebase/)（7 份 fact docs，Analysis Date: 2026-07-15）

修改 backend 前先读 `docs/conventions.md`，再按任务读取 `backend/.planning/codebase/` 对应文档；涉及 HTTP 或跨边界行为时回看 `INTERFACES.md` 与 `coding_maps/SYSTEM_MAP.md`。
