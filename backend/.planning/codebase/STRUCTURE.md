---
last_mapped_commit: d39ed16
---

# Codebase Structure

**Analysis Date:** 2026-07-18

> 事实来源：`backend/` 工作树布局与源码。发行名 `dsagents`；安装根为 `backend/`（`package-dir=""`）。旧 `backend/dsagents/` 包壳已删除。`last_mapped_commit` 为 `d39ed16`。

## Directory Layout

```text
backend/
├── api.py                              # FastAPI HTTP：upload / runs / cancel
├── pyproject.toml                      # 包名 dsagents；setuptools 布局
├── uv.lock
├── log/                                # 运行时生成：oms_log.log（OMS run 检索索引，非 run_events）
├── runtime/
│   ├── __init__.py                     # 稳定导出：Resources / harness / ledger
│   ├── agent.py                        # Brain Protocol、工厂、SubAgent 装配
│   ├── middleware.py                   # runtime middleware 与兼容性 hook
│   ├── execution.py                    # HarnessRuntime.execute_run / cancel
│   ├── observability.py                # chunk → 载荷纯函数
│   ├── oms_log.py                      # POST /runs 成功 create 后 JSONL 摘要索引
│   ├── resources.py                    # AgentResources + ResourceConfig + CompositeBackend
│   ├── runs.py                         # SqliteRunLedger、RunEvent、RunSnapshot
│   └── tools.py                        # ToolCatalog + default_tool_catalog()
├── integrations/
│   ├── __init__.py
│   ├── artifacts.py                    # /artifacts/ 路径与 JSON artifact helper
│   └── mineru.py                       # parse_documents / extract_archives
├── skills/
│   ├── __init__.py
│   ├── philipswgqinboundrecognition/   # Philips Python 运行时包
│   │   ├── __init__.py
│   │   ├── schema.py                   # Pydantic 响应合同；WORKFLOW 常量
│   │   └── scripts/
│   │       ├── __init__.py
│   │       └── tools.py                # Tracking/Oracle 单一主数据 Tool
│   ├── philips-wgq-inbound-recognition/ # Philips Agent Skill 资源
│   │   └── SKILL.md
│   ├── tecanimport/                    # Tecan Python 运行时包
│   │   ├── __init__.py
│   │   └── scripts/
│   │       ├── __init__.py
│   │       ├── tools.py
│   │       └── documents.py
│   └── tecan-import/                   # Tecan Agent Skill 资源
│       ├── SKILL.md
│       ├── references/
│       │   ├── fields.md
│       │   └── rules.md
│       └── assets/
│           └── Tecan_进口_发票箱单_空运.xlsx
├── tests/
│   ├── __init__.py
│   ├── test_support.py                 # FakeBrain / helpers
│   ├── test_api.py
│   ├── test_harness.py
│   ├── test_run_ledger.py
│   ├── test_tools.py
│   ├── test_workflow_setup.py
│   ├── test_philips_wgq_inbound_recognition.py
│   ├── test_tecan_import.py
│   ├── test_real_philips_wgq_inbound_recognition.py
│   ├── test_real_philips_wgq_ups.py    # 真实集成（诊断/HTTP）
│   ├── test_real_image_run.py
│   ├── test_real_multi_pdf_run.py
│   └── test_minimax_cache_baseline.py
├── .planning/
│   └── codebase/                       # 子项目级事实文档（本目录）
└── data/                               # 运行时数据（通常 gitignore）
    ├── dsagents_runs.db
    ├── dsagents_checkpoints.db
    ├── dsagents_store.db
    ├── artifacts/
    │   ├── uploads/
    │   └── downloads/
    └── internal/
        └── run-events/                 # 大 payload spill（按需创建）
```

构建/安装产物（非源码事实，可能出现在工作树）：`dist/`、`dsagents.egg-info/`、`build/`、`__pycache__/`。分析时跳过数据产物与缓存。

## Directory Purposes

