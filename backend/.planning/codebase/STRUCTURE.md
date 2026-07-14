---
last_mapped_commit: 08413f4688e03e5a24fb8ac08270541d280aee5d
---

# Codebase Structure

**Analysis Date:** 2026-07-14

> 事实来源：`backend/` 工作树布局与源码。发行名 `dsagents`；安装根为 `backend/`（`package-dir=""`）。旧 `backend/dsagents/` 包壳已删除。

## Directory Layout

```text
backend/
├── api.py                              # FastAPI HTTP：upload / runs / cancel
├── pyproject.toml                      # 包名 dsagents；setuptools 布局
├── uv.lock
├── runtime/
│   ├── __init__.py                     # 稳定导出：Resources / harness / ledger
│   ├── agent.py                        # Brain Protocol、工厂、middleware、SubAgent
│   ├── execution.py                    # HarnessRuntime.execute_run / cancel
│   ├── observability.py                # chunk → 载荷纯函数
│   ├── resources.py                    # AgentResources + ResourceConfig + CompositeBackend
│   ├── runs.py                         # SqliteRunLedger、RunEvent、RunSnapshot
│   └── tools.py                        # ToolCatalog + default_tool_catalog()
├── integrations/
│   ├── __init__.py
│   ├── artifacts.py                    # /artifacts/ 路径与 JSON artifact helper
│   └── mineru.py                       # parse_documents / extract_archives
├── skills/
│   ├── __init__.py
│   ├── philipswgqimport/               # Philips 外高桥进境 Skill
│   │   ├── __init__.py
│   │   ├── SKILL.md
│   │   ├── references/
│   │   │   ├── fields.md
│   │   │   └── rules.md
│   │   ├── assets/                     # Excel 模板
│   │   └── scripts/
│   │       ├── __init__.py
│   │       ├── tools.py                # save_* + generate_* 业务 Tool
│   │       └── documents.py            # openpyxl 写入器
│   └── tecanimport/                    # Tecan 帝肯进口 Skill
│       ├── __init__.py
│       ├── SKILL.md
│       ├── references/
│       │   ├── fields.md
│       │   └── rules.md
│       ├── assets/
│       └── scripts/
│           ├── __init__.py
│           ├── tools.py
│           └── documents.py
├── tests/
│   ├── __init__.py
│   ├── test_support.py                 # FakeBrain / helpers
│   ├── test_api.py
│   ├── test_harness.py
│   ├── test_run_ledger.py
│   ├── test_tools.py
│   ├── test_workflow_setup.py
│   ├── test_philips_wgq_import.py
│   ├── test_tecan_import.py
│   ├── test_real_image_run.py          # 真实集成（env 守卫）
│   ├── test_real_multi_pdf_run.py
│   ├── test_minimax_cache_baseline.py
│   └── tests_file/                     # 样例图片/PDF（非源码）
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

构建/安装产物（非源码事实，可能出现在工作树）：`dist/`、`dsagents.egg-info/`、`__pycache__/`。

## Directory Purposes

| 路径 | 用途 |
|------|------|
| `backend/api.py` | 唯一 HTTP 入口模块；`create_app` / 模块级 `app` |
| `backend/runtime/` | 运行时核心：执行、资源、事件账本、工具目录、Brain 装配、可观测提取 |
| `backend/integrations/` | 与外部系统/路径契约的通用能力（artifact FS、MinerU HTTP），不含业务裁决 |
| `backend/skills/` | 内置 Agent Skills：指令（`SKILL.md`）、字段/规则参考、模板、可调用 scripts |
| `backend/skills/*/scripts/` | 业务 Tool 与 Excel 生成；由 `runtime/tools.py` 静态 import |
| `backend/tests/` | 可执行 assert 脚本与 `FakeBrain` 替身；真实集成脚本与本地回归分文件 |
| `backend/tests/tests_file/` | 手工/集成用样例文件 |
| `backend/data/` | 固定数据根（`ResourceConfig`，与 CWD 无关）：三库 + artifacts + 事件 spill |
| `backend/.planning/codebase/` | 子项目 codebase maps；根级文档上游事实源 |
| `backend/pyproject.toml` | 依赖与 setuptools 打包：`py-modules=["api"]`，packages `runtime*` / `integrations*` / `skills*` |

### Skill 目录角色

每个 Skill 目录名同时满足 Agent Skill 名与 Python 包标识符（无连字符），故可直接 `from skills.philipswgqimport...` 与 `skills=[SKILLS_SOURCE]`（`/skills/`）挂载，无需动态 loader。

| 子路径 | 角色 |
|--------|------|
| `SKILL.md` | 主 Agent 可读的适用场景与 A/B/C 流程指令 |
| `references/` | 字段与规则详述（Agent 按需读取） |
| `assets/` | 只读 Excel 模板（随 wheel `package-data` 打包） |
| `scripts/tools.py` | 暴露给模型的业务 Tool（每 Skill 2 个） |
| `scripts/documents.py` | 模板填充/行插入等写入实现，一般不直接注册为 Tool |

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

### Brain / 工具 / Skill

| 关注点 | 文件 |
|--------|------|
| Protocol 与默认工厂 | `backend/runtime/agent.py` |
| 静态工具注册 | `backend/runtime/tools.py` |
| MinerU | `backend/integrations/mineru.py` |
| artifact 路径安全 | `backend/integrations/artifacts.py` |
| Philips 业务 | `backend/skills/philipswgqimport/scripts/tools.py` |
| Philips Excel | `backend/skills/philipswgqimport/scripts/documents.py` |
| Tecan 业务 | `backend/skills/tecanimport/scripts/tools.py` |
| Tecan Excel | `backend/skills/tecanimport/scripts/documents.py` |

### 测试

| 关注点 | 文件 |
|--------|------|
| HTTP 契约 / cancel / usage | `backend/tests/test_api.py` |
| harness / middleware / 事件序列 | `backend/tests/test_harness.py` |
| ledger / spill / 聚合 | `backend/tests/test_run_ledger.py` |
| 六工具注册与 MinerU mock | `backend/tests/test_tools.py` |
| SubAgent / middleware 装配 | `backend/tests/test_workflow_setup.py` |
| 业务生成形状 | `backend/tests/test_philips_wgq_import.py`、`test_tecan_import.py` |
| 替身 | `backend/tests/test_support.py` |

### 业务 Tool → 模块映射

| 工具可调用名 | 所属模块 |
|--------------|----------|
| `parse_documents` | `integrations/mineru.py` |
| `extract_archives` | `integrations/mineru.py` |
| `save_philips_wgq_extraction` | `skills/philipswgqimport/scripts/tools.py` |
| `generate_philips_wgq_import` | `skills/philipswgqimport/scripts/tools.py` |
| `save_tecan_extraction` | `skills/tecanimport/scripts/tools.py` |
| `generate_tecan_import` | `skills/tecanimport/scripts/tools.py` |

## Naming Conventions

### 包与导入

- 安装根：`backend/`；`package-dir = {"" = "."}`。
- 顶层包：`runtime`、`integrations`、`skills`；顶层模块：`api`。
- **绝对顶层导入**（`backend/` 在 `sys.path` 或已 editable 安装）：
  - `from runtime import AgentResources, create_harness`
  - `from runtime.runs import SqliteRunLedger, RunEvent`
  - `from integrations.artifacts import resolve_artifact_path, write_json_artifact`
  - `from skills.philipswgqimport.scripts.tools import generate_philips_wgq_import`
- Skill 目录名：小写无连字符 Python 包名（`philipswgqimport`、`tecanimport`），与 workflow 字符串（如 `philips-wgq-import`）区分。

### 标识符风格

| 类别 | 约定 | 示例 |
|------|------|------|
| 模块/文件 | snake_case | `execution.py`、`artifacts.py` |
| 类 | PascalCase | `HarnessRuntime`、`SqliteRunLedger` |
| 函数/方法 | snake_case | `execute_run`、`emit_run_status` |
| 常量 | UPPER_SNAKE | `RUN_STATUSES`、`MAIN_AGENT_NAME`、`SKILLS_SOURCE` |
| frozen dataclass | PascalCase | `RunEvent`、`ToolCatalog`、`ResourceConfig` |
| Protocol | PascalCase 能力名 | `Brain`、`BrainFactory` |
| 工具函数名 | snake_case，即模型可见名 | `parse_documents`、`generate_tecan_import` |
| SubAgent `name` | kebab-case | `philips-wgq-extractor-a`、`tecan-extractor-b` |
| 主 Agent `name` | `dsagents-main`（`MAIN_AGENT_NAME`） | |
| 虚拟路径 | POSIX `/artifacts/...`、`/skills/`、`/memories/` | |
| 事件 type 字符串 | snake_case | `tool_execution`、`model_usage`、`assistant_message` |
| run status | 小写英文 | `queued`、`cancelling`、`succeeded` |
| 测试模块 | `test_*.py`，入口常为 `run()` | 非 pytest 发现规则强制 |
| 环境变量 | UPPER_SNAKE | `MINIMAX_*`、`MINERU_*`、`ORACLE_*` |

### 数据文件命名

- 上传：`{cleaned_stem}_{YYYYMMDDHHMMSS}(_n).{ext}`（`make_timestamped_name`）
- 下载/业务产物：`unique_download_path(stem, suffix)` → stem + 时间戳 + 冲突后缀
- 事件 spill：`{uuid.hex}.json` 于 `data/internal/run-events/`
- DB：`dsagents_runs.db` / `dsagents_checkpoints.db` / `dsagents_store.db`

### 文档语言

- `.planning/codebase/` 与长期文档：说明用简体中文；代码标识符、路径、命令、配置键、API 名称保持原文。

## Where to Add New Code

| 目标 | 落点 | 注意 |
|------|------|------|
| 新 HTTP 路由/契约 | `api.py` | 保持 run-first；轮询非 SSE；可注入 `harness_factory` 便于测 |
| 改 stream→事件映射 | `runtime/execution.py` | 保持 harness 薄；业务不下沉到此 |
| 新 chunk 字段解析 | `runtime/observability.py` | 保持纯函数、无 I/O |
| 新 middleware | `runtime/agent.py` + `runtime_middlewares()` | SubAgent 需各自注入实例 |
| 换默认模型/装配 | `DeepAgentsBrainFactory` 或注入自定义 `BrainFactory` | 仅 Protocol 边界可替换 |
| 新通用工具（非业务） | 宜放 `integrations/` 或 `runtime/`，并在 `default_tool_catalog()` 静态追加 | 禁止自动扫描插件 |
| 新业务 Skill | `skills/<packagename>/`：`SKILL.md` + `references/` + `assets/` + `scripts/` | 目录名须合法 Python 包名；`pyproject.toml` `package-data` 增加资源；`tools.py` 注册进 `default_tool_catalog()` |
| 新 extractor SubAgent | `workflow_subagents()` / `_extractor` | 自装 middleware；工具与权限最小化 |
| 改持久化路径/后端路由 | `ResourceConfig` / `AgentResources.__enter__` | 三库职责勿混 |
| 改 run 事件 schema | `runtime/runs.py` | fresh schema 无迁移；破坏性变更需清库策略 |
| 本地回归测试 | `tests/test_*.py` + `test_support.py` | assert 脚本；`python -m tests.<module>` |
| 真实模型/HTTP 集成 | 独立 `test_real_*.py` 等，env 守卫 | 勿并入默认本地回归 |
| 文档事实刷新 | `backend/.planning/codebase/*.md` | 改 backend 后先同步此处，再按影响更新根级文档 |

### 新增 Skill 最小清单

1. 创建 `skills/<name>/`（`SKILL.md`、`references/`、`assets/`、`scripts/{tools,documents}.py`）。
2. 在 `runtime/tools.py` 的 `default_tool_catalog()` 静态 import 并注册 Tool。
3. 若需 A/B 抽取，在 `workflow_subagents()` 增加声明式 SubAgent。
4. `pyproject.toml` `[tool.setuptools.package-data]` 声明 `SKILL.md` / `references/*` / `assets/*`。
5. 增加 `tests/test_<skill>.py` 覆盖 `input_problems` 形状与代表性工作簿输出。
6. 刷新 `.planning/codebase/` 相关事实文档。

### 不要放在哪里

- 不要在 `api.py` 写业务裁决或 Excel 逻辑。
- 不要在 `execution.py` 解析业务字段；只转发事件。
- 不要新增 session 表或 SSE 通道替代 run 轮询（除非产品契约整体变更）。
- 不要引入动态 Skill 扫描器替代 `default_tool_catalog()` 静态注册。
- 不要把密钥或 `.env` 内容写入文档或测试断言字符串。

---
*Structure analysis: 2026-07-14*
