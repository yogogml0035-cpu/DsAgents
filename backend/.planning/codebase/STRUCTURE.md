# STRUCTURE

> 事实来源：当前 `backend/` 源码（run-first runtime）。
> 本轮刷新（2026-07-08）已核对当前 HEAD：`349357b`（最终 `assistant_message.payload.thinking`）、`2206b1a`（harness 事件规范化）、`c8cc563`（run-ledger 时区统一与迁移）、`bc383ac`（测试端口配置）。

## 1. 顶层模块组织

`backend/` **不是包**，是扁平顶层模块（flat top-level modules）。当前 `.py` 文件：

| 模块 | 职责（一两句话） |
|------|------------------|
| `api.py` | FastAPI run-first HTTP 层：`POST /runs`、`GET /runs/{run_id}`、`POST /upload`；run 后台线程调度、同 session 并发保护、启动恢复、多文件上传落盘 |
| `harness.py` | run 执行核心：`HarnessRuntime.execute_run` 装配 Brain/Hands/Tools 并把 `brain.stream(...)` 的 chunk 规范化为 `RunEvent`；含 `create_harness` 默认工厂与 `DeepAgentsBrainFactory`；从最终 AIMessage 提取 `assistant_message.payload.thinking` |
| `hands.py` | 执行器抽象：`Hands` Protocol + 默认 `ToolStatusHands`/`ToolStatusMiddleware`（在工具调用前后发 `tool_status` custom event） |
| `resources.py` | 资源装配：`AgentResources`（context manager）与 `ResourceConfig`；装配 run ledger、LangGraph store、LangGraph checkpointer、`CompositeBackend` |
| `run_ledger.py` | SQLite run ledger：`SqliteRunLedger` 维护 `runs`/`run_events` 表，支持状态投影、增量事件查询、大 payload 外溢、启动恢复 |
| `tools.py` | 工具定义：`ToolCatalog`/`ToolHandler` 抽象 + 默认业务工具 `parse_documents`（一次调 MinerU 批量解析文档为 markdown） |

`backend/dsagents.egg-info/` 当前仍被 git 跟踪，但它是 setuptools 生成元数据，不是运行入口；修改依赖或 `py-modules` 时容易出现机械 churn。

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
py-modules = ["api", "hands", "harness", "resources", "run_ledger", "tools"]
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
│   └── test_tools.py
├── pyproject.toml          # package-dir=""  py-modules=[...]
├── uv.lock
└── data/                   # 固定数据目录（ResourceConfig.data_dir，与 CWD 无关）
    ├── dsagents_runs.db            # run ledger；首次创建 run/进入 `AgentResources` 时按需生成
    ├── dsagents_checkpoints.db     # LangGraph checkpointer（按需生成）
    ├── dsagents_store.db           # LangGraph store（按需生成）
    ├── artifacts/
    │   ├── downloads/              # parse_documents 输出目录（<artifact-stem>_<timestamp>.md，按需创建）
    │   ├── run-events/             # run 事件大 payload 外溢（*.json，按需创建）
    │   └── uploads/                # POST /upload 上传落地点；首次写入时创建
```

> 数据目录路径由 `ResourceConfig`（`resources.py`）决定，固定指向 `backend/data/`，不受进程 CWD 影响。
> 代码约定的路径不等于文件一定已经存在：干净工作区里 `dsagents_runs.db` 与 `artifacts/uploads/` 可能尚未创建，只有在对应运行路径真正发生写入后才会出现。

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

- `test_tools.py`：`parse_documents` env guard、`/artifacts/...` 路径解析、批量提交/部分失败/链路失败
- `test_run_ledger.py`：`input_messages_json`、事件投影、大 payload 外溢、启动恢复
- `test_harness.py`：FakeBrain、ToolStatusMiddleware、`execute_run(messages, ...)`、artifact block 归一化、`assistant_message` 的最终 `thinking` 载荷
- `test_api.py`：`POST /upload`、`POST /runs` 新契约、`latest_content_event`、`assistant_message.thinking`、并发冲突、失败后续跑、启动恢复
- `test_real_image_run.py`：手动真实图片 HTTP 集成测试

当前仍**不是 pytest 套件**；没有总控 runner，回归按影响范围直接运行对应 `test_*.py` 脚本。
