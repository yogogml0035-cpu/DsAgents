---
title: backend 目录结构事实
last_mapped_commit: 555bca7
analysis_date: 2026-07-20
focus: arch
---

# STRUCTURE — backend（dsagents）

> 分析日期：2026-07-20
> 映射提交：`555bca7`
> 范围：`backend/` 源码与包结构（跳过 venv、artifacts 大文件、egg-info 内容展开）

## Directory Layout

```
backend/
├── api.py                          # FastAPI 入口：四端点 + create_app
├── pyproject.toml                  # 发行名 dsagents、package-data、依赖
├── uv.lock
│
├── runtime/                        # 运行时核心包
│   ├── __init__.py                 # 对外稳定导出
│   ├── agent.py                    # Brain Protocol、DeepAgentsBrainFactory、SubAgent
│   ├── execution.py                # HarnessRuntime、create_harness
│   ├── middleware.py               # ToolTelemetry / NoProgress / StructuredOutput* / Memory
│   ├── observability.py            # stream chunk → 事件 payload（无 I/O）
│   ├── oms_log.py                  # OMS run_created JSONL 旁路
│   ├── resources.py                # ResourceConfig、AgentResources、CompositeBackend
│   ├── runs.py                     # SqliteRunLedger、RunEvent、RunSnapshot
│   └── tools.py                    # ToolCatalog、default_tool_catalog
│
├── integrations/                   # 外部集成（与业务 Skill 解耦）
│   ├── __init__.py
│   ├── artifacts.py                # 路径虚拟化、上传命名、JSON artifact I/O
│   └── mineru.py                   # parse_documents / extract_archives
│
├── skills/                         # Skill 成对目录根
│   ├── __init__.py
│   ├── philips-wgq-inbound-recognition/    # kebab 资源（挂载 /skills/）
│   │   └── SKILL.md
│   ├── philipswgqinboundrecognition/       # 可 import Python 包
│   │   ├── __init__.py                     # WORKFLOW、PhilipsWgqRecognitionResult
│   │   ├── schema.py                       # 结构化结果契约
│   │   └── scripts/
│   │       ├── __init__.py
│   │       └── tools.py                    # lookup_philips_wgq_master_data
│   ├── tecan-import/                       # kebab 资源
│   │   ├── SKILL.md
│   │   ├── references/
│   │   │   ├── fields.md
│   │   │   └── rules.md
│   │   └── assets/
│   │       └── Tecan_进口_发票箱单_空运.xlsx
│   └── tecanimport/                        # 可 import Python 包
│       ├── __init__.py
│       └── scripts/
│           ├── __init__.py
│           ├── tools.py                    # save_tecan_extraction / generate_tecan_import
│           └── documents.py                # 发票箱单 Excel 写入
│
├── tests/                          # 可执行 assert 脚本（非 pytest）
│   ├── __init__.py
│   ├── test_support.py             # FakeBrain、HTTP 等待辅助
│   ├── test_api.py
│   ├── test_harness.py
│   ├── test_run_ledger.py
│   ├── test_tools.py
│   ├── test_workflow_setup.py
│   ├── test_philips_wgq_inbound_recognition.py
│   ├── test_tecan_import.py
│   ├── test_minimax_cache_baseline.py
│   ├── test_real_image_run.py
│   ├── test_real_multi_pdf_run.py
│   ├── test_real_philips_wgq_inbound_recognition.py
│   └── test_real_philips_wgq_ups.py
│
├── data/                           # 运行时数据（ResourceConfig 锚定，非包源码）
│   ├── dsagents_runs.db
│   ├── dsagents_store.db
│   ├── dsagents_checkpoints.db
│   ├── artifacts/
│   │   ├── uploads/
│   │   └── downloads/
│   └── internal/
│       └── run-events/             # 超大事件 blob 溢出
│
├── log/
│   └── oms_log.log                 # OMS JSONL 默认路径
│
├── .planning/
│   └── codebase/                   # 本目录：架构/结构事实文档
│
├── build/                          # setuptools 构建产物（映射时跳过）
├── dist/                           # wheel 产物（映射时跳过）
└── dsagents.egg-info/              # 元数据（映射时跳过）
```

## Directory Purposes