| 路径 | 用途 |
|------|------|
| `backend/api.py` | 唯一 HTTP 入口模块；`create_app` / 模块级 `app`；四端点 + session 单飞锁 + OMS 触发 |
| `backend/runtime/` | 运行时核心：执行、资源、事件账本、OMS 索引日志、工具目录、Brain 装配、middleware、可观测提取 |
| `backend/log/` | 运行时生成：`oms_log.log`（按时间/文件名检索 run；非 git 源码；非 `run_events`） |
| `backend/integrations/` | 与外部系统/路径契约的通用能力（artifact FS、MinerU HTTP），不含业务裁决 |
| `backend/skills/` | 内置 Agent Skill 资源与运行时 Python 包；资源目录提供 `SKILL.md`、字段/规则参考和模板 |
| `backend/skills/*/scripts/` | 运行时业务 Tool；Tecan 同时含 Excel 生成；由 `runtime/tools.py` 静态 import |
| `backend/tests/` | 可执行 assert 脚本与 `FakeBrain` 替身；真实集成脚本与本地回归分文件；**无**仓库内 `tests_file/` 夹具目录（真实样例路径由 env 或脚本默认值指向外部） |
| `backend/data/` | 固定数据根（`ResourceConfig`，与 CWD 无关）：三库 + artifacts + 事件 spill |
| `backend/.planning/codebase/` | 子项目 codebase maps；根级文档上游事实源 |
| `backend/pyproject.toml` | 依赖与 setuptools 打包：`py-modules=["api"]`，packages `runtime*` / `integrations*` / `skills*` |

### Skill 目录角色

运行时 Python 包目录使用合法包名，可直接绝对导入；Agent Skill 资源目录使用符合规范的 **kebab-case** 连字符名称，并通过 `skills=[SKILLS_SOURCE]`（`/skills/`）挂载到虚拟 FS；无动态 loader。Philips Python 包目录为 `philipswgqinboundrecognition`，Skill 资源目录为 `philips-wgq-inbound-recognition`，其 workflow 常量为 `philips_wgq_inbound_recognition`（`schema.WORKFLOW`）。

| 子路径 | 角色 |
|--------|------|
| `*/SKILL.md` | 主 Agent 可读的领域流程；Philips 只有一份专用提示词 |
| `schema.py` | Philips 固定 Pydantic 结构化响应合同（`PhilipsWgqRecognitionResult`） |
| `tecan-import/references/` / `tecan-import/assets/` | Tecan 字段参考与 Excel 模板 |
| `scripts/tools.py` | 暴露给模型的业务 Tool；Philips 1 个、Tecan 2 个 |
| `scripts/documents.py` | 仅 Tecan 的模板写入实现，不直接注册为 Tool |

Skill 包布局约定：**kebab-case `SKILL.md` 资源目录** + **可 import 的 Python 包目录** 成对存在（两套内置 Skill 均如此）。

## Key File Locations

### 入口与装配

| 关注点 | 文件 |
|--------|------|
| HTTP app | `backend/api.py` |
| 程序内 harness 工厂 | `backend/runtime/execution.py` → `create_harness` |
| 资源 context manager | `backend/runtime/resources.py` → `AgentResources` |
| 包导出 | `backend/runtime/__init__.py` |

### Run 与事件

| 关注点 | 文件 |
|--------|------|
| 执行循环 / cancel | `backend/runtime/execution.py` |
| ledger schema / 投影 | `backend/runtime/runs.py` |
| stream 载荷提取 | `backend/runtime/observability.py` |
| OMS run_created 索引 | `backend/runtime/oms_log.py` |

### Brain / 工具 / Skill / middleware

| 关注点 | 文件 |
|--------|------|
| Protocol 与默认工厂 | `backend/runtime/agent.py` |
| middleware 与有界 structured recovery | `backend/runtime/middleware.py` |
| 静态工具注册 | `backend/runtime/tools.py` |
| MinerU | `backend/integrations/mineru.py` |
| artifact 路径安全 | `backend/integrations/artifacts.py` |
| Philips 响应合同 | `backend/skills/philipswgqinboundrecognition/schema.py` |
| Philips 主数据工具 | `backend/skills/philipswgqinboundrecognition/scripts/tools.py` |
| Tecan 业务 | `backend/skills/tecanimport/scripts/tools.py` |
| Tecan Excel | `backend/skills/tecanimport/scripts/documents.py` |

