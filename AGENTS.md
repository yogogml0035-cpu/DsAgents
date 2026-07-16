# AGENTS — DsAgents

DsAgents 是**单子项目**的 agent 运行时底座：产品代码位于 `backend/`，发行名 `dsagents`，通过可注入 Brain、执行器、工具和资源承载通用运行与 Philips/Tecan 内置 Skill。

## 关键约定

- 包管理器使用 `uv`，先执行 `cd backend && uv sync`；不要用 `pip install -e .` 绕过 `uv.lock`。
- run 是唯一执行与查询单位；`run_events` 是 append-only 事件源，`runs` 是投影快照；`session_id` 只用于 LangGraph `thread_id` 和进程内单飞锁。
- HTTP 入口仅四端点：`POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`（无 SSE / session API）；程序内入口是 `AgentResources` + `create_harness(...).execute_run(...)`。
- 唯一固定 workflow 为 `philips_wgq_inbound_recognition`，业务 JSON 走 `run.result`（`input_problems` 时 run 仍 `succeeded`）；工具静态 5 个；Tecan 保留 2 个 SubAgent；middleware 集中在 runtime middleware 模块（主 Agent 含 memory 共约 5 个，SubAgent 各 4 个）。
- 当为 workflow 收窄 `tools` 时，必须用 **denylist** 排除**其他业务**工具（如帝肯），保留共享 MinerU 工具 `parse_documents` / `extract_archives` 与本业务主数据工具，并与共享操作手册、对应 Skill 一致；禁止只 allowlist 业务工具导致手册里的通用工具从模型工具表消失。用 `python -m tests.test_workflow_setup` 验证 Philips 工具名集合含 `extract_archives`、不含帝肯工具。
- `typing.Protocol` 只用于可注入的 `Brain` / `BrainFactory` 边界；工具使用 callable + `ToolCatalog`，资源与 ledger 使用具体类。
- 工具静态注册、事件固定 7 类、业务问题统一 `input_problems`；不要重新引入已删除的 session API、SSE 或旧顶层辅助模块。
- 当实现 `after_model` + `jump_to: "model"` 的有界重试（`StructuredOutputRecovery`，含空 data 壳纠错）时，必须同时声明 `can_jump_to` 含 `"end"`，并在达到 `max_retries` 或无法产出 `structured_response` 时显式 `jump_to: "end"`；禁止只返回 `None` 依赖默认边退出——在仅有 `ToolStrategy`、无业务 tool 的图上会触发 model↔model 无限循环。用 `cd backend && python -m tests.test_harness` 验证重试次数封顶。
- backend 代码改动后先同步子项目 codebase 事实文档，再按影响更新根级系统文档与系统地图；文档变更至少运行 `git diff --check`。
- 测试采用可执行 assert 脚本（`cd backend && python -m tests.<name>`，**非 pytest**）；真实模型、MinerU、Oracle 或外部 HTTP 测试必须与普通本地回归分开。
- 长期文档使用简体中文，保留代码标识符、路径、命令、配置键和 API 名称；不写密钥、本地 `.env` 值或私有连接串。
- Oracle thick mode 依赖外部 `ORACLE_CLIENT_LIB_DIR`；缺失时按文档定义优雅降级，部署前提见 backend 风险文档。

## 详细文档

- 系统定位与理解路径：[ARCHITECTURE.md](ARCHITECTURE.md)
- 接口、provider、存储与 artifacts 边界：[INTERFACES.md](INTERFACES.md)
- 系统级调用链与任务阅读指南：[coding_maps/SYSTEM_MAP.md](coding_maps/SYSTEM_MAP.md)
- 全局原则与维护规则：[docs/conventions.md](docs/conventions.md)
- 命令、本地门禁与真实集成入口：[docs/commands.md](docs/commands.md)
- 按任务分类的阅读顺序：[docs/reading-order.md](docs/reading-order.md)
- backend 概览：[docs/backend.md](docs/backend.md)
- 项目总览与源码入口：[docs/project-overview.md](docs/project-overview.md)
- backend 实现事实（7 份 fact docs，Analysis Date: 2026-07-16）：[backend/.planning/codebase/](backend/.planning/codebase/)

修改 backend 前先读 `docs/conventions.md`，再按任务读取 backend codebase 事实文档；涉及 HTTP 或跨边界行为时回看 `INTERFACES.md` 与 `coding_maps/SYSTEM_MAP.md`。
