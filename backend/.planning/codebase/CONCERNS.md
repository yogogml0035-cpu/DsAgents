# CONCERNS — backend 风险、技术债、陷阱与护栏

> 事实来源：仅代码与 `AGENTS.md`（根目录）。未确认项标注「当前代码未确认」。

## 1. 里程碑范围纪律（防范围蔓延）

- First Milestone 定义（`AGENTS.md` 第 23-30 行）：最小可运行 DeepAgents demo = 一个 MinerU 解析工具 + 一个 DeepAgents 工厂 + 一个 `CompositeBackend` 配置 + 一个最小 session runner。
- 禁止范围（`AGENTS.md` 第 32 行 / 第 21 行）：service layer、container setup、auth system、policy framework、workflow engine，在「真实调用方需要」之前一律不加。
- Simplicity Constraint（`AGENTS.md` 第 44-46 行）："Prefer deleting scope over adding knobs." 每个新抽象必须保护五个模块边界之一，否则删除。AI 编码代理易犯「过早抽象」陷阱，新增任何类/工厂/配置层前需自问是否守护五边界。
- 当前实现贴合里程碑：`default_tool_catalog()`（`backend/tools.py:133-134`）仅注册 `parse_document_with_mineru`；`DeepAgentsBrainFactory`（`backend/harness.py:39-60`）是单一工厂；session runner 为 `run_session`（`backend/session.py:209-215`）。

## 2. 五个稳定边界（不得硬编码能力到单一 runner/container/model/workflow）

- 五边界见 `AGENTS.md` 第 11-17 行：`Session`（append-only 事件）、`Harness`（读历史→派生上下文→请求执行→写回事件）、`Hands`（暴露执行 trace + 透传真实错误）、`Resources`（持久化 store/checkpointer/artifact 路径）、`Tools`（可调用能力，不绑定单一 runner）。
- 护栏实现：`Brain` / `BrainFactory` 为 `Protocol`（`backend/harness.py:24-36`），允许替换实现；`default_tool_catalog` 通过 `ToolCatalog` 元组组合（`backend/tools.py:18-23`），而非硬编码进 runner。
- 陷阱：AI 代理若把 MinerU URL、模型名或某 backend 直接连进 `HarnessRuntime.run_turn`（`backend/harness.py:84-109`），即违反此边界。

## 3. Session 不是上下文窗口（原始事件为唯一真相）

- `AGENTS.md` 第 19 行明文："Session is not the context window. Summaries or trimmed views may be appended as events, but they must not replace raw events as the source of truth."
- `SqliteSessionStore` 全部 append-only：`emit_event` 只 `insert`，从不 `update`/`delete`（`backend/session.py:110-144`）。`context_window`（`backend/session.py:146-159`）是只读派生视图（取最后 20 条 + 裁到首个 user），原始事件不被替换。
- 陷阱：勿把摘要写成对历史的覆盖；勿把 `context_window` 的截断误当「清理」。`CONTEXT_MESSAGE_LIMIT = 20`（`backend/session.py:50`）仅影响派生视图，不影响事件表。

## 4. MinerU 运行时耦合（网络/主机可用性风险）

- 硬依赖 `MINERU_BASE_URL = "http://10.11.0.110:6006"`（`backend/tools.py:12`），经 `POST /tasks` → 轮询 `GET /tasks/{task_id}` → `GET /tasks/{task_id}/result`（`backend/tools.py:60-97`）。该主机不可达即整链路失败，无降级/fallback。
- 固定参数 `backend=hybrid-engine`、`effort=high`（`backend/tools.py:67-68`），`AGENTS.md` 第 37 行确认本里程碑不可由用户配置。任何「参数化」修改都违反里程碑约束。
- 轮询超时 `timeout_seconds=900`、`poll_interval_seconds=2.0`（`backend/tools.py:29-30`）为默认值，超时抛 `TimeoutError`（`backend/tools.py:97`）。

## 5. 持久化存储的可移植性假设（本地 SQLite + 文件系统）

