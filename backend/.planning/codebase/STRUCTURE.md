# STRUCTURE

> 事实来源：当前 `backend/` 源码（run-first runtime）。
> 本轮刷新已核对最近相关提交：`5329588`、`b6bc3f3`、`3055899`。

## 1. 顶层模块组织

`backend/` **不是包**，是扁平顶层模块（flat top-level modules）。当前 `.py` 文件：

| 模块 | 职责（一两句话） |
|------|------------------|
| `api.py` | FastAPI run-first HTTP 层：`POST /runs`、`GET /runs/{run_id}`、`POST /files`；run 后台线程调度、同 session 并发保护、启动恢复 |
| `harness.py` | run 执行核心：`HarnessRuntime.execute_run` 装配 Brain/Hands/Tools 并把 `brain.stream(...)` 的 chunk 规范化为 `RunEvent`；含 `create_harness` 默认工厂与 `DeepAgentsBrainFactory` |
| `hands.py` | 执行器抽象：`Hands` Protocol + 默认 `ToolStatusHands`/`ToolStatusMiddleware`（在工具调用前后发 `tool_status` custom event） |
| `resources.py` | 资源装配：`AgentResources`（context manager）与 `ResourceConfig`；装配 run ledger、LangGraph store、LangGraph checkpointer、`CompositeBackend` |
| `run_ledger.py` | SQLite run ledger：`SqliteRunLedger` 维护 `runs`/`run_events` 表，支持状态投影、增量事件查询、大 payload 外溢、启动恢复 |
| `tools.py` | 工具定义：`ToolCatalog`/`ToolHandler` 抽象 + 默认业务工具 `parse_document`（调 MinerU 解析文档为 markdown） |
| `self_check.py` | 端到端自检脚本：用 `_FakeBrain`/`_FakeBrainFactory` 替身跑通 resources/ledger/middleware/harness/api/启动恢复/虚拟 artifacts |

已删除（本次重构）：

- `session.py`（旧 session 模块）
- `tests/test_stream_typing.py`

无 `__init__.py`、无 `__main__.py`。

## 2. 绝对导入约定

`pyproject.toml`：

```toml
[tool.setuptools]
package-dir = {"" = "."}
py-modules = ["api", "hands", "harness", "resources", "run_ledger", "tools", "self_check"]
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
├── self_check.py
├── pyproject.toml          # package-dir=""  py-modules=[...]
├── uv.lock
└── data/                   # 固定数据目录（ResourceConfig.data_dir，与 CWD 无关）
    ├── dsagents_runs.db            # run ledger；首次创建 run/进入 `AgentResources` 时按需生成
    ├── dsagents_checkpoints.db     # LangGraph checkpointer（按需生成）
    ├── dsagents_store.db           # LangGraph store（按需生成）
    ├── artifacts/
    │   ├── run-events/             # run 事件大 payload 外溢（*.json，按需创建）
    │   └── uploads/                # POST /files 上传落地点；首次上传时创建
    └── document_outputs/           # parse_document 默认输出目录（<stem>.md，按需创建）
```

> 数据目录路径由 `ResourceConfig`（`resources.py`）决定，固定指向 `backend/data/`，不受进程 CWD 影响。
> 代码约定的路径不等于文件一定已经存在：干净工作区里 `dsagents_runs.db` 与 `artifacts/uploads/` 可能尚未创建，只有在对应运行路径真正发生写入后才会出现。

## 4. 对外入口

- **HTTP**（`api.py`，`app = create_app()`）：
  - `POST /runs` —— body `{message, session_id?}`，立即返回 `{run_id, session_id, status:"queued"}`
  - `GET  /runs/{run_id}?after_event_id=N` —— 返回 `{run, events[]}`，未知 run 返回 `404`
  - `POST /files` —— multipart 上传，返回虚拟路径 `/artifacts/uploads/<uuid>_<原名>`
- **自检**：推荐从仓库根运行 `python backend/self_check.py`；在 `backend/` 目录内运行 `python self_check.py` 也会走同一脚本，并在通过时打印 `self-check passed`。
- **程序内**：无单函数 one-shot 入口；需显式组合 `AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(message, session_id, run_id)`。

## 5. 测试位置

当前没有 `backend/tests/` 测试源码目录。唯一的“测试”是 `self_check.py`（基于 `TestClient` + `unittest.mock` 的内置端到端自检，非 pytest 套件）。`tests/test_stream_typing.py` 已在本次重构删除。