### 测试

| 关注点 | 文件 |
|--------|------|
| HTTP 契约 / cancel / usage | `backend/tests/test_api.py` |
| harness / middleware / 事件序列 / recovery 封顶 | `backend/tests/test_harness.py` |
| ledger / spill / 聚合 | `backend/tests/test_run_ledger.py` |
| 五工具注册与 MinerU mock | `backend/tests/test_tools.py` |
| SubAgent / middleware / Philips denylist 装配 | `backend/tests/test_workflow_setup.py` |
| 业务合同/生成形状 | `backend/tests/test_philips_wgq_inbound_recognition.py`、`test_tecan_import.py` |
| Philips 真实 HTTP 样例 | `backend/tests/test_real_philips_wgq_inbound_recognition.py` |
| 替身 | `backend/tests/test_support.py` |

### 业务 Tool → 模块映射

| 工具可调用名 | 所属模块 |
|--------------|----------|
| `parse_documents` | `integrations/mineru.py` |
| `extract_archives` | `integrations/mineru.py` |
| `lookup_philips_wgq_master_data` | `skills/philipswgqinboundrecognition/scripts/tools.py` |
| `save_tecan_extraction` | `skills/tecanimport/scripts/tools.py` |
| `generate_tecan_import` | `skills/tecanimport/scripts/tools.py` |

## Naming Conventions

### 包与导入

- 安装根：`backend/`；`package-dir = {"" = "."}`。
- 顶层包：`runtime`、`integrations`、`skills`；顶层模块：`api`。
- **绝对顶层导入**（`backend/` 在 `sys.path` 或已 editable 安装）：
  - `from runtime import AgentResources, create_harness`
  - `from runtime.runs import SqliteRunLedger, RunEvent`
  - `from runtime.oms_log import append_run_created_log`
  - `from integrations.artifacts import resolve_artifact_path, write_json_artifact`
  - `from skills.philipswgqinboundrecognition.schema import PhilipsWgqRecognitionResult`
- Python 包目录名：小写无连字符（`philipswgqinboundrecognition`、`tecanimport`）；Agent Skill 资源目录名使用连字符（`philips-wgq-inbound-recognition`、`tecan-import`），Philips HTTP workflow 使用下划线常量 `philips_wgq_inbound_recognition`。

### 标识符风格

| 类别 | 约定 | 示例 |
|------|------|------|
| 模块/文件 | snake_case | `execution.py`、`artifacts.py`、`middleware.py`、`oms_log.py` |
| 类 | PascalCase | `HarnessRuntime`、`SqliteRunLedger`、`StructuredOutputRecovery` |
| 函数/方法 | snake_case | `execute_run`、`emit_run_status`、`runtime_middlewares`、`append_run_created_log` |
| 常量 | UPPER_SNAKE | `RUN_STATUSES`、`MAIN_AGENT_NAME`、`SKILLS_SOURCE`、`DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES`、`DEFAULT_OMS_LOG_PATH` |
| frozen dataclass | PascalCase | `RunEvent`、`ToolCatalog`、`ResourceConfig` |
| Protocol | PascalCase 能力名 | `Brain`、`BrainFactory`（**仅此**使用 `typing.Protocol`） |
| 工具函数名 | snake_case，即模型可见名 | `parse_documents`、`generate_tecan_import` |
| SubAgent `name` | kebab-case | `tecan-extractor-a`、`tecan-extractor-b`；Philips 无 SubAgent |
| 主 Agent `name` | `dsagents-main`（`MAIN_AGENT_NAME`） | |
| 虚拟路径 | POSIX `/artifacts/...`、`/skills/`、`/memories/` | |
| 事件 type 字符串 | snake_case | `tool_execution`、`model_usage`、`assistant_message` |
| run status | 小写英文 | `queued`、`cancelling`、`succeeded` |
| OMS log event | snake_case | `run_created` |
| 测试模块 | `test_*.py`，入口常为 `run()` | 非 pytest 发现规则强制 |
| 环境变量 | UPPER_SNAKE | `MINIMAX_*`、`MINERU_*`、`ORACLE_*` |

### 数据文件命名

