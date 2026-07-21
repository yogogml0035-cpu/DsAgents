# AGENTS — DsAgents

该文件为 AI 编码代理的入口：只放**全局硬约束**与**文档导航**。实现事实在 `backend/.planning/codebase/`；系统边界在 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`。

## 项目概览

DsAgents 是**单子项目** agent 运行时底座：产品代码在 `backend/`，发行名 `dsagents`，以可注入 Brain、执行器、工具和资源承载通用运行与 Philips / Tecan 内置 Skill。**无前端子项目**。

## 技术栈（摘要）

| 技术 | 用途 |
|------|------|
| Python `>=3.11` + **`uv`** | 运行时与包管理（以 `backend/uv.lock` 为准） |
| FastAPI / uvicorn | 四 HTTP 端点（轮询，无 SSE） |
| DeepAgents / LangGraph | Brain 与 checkpointer / store |
| SQLite（三库） | run ledger / checkpoints / store |
| MinerU / openpyxl / 可选 oracledb | PDF 解析、XLSX 输入读取、Philips 主数据 |

## 关键约定

- 包管理器使用 **`uv`**（`cd backend && uv sync`）；不要用 `pip install -e .` 绕过 `uv.lock`。
- **run-first**：run 是唯一执行与查询单位；`run_events` append-only，`runs` 为投影快照；`session_id` 只作 LangGraph `thread_id` 与进程内单飞锁。
- HTTP 仅四端点：`POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`（无 SSE / session API）。程序内入口：`AgentResources` + `create_harness(...).execute_run(...)`。
- 唯一固定 workflow：`philips_wgq_inbound_recognition`；Philips 与明确请求的 Tecan Skill 均将最终业务 JSON 写入 `run.result`（`input_problems` 时 run 仍 `succeeded`）。工具静态 **5** 个：MinerU 2 个、Philips 主数据 1 个、共享 XLSX 检查 1 个、Tecan finalizer 1 个；生产不配置业务 SubAgent。middleware 集中在 runtime middleware 模块，主 Agent 有 memory 时约 5 个。
- workflow 收窄 `tools` 必须用 **denylist** 排除**其他业务**工具（当前为 Tecan finalizer），保留共享 MinerU（`parse_documents` / `extract_archives`）、共享 XLSX 检查器与本业务主数据工具；禁止业务-only allowlist。验证：`python -m tests.test_workflow_setup`。
- **Skill 成对目录**：kebab-case 资源目录（`SKILL.md` / 按需 references，挂载 `/skills/`）+ 可 import 的 Python 包；新增 Skill 须两套目录并更新 `package-data`。Tecan 不再携带 Excel 模板或生成器。
- `typing.Protocol` **只**用于 `Brain` / `BrainFactory`；工具用 callable + `ToolCatalog`；资源与 ledger 用具体类。
- 事件固定 **7** 类；业务问题统一 `input_problems`；不要重新引入 session API、SSE 或旧顶层辅助模块。
- **`StructuredOutputRecovery`**（Philips 专用 `after_model` + `jump_to: "model"`）：`can_jump_to` 必须含 `"end"`；耗尽时显式 `jump_to: "end"`，禁止只返回 `None`。空 data 壳按同回合 `tool_call_id` 恢复或 skeleton 纠错；空壳耗尽 → all-null + `partial_success`；其它失败耗尽 → 无 `structured_response`。普通/Tecan run 使用 `structured_schema=None`，不强制按 Philips schema 恢复；Tecan 以 finalizer 工具校验终态。验证：`python -m tests.test_harness`。完整算法见 [docs/conventions.md](docs/conventions.md)。
- **渠道终态合同**：Philips / Tecan header 各自独立，`items[]` 共用完整 24 字段；未知值为 `null`，不输出 `shipment`、Excel、候选噪声或审计细节。业务同票归集在单一 run 完成，不新增消息/任务状态表或业务 middleware。
- OMS 旁路索引 best-effort、不阻塞已创建 run（非 `run_events`、无查询 API）；时间戳统一 UTC+8 本地 `YYYY-MM-DD HH:MM:SS`（ledger 与 OMS）。
- 测试为可执行 assert 脚本（`cd backend && python -m tests.<name>`，**非 pytest**）；真实模型 / MinerU / Oracle / 外部 HTTP 与本地回归分开。
- 权威源码是 `backend/` 顶层 `api.py` + `runtime/` + `integrations/` + `skills/`；**不要**把 setuptools 构建产物（历史 `backend/build/`）当源码或读进 VCS。
- 改 backend 代码后先同步子项目 codebase 事实文档，再按影响更新根级系统文档与系统地图；文档变更至少 `git diff --check`。
- 长期文档用简体中文；保留标识符、路径、命令、配置键、API 名；不写密钥 / `.env` 值 / 私有连接串。
- Oracle thick mode 依赖 `ORACLE_CLIENT_LIB_DIR`；缺失时优雅降级（见 backend 风险文档）。

## 命令与验证（摘要）

```powershell
cd backend
uv sync
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
# 文档：仓库根目录 git diff --check
```

完整命令、真实集成开关与启动方式见 [docs/commands.md](docs/commands.md)。

## 详细文档

| 主题 | 文档 |
|------|------|
| 系统定位与理解路径 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 接口 / provider / 存储 / artifacts / OMS | [INTERFACES.md](INTERFACES.md) |
| 调用链与任务阅读指南 | [coding_maps/SYSTEM_MAP.md](coding_maps/SYSTEM_MAP.md) |
| 全局原则与维护规则 | [docs/conventions.md](docs/conventions.md) |
| 命令与门禁 | [docs/commands.md](docs/commands.md) |
| 渠道供应链 JSON 业务合同 | [docs/channel-supply-chain-json-prd.md](docs/channel-supply-chain-json-prd.md) |
| 按任务阅读顺序 | [docs/reading-order.md](docs/reading-order.md) |
| backend 概览 | [docs/backend.md](docs/backend.md) |
| 项目总览与源码入口 | [docs/project-overview.md](docs/project-overview.md) |
| backend 实现事实（Analysis Date: 2026-07-22） | [backend/.planning/codebase/](backend/.planning/codebase/) |

修改 backend 前先读 `docs/conventions.md`，再按任务读 codebase 事实文档；涉及 HTTP 或跨边界时回看 `INTERFACES.md` 与 `SYSTEM_MAP.md`。
