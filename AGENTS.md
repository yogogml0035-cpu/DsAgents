# AGENTS — DsAgents

该文件为 AI 编码代理的入口：只放**全局硬约束**与**文档导航**。实现事实在 `backend/.planning/codebase/`；系统边界在 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`。

## 项目概览

DsAgents 是**单子项目** agent 运行时底座：产品代码在 `backend/`，发行名 `dsagents`，以可注入 Brain、执行器、工具和资源承载通用运行与 Philips / Tecan 内置 Skill。**无前端子项目**。

## 技术栈

| 技术 | 用途 |
|------|------|
| Python `>=3.11` + **`uv`** | 运行时与包管理（以 `backend/uv.lock` 为准） |
| FastAPI / uvicorn | 四 HTTP 端点（轮询，无 SSE） |
| DeepAgents / LangGraph | Brain 与 checkpointer / store |
| SQLite（三库） | run ledger / checkpoints / store |
| MinerU / openpyxl / 可选 oracledb | PDF 解析、XLSX 输入读取、WGQ / DK 共享 12NC 主数据 |

## 命令与验证

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

- 包管理器用 **`uv`**；不要用 `pip install -e .` 绕过 `uv.lock`。
- 测试为可执行 assert 脚本（`python -m tests.<name>`，**非 pytest**）；真实模型 / MinerU / Oracle / 外部 HTTP 与本地回归分开。
- 完整命令、真实集成开关与启动方式见 [docs/commands.md](docs/commands.md)。

## 结构（形状）

产品源码仅在 `backend/`：HTTP 适配、`runtime/`（Brain / 执行 / ledger / middleware / 工具目录）、`integrations/`（artifacts / MinerU）、`skills/`（共享渠道合同 + Philips / Tecan 下划线包）。**不要**把 setuptools 构建产物当源码。权威目录树见 codebase `STRUCTURE.md`。

## 全局硬约束

- **run-first**：run 是唯一执行与查询单位；`run_events` append-only，`runs` 为投影快照；`session_id` 只作 LangGraph `thread_id` 与进程内单飞锁。
- HTTP 仅四端点：`POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`（无 SSE / session API）。程序内入口：`AgentResources` + `create_harness(...).execute_run(...)`。
- HTTP workflow 仅 `WGQ`（飞利浦外高桥）与 `DK`（帝肯境外供应链）。两者均以各自 schema 的 `ToolStrategy` 产生 `structured_response`，经共享 runtime finalizer 写入 `run.result`（`input_problems` 时 run 仍 `succeeded`）；工具静态 **5** 个；生产不配置业务 SubAgent。
- workflow 收窄 `tools` 必须用 **denylist** 排除**其他业务**工具：WGQ / DK 均排除 Tecan finalizer，均保留共享 MinerU、12NC 主数据查询与 XLSX 检查器；Tecan finalizer 仅供无 workflow 的明确 Tecan 请求兼容，禁止业务-only allowlist。验证：`python -m tests.test_workflow_setup`。
- **Skill 单目录**：每个业务 Skill 用一个下划线命名、可 import 的 Python 包；包内同时包含 `SKILL.md` / 按需 references、schema 与 scripts。运行时以同一目录的**连字符**虚拟路径 `/skills/<kebab-case>/` 挂载（须与 `SKILL.md` frontmatter `name` 一致）。新增须更新 `package-data` 与 skills 路由。Tecan 不携带 Excel 模板或生成器。
- `typing.Protocol` **只**用于 `Brain` / `BrainFactory`；工具用 callable + `ToolCatalog`；资源与 ledger 用具体类。
- 事件固定 **7** 类；业务问题统一 `input_problems`；不要重新引入 session API、SSE 或旧顶层辅助模块。
- **`StructuredOutputRecovery`**（workflow 共用）：`can_jump_to` 必须含 `"end"`；耗尽时显式 `jump_to: "end"`，禁止只返回 `None`。WGQ / DK 传入各自 schema；空 data 壳耗尽时生成当前 schema 的完整 all-null `input_problems` + runtime problem；普通 run 使用 `structured_schema=None`。验证：`python -m tests.test_harness`。完整算法见 [docs/conventions.md](docs/conventions.md)。
- **渠道终态合同**：Philips / Tecan header 各自独立，`items[]` 共用完整 24 字段；未知值为 `null`，不输出 `shipment`、Excel、候选噪声或审计细节。同票归集在单一 run 完成，不新增消息/任务状态表或业务 middleware。
- OMS 旁路索引 best-effort、不阻塞已创建 run（非 `run_events`、无查询 API）；时间戳统一 UTC+8 本地 `YYYY-MM-DD HH:MM:SS`（ledger 与 OMS）。
- 部署按**单进程**假设：`session_id` 锁与 cancel 仅进程内有效，不要假设多 worker 互斥。
- 改 backend 代码后先同步子项目 codebase 事实文档，再按影响更新根级系统文档与系统地图；文档变更至少 `git diff --check`。
- 长期文档用简体中文；保留标识符、路径、命令、配置键、API 名；不写密钥 / `.env` 值 / 私有连接串。
- Windows checkout 随仓库提供 `backend/.oracle/instantclient/instantclient_19_31`，未设置 `ORACLE_CLIENT_LIB_DIR` 时用作 Oracle thick mode 默认路径；显式配置可覆盖它，缺客户端或连接配置时优雅降级（见 backend 风险文档）。

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
| backend 实现事实（Analysis Date: 2026-07-24） | [backend/.planning/codebase/](backend/.planning/codebase/) |

修改 backend 前先读 `docs/conventions.md`，再按任务读 codebase 事实文档；涉及 HTTP 或跨边界时回看 `INTERFACES.md` 与 `SYSTEM_MAP.md`。