- 上传：`{cleaned_stem}_{YYYYMMDDHHMMSS}(_n).{ext}`（`make_timestamped_name`）
- 下载/业务产物：`unique_download_path(stem, suffix)` → stem + 时间戳 + 冲突后缀
- 事件 spill：`{uuid.hex}.json` 于 `data/internal/run-events/`
- DB：`dsagents_runs.db` / `dsagents_checkpoints.db` / `dsagents_store.db`
- OMS：`log/oms_log.log`（JSONL 追加）
- 时间戳字段：中国时区（UTC+8）本地格式 `YYYY-MM-DD HH:MM:SS`（`SqliteRunLedger` 与 `oms_log` 一致）

### 文档语言

- `.planning/codebase/` 与长期文档：说明用简体中文；代码标识符、路径、命令、配置键、API 名称保持原文。

## 模块职责表

| 模块 | 职责 | 不负责 |
|------|------|--------|
| `api.py` | HTTP 契约、session 单飞锁、后台线程调度、usage 计价汇总、触发 OMS 日志 | 业务裁决、stream 解析细节 |
| `runtime/execution.py` | `HarnessRuntime`：装配 Brain、消费 stream、写 7 类事件、协作取消 | 解析业务字段、计价、OMS 文件 |
| `runtime/agent.py` | `Brain`/`BrainFactory` Protocol、DeepAgents 工厂、SubAgent 声明 | 事件落库、HTTP |
| `runtime/middleware.py` | Tool 遥测、无进展熔断、ToolStrategy thinking 兼容、structured recovery 有界重试 | harness 循环、ledger |
| `runtime/observability.py` | chunk → usage/thinking/text/assistant/tool payload 纯函数 | I/O、run 状态 |
| `runtime/resources.py` | 三库 + CompositeBackend 装配与 handbook 种子 | run 事件语义 |
| `runtime/runs.py` | append-only `run_events` + `runs` 投影、usage 聚合、启动清理 | 模型调用、OMS 旁路 |
| `runtime/oms_log.py` | HTTP `create_run` 后 best-effort JSONL 索引（`run_created`） | run_events、业务结果、查询 API |
| `runtime/tools.py` | `ToolCatalog` 与 5 工具静态注册 | 工具实现体 |
| `integrations/artifacts.py` | `/artifacts/` 路径安全、命名、JSON 写入 helper | 业务 schema |
| `integrations/mineru.py` | MinerU 解析/解压工具与 progress 事件 | run ledger |
| `skills/philipswgqinboundrecognition/` | 固定 workflow 合同 + 主数据 Tool | harness 通用路径 |
| `skills/philips-wgq-inbound-recognition/` | Philips Agent Skill `SKILL.md` | DeepAgents `/skills/` 挂载 |
| `skills/tecanimport/` | 抽取/生成 Excel 业务 Tool | HTTP 入口 |
| `skills/tecan-import/` | Tecan Agent Skill `SKILL.md`、references、assets | DeepAgents `/skills/` 挂载 |
| `tests/*` | 可执行 assert 回归与真实集成脚本 | 生产路径 |

## 数据目录

| 路径 | 说明 |
|------|------|
| `backend/data/` | `ResourceConfig.data_dir` 默认根；与进程 CWD 无关 |
| `data/dsagents_runs.db` | `SqliteRunLedger`：`runs` 投影 + `run_events` 事件源 |
| `data/dsagents_checkpoints.db` | LangGraph `SqliteSaver`；`thread_id = session_id` |
| `data/dsagents_store.db` | LangGraph `SqliteStore`；`/memories/` 经 StoreBackend |
| `data/artifacts/uploads/` | `POST /upload` 落盘 |
| `data/artifacts/downloads/` | MinerU / 业务工具产物 |
| `data/internal/run-events/` | 超 `max_inline_bytes`（默认 262_144）的事件 payload spill |
| `backend/log/oms_log.log` | OMS `run_created` JSONL；锚定 `backend/`，**非** `data/` 下 |

## Where to Add New Code