- 持久层为本地 SQLite `.db` 文件：`dsagents_sessions.db` / `dsagents_store.db` / `dsagents_checkpoints.db`（`backend/resources.py:19-28`），`data_dir` 默认 `Path("data")`（`backend/resources.py:16`）——相对路径假设进程在仓库根运行。
- 大 artifact 与大日志落文件系统于 `data/artifacts/`（`AGENTS.md` 第 40 行；`backend/resources.py:31-32`）。`SqliteSessionStore` 对 >`max_inline_bytes`(262144) 的 payload 溢出到 `artifacts_dir/"session-events"`（`backend/session.py:117-124`）。
- 陷阱：`artifact_path` 以绝对字符串写入 DB（`backend/session.py:119-120`），跨主机/移动 `data` 目录后该路径失效；`_read_event` 仍按该绝对路径回读（`backend/session.py:161-175`）。

## 6. 错误传播规则（真实错误必须穿透，不得吞掉）

- `AGENTS.md` 第 15 行：`Hands` "pass real errors through"。`TraceMiddleware.wrap_model_call` 与 `wrap_tool_call` 均 `try/except ... raise`，先 `emit_event("model_error"/"tool_error")` 再重新抛出（`backend/hands.py:38-45, 58-66`）。
- 工具侧 `parse_document_with_mineru` 用 `response.raise_for_status()`（`backend/tools.py:74, 87, 94`），失败状态抛 `RuntimeError`，超时抛 `TimeoutError`，均不捕获吞没。
- `self_check.py` 显式断言此规则：第 69 行 `"model errors must be passed through"`、第 79 行 `"tool errors must be passed through"`——回归护栏。陷阱：勿在 `wrap_*_call` 中包一层 `except: return` 吞错误。

## 7. 日志护栏（绝不打印/持久化隐藏思维链）

- `AGENTS.md` 第 42 行：Middleware 只可记录 model-visible messages、tool calls、tool results、final answers；不得打印/持久化 hidden chain-of-thought。
- `TraceMiddleware` 只发 `model_request`/`model_response`/`tool_request`/`tool_response`/`model_error`/`tool_error` 事件（`backend/hands.py:37, 41, 43, 53-56, 60-65, 67-71`），`print` 仅输出模型末条内容与工具名（`backend/hands.py:44, 72`）。当前未观察到 CoT 持久化——但具体「hidden CoT」字段是否随 `request.messages` 整体入库，当前代码未确认（事件 payload 含整个 messages 列表，存在过度记录风险）。

## 8. 隐藏依赖：DeepAgents 默认虚拟文件系统

- `AGENTS.md` 第 41 行明文："The built-in DeepAgents virtual filesystem is used; do not add another virtual filesystem wrapper."
- `AgentResources` 中 `FilesystemBackend(..., virtual_mode=True)`（`backend/resources.py:53`）即该内置虚拟文件系统，`CompositeBackend` 路由 `/artifacts/`、`/large_tool_results/` 指向它（`backend/resources.py:54-63`）。陷阱：AI 代理勿另起一层自己的虚拟 FS 抽象。

## 9. TODO / FIXME / 占位代码

- 对 `backend/` 全量 `grep`（`TODO|FIXME|XXX|HACK|placeholder|NotImplemented`）：**未发现任何标记或占位实现**。当前代码无 TODO/FIXME 债务。
- 注：`self_check.py` 是运行时自检脚本而非测试框架产物（`if __name__ == "__main__": main()`，`backend/self_check.py:109-110`），`_FakeBrain`/`_FakeBrainFactory`（第 14-23 行）是自检专用桩，非生产占位。

## 10. AI 编码代理易踩的具体陷阱

- 误改五模块：把 MinerU URL/模型名/超时写死进 `HarnessRuntime` 或 `run_turn`，破坏 Tools/Harness 边界。
- 过早抽象：在只有一个工具时引入工具注册中心、插件系统或配置 schema，违反 Simplicity Constraint。
- 把 `context_window` 的截断当「清理」操作，误删原始事件（违反第 3 节）。
- 移动/重命名 `data/` 后忽略 `artifact_path` 绝对路径失效（第 5 节）。
- 在 `wrap_model_call`/`wrap_tool_call` 加 `try/except` 吞错误，破坏第 6 节透传规则。
- 在 TraceMiddleware 中 `print` 或 `emit_event` 模型内部 CoT，违反第 7 节日志护栏。
- 引入第二套虚拟文件系统（第 8 节）。
- 为 MinerU 参数加「可配置」开关，违反 First Milestone 固定参数约束（第 4 节 / `AGENTS.md` 第 37 行）。
