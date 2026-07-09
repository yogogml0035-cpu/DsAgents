# STRUCTURE

> 事实来源：当前 `backend/` 源码（run-first runtime）。
> 本轮刷新（2026-07-09）已核对当前工作树：扁平顶层模块布局、`dsagents.egg-info/` 已被 gitignore 不再跟踪、`tests/tests_file/` 为真实集成测试的样本数据。

## 1. 顶层模块组织

`backend/` **不是包**，是扁平顶层模块（flat top-level modules）。当前 `.py` 文件：

| 模块 | 职责（一两句话） |
|------|------------------|
| `api.py` | FastAPI run-first HTTP 层：`POST /runs`、`GET /runs/{run_id}`、`POST /upload`；run 后台线程调度、同 session 并发保护、启动恢复、多文件上传落盘 |
| `artifact_names.py` | 共享 artifact 命名 helper：文件名清洗、`<stem>_<timestamp>(_n).ext` 生成、上传后缀剥离 |
| `harness.py` | run 执行核心：`HarnessRuntime.execute_run` 装配 Brain/Hands/Tools 并把 `brain.stream(...)` 的 chunk 规范化为 `RunEvent`；含 `create_harness` 默认工厂与 `DeepAgentsBrainFactory`；从最终 AIMessage 提取 `assistant_message.payload.thinking` |
| `hands.py` | 执行器抽象：`Hands` Protocol + 默认 `ToolStatusHands`/`ToolStatusMiddleware`（在工具调用前后发 `tool_status` custom event） |
| `resources.py` | 资源装配：`AgentResources`（context manager）与 `ResourceConfig`；装配 run ledger、LangGraph store、LangGraph checkpointer、`CompositeBackend` |
| `run_ledger.py` | SQLite run ledger：`SqliteRunLedger` 维护 `runs`/`run_events` 表，支持状态投影、增量事件查询、大 payload 外溢、启动恢复 |
| `tools.py` | 工具定义：`ToolCatalog`/`ToolHandler` 抽象 + 默认业务工具 `parse_documents`（一次调 MinerU 批量解析文档，默认保存 task 级 JSON，按需保存 ZIP）与 `extract_archives`（解压 ZIP 列出文件清单） |

`backend/dsagents.egg-info/` 是 setuptools 安装时生成的元数据目录，已被 gitignore 忽略、**不进版本控制**（`864470d` 清理了此前误提交的副本），也不是运行入口；改依赖或 `py-modules` 后由 `pip install -e .` 重新生成，属正常 churn。

已删除（本次重构）：

- `session.py`（旧 session 模块）
- `tests/test_stream_typing.py`
- `self_check.py`（旧自检聚合入口）

无 `__init__.py`、无 `__main__.py`。

## 2. 绝对导入约定

`pyproject.toml`：

```toml
[tool.setuptools]
package-dir = {"" = "."}
py-modules = ["api", "artifact_names", "hands", "harness", "resources", "run_ledger", "tools"]
```

含义：`backend/` 目录本身作为安装根，内部 `.py` 直接安装为顶层模块。因此模块内一律使用**绝对导入**：

- `from hands import Hands, ToolStatusHands`
- `from harness import HarnessRuntime, create_harness`
- `from resources import AgentResources, ResourceConfig`
- `from run_ledger import SqliteRunLedger, RunEvent`
- `from tools import ToolCatalog, ToolHandler, default_tool_catalog`

调用前提是 `backend/` 在 `sys.path`（开发时 `cd backend` 运行；安装后由 py-modules 提供）。

## 3. 目录结构

```text
backend/
├── api.py
├── artifact_names.py
├── harness.py
├── hands.py
├── resources.py
├── run_ledger.py
├── tools.py
├── tests/
│   ├── __init__.py
│   ├── test_api.py
│   ├── test_harness.py
│   ├── test_run_ledger.py
│   ├── test_real_image_run.py
│   ├── test_real_multi_pdf_run.py
│   ├── test_support.py
│   ├── test_tools.py
│   └── tests_file/            # 真实集成测试样本数据（PDF/图片），非测试源码；由 test_real_*.py 引用
├── pyproject.toml          # package-dir=""  py-modules=[...]
├── uv.lock
├── .planning/              # 子项目级文档事实层（codebase maps 等），见下节
└── data/                   # 固定数据目录（ResourceConfig.data_dir，与 CWD 无关；整体被 gitignore）
    ├── dsagents_runs.db            # run ledger；首次创建 run/进入 `AgentResources` 时按需生成
    ├── dsagents_checkpoints.db     # LangGraph checkpointer（按需生成）
    ├── dsagents_store.db           # LangGraph store（按需生成）
    ├── artifacts/
    │   ├── downloads/              # parse_documents 输出 task 级 JSON/ZIP，extract_archives 解压到 <zip-stem>/ 子目录（按需创建）
    │   └── uploads/                # POST /upload 上传落地点（<原名>_<upload-ts>(_n).ext）；首次写入时创建
    └── internal/
        └── run-events/             # run 事件大 payload 外溢（*.json，仅真正 spill 时创建）
```