| 路径 | 用途 |
|------|------|
| `backend/api.py` | HTTP 边界：上传、run 生命周期、session 单飞锁、usage 展示层 |
| `backend/runtime/` | harness 执行、资源装配、ledger、middleware、工具目录、OMS 旁路 |
| `backend/integrations/` | 与厂商/存储相关的可复用集成（MinerU、artifact 路径），无业务 schema |
| `backend/skills/` | 内置 Skill：成对「资源目录 + Python 包」 |
| `backend/tests/` | 本地回归与可选真实集成；`python -m tests.<module>` |
| `backend/data/` | SQLite 与 artifacts 落盘；路径由 `ResourceConfig` 固定到 `backend/data` |
| `backend/log/` | OMS 旁路日志默认目录 |
| `backend/.planning/codebase/` | 子项目实现事实文档（本文件与 `ARCHITECTURE.md` 等） |

## Key File Locations

### 入口与配置

| 角色 | 路径 |
|------|------|
| HTTP 应用入口 | `backend/api.py` |
| 包元数据 / package-data | `backend/pyproject.toml` |
| 锁文件 | `backend/uv.lock` |
| 环境变量加载点 | `runtime/agent.py`、`integrations/mineru.py` 读取 `backend/.env`（**勿在文档记录密钥**） |
| 运行时对外 API | `backend/runtime/__init__.py` |

### Runtime 核心

| 角色 | 路径 |
|------|------|
| 执行循环 | `backend/runtime/execution.py` |
| 资源 / backend 路由 | `backend/runtime/resources.py` |
| Brain 与 workflow | `backend/runtime/agent.py` |
| Middleware / recovery | `backend/runtime/middleware.py` |
| 事件抽取 | `backend/runtime/observability.py` |
| Ledger | `backend/runtime/runs.py` |
| 工具注册表 | `backend/runtime/tools.py` |
| OMS 索引 | `backend/runtime/oms_log.py` |

### Skills（成对）

| 角色 | 路径 |
|------|------|
| Philips 资源 SKILL | `backend/skills/philips-wgq-inbound-recognition/SKILL.md` |
| Philips schema / WORKFLOW | `backend/skills/philipswgqinboundrecognition/schema.py` |
| Philips 主数据工具 | `backend/skills/philipswgqinboundrecognition/scripts/tools.py` |
| Tecan 资源 SKILL | `backend/skills/tecan-import/SKILL.md` |
| Tecan 字段/规则参考 | `backend/skills/tecan-import/references/` |
| Tecan 模板资产 | `backend/skills/tecan-import/assets/` |
| Tecan 业务工具 | `backend/skills/tecanimport/scripts/tools.py` |
| Tecan Excel 写入 | `backend/skills/tecanimport/scripts/documents.py` |

### Integrations

| 角色 | 路径 |
|------|------|
| Artifact 路径与 JSON | `backend/integrations/artifacts.py` |
| MinerU 文档解析 | `backend/integrations/mineru.py` |

### Tests

| 角色 | 路径 |
|------|------|
| 共享 FakeBrain / 等待 | `backend/tests/test_support.py` |
| API / harness / ledger / tools / workflow | `backend/tests/test_*.py`（见布局树） |
| 真实外部依赖 | `backend/tests/test_real_*.py` |

### 运行时数据（非源码，但路径契约）

| 角色 | 默认路径 |
|------|----------|
| Run ledger DB | `backend/data/dsagents_runs.db` |
| LangGraph store | `backend/data/dsagents_store.db` |
| Checkpoints | `backend/data/dsagents_checkpoints.db` |
| 上传 / 下载 | `backend/data/artifacts/uploads/`、`.../downloads/` |
| 事件大 blob | `backend/data/internal/run-events/` |
| OMS JSONL | `backend/log/oms_log.log` |

## Naming Conventions

### Python 包 vs Skill 资源目录

| 类型 | 约定 | 示例 |
|------|------|------|
| 可 import 包 | **无连字符**、小写、可作 `skills.<pkg>` | `philipswgqinboundrecognition`、`tecanimport` |
| Skill 资源目录 | **kebab-case**，与 `SKILL.md` frontmatter `name` 对齐 | `philips-wgq-inbound-recognition`、`tecan-import` |
| 工作流 ID（API/ledger） | snake_case 字面量 | `philips_wgq_inbound_recognition` |
| 工具函数名 | snake_case callable，与注册 `__name__` 一致 | `parse_documents`、`lookup_philips_wgq_master_data` |
| SubAgent name | kebab-case | `tecan-extractor-a`、`tecan-extractor-b` |
| 主 Agent name | 固定字符串 | `dsagents-main` |
| 虚拟 FS 前缀 | 以 `/` 开头的逻辑路径 | `/artifacts/`、`/skills/`、`/memories/` |
| 测试模块 | `test_<area>.py`，入口 `run()` | `python -m tests.test_harness` |
| 真实集成测试 | `test_real_*.py` | 与 mock 回归分离 |

### package-data（`pyproject.toml`）

