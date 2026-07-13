# STRUCTURE

> 事实来源：当前 `backend/` 源码（run-first runtime，内置 Skill 模块化）。
> 本轮刷新（2026-07-13）已逐文件核对工作树：`dsagents/` 包（runtime / integrations / skills 子包，含两个内置 Skill 包）、`tests/`、`data/`、`pyproject.toml`。所有结论以源码为准。

## 1. 包组织

`backend/` 安装根下唯一产品包是 `dsagents/`（扁平顶层模块已被删除）。结构：

```text
backend/
├── dsagents/
│   ├── __init__.py
│   ├── api.py                      # FastAPI run-first HTTP 层
│   ├── runtime/
│   │   ├── __init__.py             # 对外稳定入口：AgentResources / create_harness / RunLedger
│   │   ├── agent.py                # Brain/BrainFactory、DeepAgentsBrainFactory、SubAgent、两个 middleware
│   │   ├── execution.py            # HarnessRuntime.execute_run：stream chunk → RunEvent；RunControl drain → cancelled
│   │   ├── observability.py        # 纯内容/元数据提取器（model_usage/thinking/text/agent scope）
│   │   ├── resources.py            # AgentResources（context manager）与 ResourceConfig；CompositeBackend 装配
│   │   ├── runs.py                 # SqliteRunLedger：runs/run_events（UTC ISO-8601 毫秒、fresh schema）
│   │   └── tools.py                # ToolCatalog + default_tool_catalog() 静态注册
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── artifacts.py            # /artifacts/ 路径、唯一下载名、immutable JSON helper、上传命名
│   │   └── mineru.py               # parse_documents / extract_archives 两个 MinerU 通用工具
│   └── skills/
│       ├── __init__.py
│       ├── philipswgqimport/        # Philips 外高桥进境 Skill（目录名同时满足 Skill 名与 Python 包名）
│       │   ├── __init__.py
│       │   ├── SKILL.md             # 适用场景/材料识别/A/B/C 流程/裁决条件/失败处理
│       │   ├── references/{fields.md,rules.md}
│       │   ├── assets/{invoice,packing进境.xlsx, 核注清单导入模板.xlsx}
│       │   └── scripts/
│       │       ├── __init__.py
│       │       ├── tools.py         # save_philips_wgq_extraction + generate_philips_wgq_import（2 个业务 Tool）
│       │       └── documents.py     # 三个 Excel 写入器 + 共享 openpyxl helper
│       └── tecanimport/             # Tecan 帝肯进口 Skill
│           ├── __init__.py
│           ├── SKILL.md
│           ├── references/{fields.md,rules.md}
│           ├── assets/Tecan_进口_发票箱单_空运.xlsx
│           └── scripts/
│               ├── __init__.py
│               ├── tools.py         # save_tecan_extraction + generate_tecan_import（2 个业务 Tool）
│               └── documents.py     # 发票箱单写入器 + insert_rows
├── tests/                           # 见第 5 节
├── pyproject.toml                   # package-dir="" + packages.find include=dsagents* + package-data
├── uv.lock
├── .planning/                       # 子项目级文档事实层
└── data/                            # 固定数据目录（整体被 gitignore；fresh schema，无迁移）
    ├── dsagents_runs.db             # run ledger（UTC ISO-8601 毫秒时间戳）
    ├── dsagents_checkpoints.db      # LangGraph checkpointer（thread_id=session_id）
    ├── dsagents_store.db            # LangGraph store（namespace=("dsagents",)）
    ├── artifacts/
    │   ├── downloads/               # MinerU/解压产物 + 唯一命名的业务 JSON/Excel
    │   └── uploads/                 # POST /upload 上传落地点
    └── internal/run-events/         # run 事件大 payload 外溢（*.json，仅真正 spill 时创建）
```

### 1.1 业务 Tool → 模块映射

每个 Skill 只暴露两个业务 Tool（抽取保存 + 一站式生成）；MinerU 两个通用工具在 `integrations/mineru.py`。全部 6 个工具由 `runtime/tools.py` 的 `default_tool_catalog()` 静态注册成 `ToolCatalog`，主 Agent 在装配时直接 import，不自动扫描、无插件平台。

| 工具可调用名 | 所属模块 |
|---|---|
| `parse_documents` | `dsagents/integrations/mineru.py` |
| `extract_archives` | `dsagents/integrations/mineru.py` |
| `save_philips_wgq_extraction` | `dsagents/skills/philipswgqimport/scripts/tools.py` |
| `generate_philips_wgq_import` | `dsagents/skills/philipswgqimport/scripts/tools.py` |
| `save_tecan_extraction` | `dsagents/skills/tecanimport/scripts/tools.py` |
| `generate_tecan_import` | `dsagents/skills/tecanimport/scripts/tools.py` |

`generate_*_import` 接收抽取 artifact 路径列表 + 裁决 `decisions`，一次完成完整校验、canonical 构建、匹配、计算、模板写入和输出复核。业务问题统一返回 `{"code":"input_problems","problems":[{"source","location","issue","action"}]}`；成功返回 `{"status":"generated","canonical_artifact","artifacts","manual_checks"}`。不再有 `build_*_canonical` / `save_*_adjudication` / `generate_*_documents` / `needs_input` / `needs_c` / `needs_adjudication` / `info_source_preference` / `pn_info_source_overrides`。

### 1.2 `dsagents.egg-info/`