| 目标 | 落点 | 注意 |
|------|------|------|
| 新 HTTP 路由/契约 | `api.py` | 保持 run-first；轮询非 SSE；可注入 `harness_factory` 便于测 |
| 改 stream→事件映射 | `runtime/execution.py` | 保持 harness 薄；业务不下沉到此 |
| 新 chunk 字段解析 | `runtime/observability.py` | 保持纯函数、无 I/O |
| 新 middleware | `runtime/middleware.py` + `runtime_middlewares()` | `execution.py` / `agent.py` 装配；主 Agent 手册用 `memory_backend=`；SubAgent 各自注入无 memory 实例 |
| structured recovery 调整 | `StructuredOutputRecovery` | 必须保留 `can_jump_to` 含 `"end"` 与耗尽时 `jump_to: "end"` |
| 换默认模型/装配 | `DeepAgentsBrainFactory` 或注入自定义 `BrainFactory` | 仅 Protocol 边界可替换 |
| 新通用工具（非业务） | 宜放 `integrations/` 或 `runtime/`，并在 `default_tool_catalog()` 静态追加 | 禁止自动扫描插件 |
| 新业务 Skill | `skills/<skill-name>/` +（需要代码时）`skills/<package_name>/`：只创建实际需要的 `SKILL.md` / schema / scripts / assets | Skill 资源目录可用连字符且必须与 frontmatter `name` 一致；Python 包目录须合法；Tool 静态注册 |
| 收窄 workflow 工具表 | denylist 排除**其他业务**工具，保留共享 MinerU 工具 | 禁止只 allowlist 业务工具导致通用工具从模型表消失 |
| 新 extractor SubAgent | `workflow_subagents()` / `_extractor` | 仅真实需要投票抽取时增加；自装 middleware |
| 改持久化路径/后端路由 | `ResourceConfig` / `AgentResources.__enter__` | 三库职责勿混 |
| 改 run 事件 schema | `runtime/runs.py` | fresh schema 无迁移；破坏性变更需清库策略 |
| 改 OMS 索引字段 | `runtime/oms_log.py` | 保持 best-effort、不进入 `run_events` |
| 本地回归测试 | `tests/test_*.py` + `test_support.py` | assert 脚本；`python -m tests.<module>` |
| 真实模型/HTTP 集成 | 独立 `test_real_*.py` 等，env 守卫 | 勿并入默认本地回归 |
| 文档事实刷新 | `backend/.planning/codebase/*.md` | 改 backend 后先同步此处，再按影响更新根级文档 |

### 新增 Skill 最小清单

1. 创建 `skills/<skill-name>/`，只加入本 Skill 实际需要的 `SKILL.md`、references 或 assets；需要 Python 代码时另建合法包目录 `skills/<package_name>/`。
2. 在 `runtime/tools.py` 的 `default_tool_catalog()` 静态 import 并注册 Tool。
3. 仅当业务明确需要独立抽取器时，在 `workflow_subagents()` 增加声明式 SubAgent。
4. `pyproject.toml` `[tool.setuptools.package-data]` 为 `skills` package 声明 `SKILL.md` / `references/*` / `assets/*`。
5. 增加 `tests/test_<skill>.py` 覆盖合同与领域规则；真实依赖另放 env 守卫脚本。
6. 刷新 `.planning/codebase/` 相关事实文档。

### 不要放在哪里

- 不要在 `api.py` 写业务裁决或 Excel 逻辑；固定 workflow 字段校验和路由透传属于 HTTP 契约。
- 不要在 `execution.py` 解析业务字段；只转发事件。
- 不要新增 session 表或 SSE 通道替代 run 轮询（除非产品契约整体变更）。
- 不要引入动态 Skill 扫描器替代 `default_tool_catalog()` 静态注册。
- 不要把密钥或 `.env` 内容写入文档或测试断言字符串。
- 不要在 `after_model` 有界重试中省略 `can_jump_to` 的 `"end"` 或耗尽时只返回 `None`。
- 不要用业务工具 allowlist 收窄 workflow 工具表，导致 `parse_documents` / `extract_archives` 从模型工具表消失。
- 不要把 OMS 日志写入 `run_events` 或依赖其作为执行真相源。

---
*Structure analysis: 2026-07-18 · last_mapped_commit: d39ed16*