带连字符的资源必须列入 `tool.setuptools.package-data` 的 `"skills"` 列表，否则 wheel 不带 `SKILL.md` / assets：

- `philips-wgq-inbound-recognition/SKILL.md`
- `tecan-import/SKILL.md`
- `tecan-import/references/*.md`
- `tecan-import/assets/*`

setuptools：`py-modules = ["api"]`；`packages.find` include `runtime*`、`integrations*`、`skills*`。

### Protocol 使用边界

- `typing.Protocol` **只**用于 `Brain` / `BrainFactory`。
- 工具：callable + `ToolCatalog`。
- 资源与 ledger：具体类（`AgentResources`、`SqliteRunLedger` 等）。

## 哪里放新代码

### 新 Skill

1. **资源目录**：`backend/skills/<kebab-name>/`
   - 必有 `SKILL.md`（frontmatter `name` 与目录一致）
   - 可选 `references/`、`assets/`
2. **Python 包**：`backend/skills/<importable_pkg>/`
   - `schema` / 工具 / 脚本；业务问题结构与既有 Skill 对齐
3. **注册工具**：在 `runtime/tools.py` 的 `default_tool_catalog()` **静态**追加 import + handler
   - 若与其他业务互斥：在 `runtime/agent.py` 用 **denylist** 排除对方业务工具，勿用业务-only allowlist
4. **package-data**：更新 `pyproject.toml` 中 `"skills"` 资源 glob
5. **测试**：`backend/tests/test_<skill_area>.py`，提供 `run()`
6. **验证**：`python -m tests.test_tools`（及业务相关模块）；workflow 变更时 `python -m tests.test_workflow_setup`

### 新 Tool（无新 Skill 时）

1. 通用集成 → `backend/integrations/` 新模块或扩展现有模块
2. 业务专用 → 对应 skill 的 `scripts/tools.py`
3. 在 `default_tool_catalog()` 注册
4. 若主 Agent 需要 telemetry / progress：遵循 `get_stream_writer` 与 `execution.py` 对 `parse_documents`/`extract_archives` 的 progress 分流约定
5. 测试放 `tests/test_tools.py` 或独立 `tests/test_*.py`

### 新 Middleware

1. 实现放 `backend/runtime/middleware.py`（或同包新模块再由 `runtime_middlewares` 组装）
2. 主 Agent / SubAgent 是否挂载在 `runtime_middlewares(...)` 与 `DeepAgentsBrainFactory.create` 中区分
3. 结构化输出相关须遵守 `can_jump_to` 含 `"end"` 等 harness 约束
4. 用 `tests/test_harness.py` 覆盖

### 新测试

| 类型 | 放置 | 运行 |
|------|------|------|
| 本地 mock 回归 | `backend/tests/test_<name>.py`，实现 `run()` | `cd backend && python -m tests.test_<name>` |
| 共享夹具 | `backend/tests/test_support.py` | 被其他模块 import |
| 真实模型 / MinerU / Oracle / HTTP | `backend/tests/test_real_*.py` | 单独、需环境；不并入默认本地门禁假设 |

常用门禁（摘要）：

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
```

### 不应放入的位置

| 避免 | 原因 |
|------|------|
| 在 `api.py` 堆业务抽取逻辑 | HTTP 只做边界与装配 |
| 自动扫描 `skills/` 注册工具 | 约定静态 `ToolCatalog` |
| 新增 session REST / SSE | 架构固定四端点 + run 查询 |
| 把密钥写入 codebase 文档或源码常量 | 仅通过 env；映射文档不读 `.env` 值 |
| 业务工具 allowlist 替代 denylist | 会误删共享 MinerU 工具 |

## 模块依赖方向（简图）

```
api.py
  → runtime.execution / resources / oms_log
  → integrations.artifacts（上传命名）

runtime.execution
  → runtime.agent（BrainFactory）
  → runtime.middleware / observability / tools / runs / resources
  → skills.philipswgqinboundrecognition（WORKFLOW、结果校验）

runtime.agent
  → runtime.middleware / tools / observability
  → skills.philipswgqinboundrecognition
  → deepagents / langchain

runtime.tools
  → integrations.mineru
  → skills.*.scripts.tools

skills.tecanimport.scripts.documents
  → skills/tecan-import/assets（模板路径）
  → integrations.artifacts

skills.philipswgqinboundrecognition.scripts.tools
  → integrations.artifacts
  → oracledb（可选）
```

依赖原则：`integrations` 不依赖 `skills`；`skills` 可依赖 `integrations` 与标准库；`runtime` 可编排两者；`api` 只依赖 `runtime` + 少量 `integrations`。