setuptools editable 安装时自动生成的元数据目录，已从版本控制删除并加入根 `.gitignore`。改依赖或包布局后由 `uv sync` 的 editable 安装按需重新生成，属正常 churn。

## 2. 绝对导入约定

`pyproject.toml`：

```toml
[tool.setuptools]
package-dir = {"" = "."}

[tool.setuptools.packages.find]
where = ["."]
include = ["dsagents*"]

[tool.setuptools.package-data]
"dsagents.skills.philipswgqimport" = ["SKILL.md", "references/*.md", "assets/*"]
"dsagents.skills.tecanimport" = ["SKILL.md", "references/*.md", "assets/*"]
```

含义：`backend/` 作为安装根，`dsagents` 是一个 Python 包。模块内一律使用**绝对包内导入**：

- `from dsagents.runtime import AgentResources, create_harness`
- `from dsagents.runtime.runs import SqliteRunLedger, RunEvent`
- `from dsagents.integrations.artifacts import resolve_artifact_path, write_json_artifact`
- `from dsagents.skills.philipswgqimport.scripts.tools import generate_philips_wgq_import`
- `from dsagents.skills.tecanimport.scripts.tools import generate_tecan_import`

调用前提是 `backend/` 在 `sys.path`（开发时 `cd backend` 运行；安装后由包提供）。

## 3. 运行入口

- **HTTP**（`dsagents/api.py`，`app = create_app()`；`create_app(*, resource_config=None, harness_factory=create_harness)` 可注入测试用的 resource 配置与 Brain 工厂）：
  - `POST /upload` —— multipart `files[]`，返回 `{files:[{file_path,name,mime_type,size}]}`。
  - `POST /runs` —— body `{messages, session_id?}`，立即返回 `{run_id, session_id, status:"queued"}`。
  - `GET  /runs/{run_id}?after_event_id=N` —— 返回 `{run, events[], latest_content_event, usage}`（`usage` 始终从该 run 全部 `model_usage` 事件汇总；无模型调用时为 `null`），未知 run 返回 `404`。**非 SSE，纯轮询。**
  - `POST /runs/{run_id}/cancel` —— 活跃 run 返回 `202`（协作 drain）；已 cancelling/cancelled 返回 `200`；已 succeeded/failed 返回 `409`；不存在返回 `404`。
  - 启动：`uvicorn dsagents.api:app --host 0.0.0.0 --port 8500`。
- **测试脚本**：从 `backend/` 目录按影响范围运行对应脚本，例如 `python -m tests.test_api`、`python -m tests.test_harness`。
- **程序内**：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(messages, session_id, run_id)`。

## 4. run 状态机

```text
queued → running → succeeded | failed
queued → cancelled
running → cancelling → cancelled
```

`SqliteSaver` 管理 `session_id` 下的 LangGraph 消息/checkpoint；run ledger 管理 run 状态和 append-only 事件。运行中取消使用 LangGraph `RunControl` 协作式 drain；`GraphDrained` 投影为 `cancelled`。取消不回滚已生成文件，不实现多进程强杀。不新增消息总线、task 表、共享 AgentState、HITL、handoff 或通用工作流引擎。

## 5. 测试位置

`backend/tests/` 是测试源码目录，断言分布：

- `test_tools.py`：MinerU/解压行为及六个默认工具注册。
- `test_run_ledger.py`：`input_messages_json`、事件投影、大 payload 外溢、启动恢复、`model_usage` 聚合与 `get_latest_content_event` 排除、UTC ISO-8601 毫秒时间戳。
- `test_harness.py`：FakeBrain（updates + subgraphs）、`ToolTelemetry`、artifact block 归一化、最终 `thinking` 载荷、subagent token 过滤、`model_usage` 提取（主/subagent scope + failed run 保留）、新事件序列（tool_execution/tool_progress）。
- `test_api.py`：`POST /upload`、`POST /runs` 契约、`latest_content_event`、`assistant_message.thinking`、并发冲突、失败续跑、启动恢复、顶层 `usage`（cache_hit_rate、tier 计价）、`POST /runs/{id}/cancel`（404/409/202/200/cancelled）。
- `test_workflow_setup.py`：Skill 挂载、四个 SubAgent（各自装 middleware）、brain 工厂 kwargs、`_update_events`。
- `test_philips_wgq_import.py` / `test_tecan_import.py`：A/B/C、裁决 decisions、统一 `input_problems` 形状与代表性工作簿输出。
- `test_real_image_run.py` / `test_real_multi_pdf_run.py` / `test_minimax_cache_baseline.py`：手动真实 HTTP / 模型 / MinerU 集成脚本（默认不运行，env 守卫）。
- `test_support.py`：`FakeBrain`/`FakeBrainFactory`/`StreamControl` 替身与 helper。

当前仍**不是 pytest 套件**；没有总控 runner，回归按影响范围直接运行对应 `test_*.py` 脚本。

## 6. `.planning/` 角色

`backend/.planning/codebase/` 是**子项目级文档事实层**，存放当前后端的持久化事实文档（codebase maps），由 `$gsd-map-codebase` 流程刷新，当前包含：

- `ARCHITECTURE.md`、`STRUCTURE.md`（本文档）、`CONVENTIONS.md`、`CONCERNS.md`、`INTEGRATIONS.md`、`STACK.md`、`TESTING.md`。

这些文档是**根级 `coding_maps/`、`AGENTS.md` 的上游事实源**；源码与文档不一致时以源码为准，并刷新对应文件。
