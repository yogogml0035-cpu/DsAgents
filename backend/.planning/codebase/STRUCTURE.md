# STRUCTURE — backend 目录与模块结构

> 源根：`D:\AgentProject\DsAgents\backend\`。事实来自代码；`data/` 目录当前代码未确认在仓库中实际存在（运行时由 `AgentResources.__enter__` 创建，见 `backend/resources.py:41-42`）。

## 目录树（backend/）

```
backend/
├── __init__.py        # 包入口，re-export 公共 API（create_mineru_agent/create_mineru_harness/parse_document_with_mineru/run_session）
├── __main__.py        # CLI 入口，调用 session.main()
├── session.py         # Session 事件源 + CLI run_session/main + SqliteSessionStore
├── harness.py         # HarnessRuntime 编排 + Brain/BrainFactory Protocol + DeepAgentsBrainFactory
├── hands.py           # TraceHands + TraceMiddleware（model/tool trace 与错误透传）
├── resources.py       # AgentResources/ResourceConfig + CompositeBackend 装配
├── tools.py           # ToolCatalog + parse_document_with_mineru + MinerU HTTP 客户端
├── self_check.py      # 离线自检（FakeBrain，覆盖 Session/Hands/Harness/超大事件落盘）
└── .planning/codebase/  # 本文档目录（事实文档，非源码）
```

运行期目录（代码引用，运行时生成，仓库中当前代码未确认已提交）：
```
data/
├── dsagents_sessions.db     # SessionStore 的 SQLite（sessions/session_events 表）
├── dsagents_store.db        # SqliteStore（DeepAgents memories/history/logs 持久层）
├── dsagents_checkpoints.db  # SqliteSaver（DeepAgents checkpointer）
├── artifacts/               # FilesystemBackend 根 + 大事件落盘
│   └── session-events/*.json   # 超大事件 payload（backend/session.py:117-124）
└── mineru_outputs/*.md      # parse_document_with_mineru 默认输出（backend/tools.py:56-57）
```

## 文件 → 五大模块映射

| 文件 | 所属模块 | 关键事实 |
|---|---|---|
| `backend/__init__.py` | （包导出层） | 仅 5 行，`__all__` 暴露 4 个公共符号（`:1-5`） |
| `backend/__main__.py` | （入口层，归 Harness 调用链） | `from .session import main; main()`（`:1-3`） |
| `backend/session.py` | **Session** | `SessionStore` Protocol（`:37`）、`SqliteSessionStore`（`:49`）、事件类（`:14-34`）、`run_session`（`:209`）、`main`（`:218`） |
| `backend/harness.py` | **Harness** | `HarnessRuntime`（`:70`）、`run_turn`（`:84`）、`Brain`/`BrainFactory`（`:24`/`:28`）、`DeepAgentsBrainFactory`（`:39`） |
| `backend/hands.py` | **Hands** | `Hands` Protocol（`:14`）、`TraceHands`（`:18`）、`TraceMiddleware`（`:26`） |
| `backend/resources.py` | **Resources** | `AgentResources`（`:35`）、`ResourceConfig`（`:14`）、`CompositeBackend`（`:54`） |
| `backend/tools.py` | **Tools** | `ToolCatalog`（`:18`）、`parse_document_with_mineru`（`:26`）、`default_tool_catalog`（`:133`）、`MINERU_BASE_URL`（`:12`） |
| `backend/self_check.py` | （测试/校验，跨模块） | `_FakeBrain`/`_FakeBrainFactory`（`:14`/`:21`）、`main`（`:26`），不入五模块边界，属运行时校验 |

## 入口链

`python -m backend` → `backend/__main__.py:1` (`from .session import main`) → `backend/__main__.py:3` (`main()`) → `backend/session.py:218` `main()`（`argparse` 取 `message` 与 `--session-id`） → `backend/session.py:223` `run_session(args.message, args.session_id)` → `backend/session.py:209` `run_session` → 在 `AgentResources(ResourceConfig())` 上下文中（`:214`） → `create_mineru_harness(resources).run_turn(message, session_id).result`（`:215`）。

## data/ 目录约定（代码引用）

- 根 `data_dir = Path("data")`（`backend/resources.py:16`），三库 `dsagents_sessions.db` / `dsagents_store.db` / `dsagents_checkpoints.db`（`:19-28`），artifacts 根 `data/artifacts`（`:31-32`）。
- `AgentResources.__enter__` 创建 `data_dir` 与 `artifacts_dir`（`:41-42`），`SqliteSessionStore` 再建 `artifacts/session-events/`（`backend/session.py:56`）。
- 大 payload（`>max_inline_bytes`，默认 262144 字节）以 `{uuid4().hex}.json` 落盘，表中存 `{"artifact_path","bytes"}` stub（`backend/session.py:117-124`），读取时从 `artifact_path` 还原（`:161-175`）。
- `parse_document_with_mineru` 默认输出 `data/mineru_outputs/{stem}.md`（`backend/tools.py:56-57`）。
- 当前代码未确认 `data/` 已被提交进仓库（`ls backend/data` 在当前检出中不存在）；其为运行期生成产物。