### `data/` 目录拆分规则

`data/` 由 `ResourceConfig`（`resources.py`）固定指向 `backend/data/`，整体被 gitignore（运行态产物不进版本控制）。内部按写入方职责拆成三个不相交区域：

- **逻辑数据库**（顶层 `.db`）：`dsagents_runs.db`（`SqliteRunLedger`）、`dsagents_checkpoints.db`（`SqliteSaver`，`thread_id=session_id`）、`dsagents_store.db`（`SqliteStore`，`namespace=("dsagents",)`），三者由资源装配按需创建，互不共享连接。
- **`artifacts/`**（用户可见产物）：`uploads/` 来自 `POST /upload`，`downloads/` 来自 `parse_documents`（默认 task 级 JSON，按需 task 级 ZIP）与 `extract_archives`（解压到 `<zip-stem>/` 子目录）。
- **`internal/run-events/`**（内部 spill）：仅当某条 `run_events` 的 payload 或 raw JSON 超过 `max_inline_bytes=262_144` 时才创建目录，外溢为 `{uuid}.json`，对业务层不可见。

> 代码约定的路径不等于文件一定已经存在：干净工作区里 `data/`、`dsagents_runs.db` 与 `artifacts/uploads/` 可能尚未创建，只有在对应运行路径真正发生写入后才会出现。

## 4. 对外入口

- **HTTP**（`api.py`，`app = create_app()`；`create_app(*, resource_config=None, harness_factory=create_harness)` 可注入测试用的 resource 配置与 Brain 工厂）：
  - `POST /upload` —— multipart `files[]`，支持一个或多个文件，返回 `{files:[{file_path,name,mime_type,size}]}`
  - `POST /runs` —— body `{messages, session_id?}`，立即返回 `{run_id, session_id, status:"queued"}`
  - `GET  /runs/{run_id}?after_event_id=N` —— 返回 `{run, events[], latest_content_event}`，未知 run 返回 `404`
  - 默认由 `scripts/start-backend.bat` 拉起：`uv run uvicorn api:app --host 0.0.0.0 --port 8500 --reload`（端口与 `tests/test_real_image_run.py` 的 `DEFAULT_BASE_URL` 一致）
- **测试脚本**：按影响范围从 `backend/` 目录运行对应脚本，例如 `python -m tests.test_api`、`python -m tests.test_harness`。
- **程序内**：无单函数 one-shot 入口；需显式组合 `AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(messages, session_id, run_id)`。

## 5. 测试位置

`backend/tests/` 是当前测试源码目录，断言分布在：

- `test_tools.py`：`parse_documents` env guard、`/artifacts/...` 路径解析、`/tasks` form 默认 content_list JSON 参数、Markdown/图片请求自动全量 ZIP 参数、单/多文件保存 task 级 JSON/ZIP、`extract_archives` 解压并返回文件清单、部分失败/链路失败
- `test_run_ledger.py`：`input_messages_json`、事件投影、大 payload 外溢、启动恢复
- `test_harness.py`：FakeBrain、ToolStatusMiddleware、`execute_run(messages, ...)`、artifact block 归一化、`assistant_message` 的最终 `thinking` 载荷
- `test_api.py`：`POST /upload`、`POST /runs` 新契约、`latest_content_event`、`assistant_message.thinking`、并发冲突、失败后续跑、启动恢复
- `test_real_image_run.py`：手动真实图片 HTTP 集成测试
- `test_real_multi_pdf_run.py`：手动真实 HTTP / 模型 / MinerU 集成脚本（确认 agent 会用 `parse_documents` 解析上传 PDF，调用次数与输出格式都由用户请求和 agent 策略决定）

当前仍**不是 pytest 套件**；没有总控 runner，回归按影响范围直接运行对应 `test_*.py` 脚本。

## 6. `.planning/` 角色

`backend/.planning/codebase/` 是**子项目级文档事实层**，存放当前后端的持久化事实文档（codebase maps），由 `$gsd-map-codebase` 流程刷新：

- `ARCHITECTURE.md`（系统边界与数据流）、`STRUCTURE.md`（本文档）、`CONVENTIONS.md`、`CONCERNS.md`、`INTEGRATIONS.md`、`STACK.md`、`TESTING.md`。
- 这些文档是**根级 `coding_maps/`、`AGENTS.md` 的上游事实源**：上层地图与导航规则按它们聚合，不直接读源码。
- 它们描述“当前代码到底是什么”，而不是“应该是什么”；源码与文档不一致时以源码为准，并刷新对应文件。
