# ARCHITECTURE

> 事实来源：backend/ 源码与 backend/pyproject.toml（2026-07-02 生成）

## 1. 系统目的

`backend/` 是一个 Harness 级的 agent 运行时底座。它的目标是把 `Session` / `Harness` / `Hands` / `Resources` / `Tools` 固化为五个稳定模块边界，让能力（Brain、执行器、工具）可以插拔，而不被硬编码到某个 runner、容器、模型或工作流里。当前里程碑交付的是最小可运行的 DeepAgents 解析演示：一个通用文档解析工具 + 一个 DeepAgents 工厂 + 一个 `CompositeBackend` 配置 + 一个最小 session runner。

## 2. 模块形态与导入

`backend/` **不是常规 Python 包**，没有 `__init__.py`，也没有 `backend/__main__.py`。它在 `backend/pyproject.toml` 中以**扁平顶层模块**（`[tool.setuptools] package-dir = {"" = "."}`，`py-modules = ["hands","harness","resources","session","tools","self_check"]`）的形式声明：`backend/` 内的每个 `.py` 文件直接作为顶层模块安装，因此模块之间用**绝对导入**（`from hands import ...`、`from session import ...`、`from resources import ...`、`from tools import ...`），**不带** `backend.` 前缀。

`.env` 由 `backend/session.py:15` 在导入时加载：`load_dotenv(Path(__file__).with_name(".env"))`（任何触发 `session` 模块导入的路径都会执行一次）。`session.py` 还 `import argparse`（`session.py:3`），但 `main()` 并未使用它，属于遗留未使用 import。

## 3. 五大模块边界

| 模块 | 文件 | 核心职责 | 公开接口（类/函数） | 依赖关系 |
|------|------|----------|----------------------|----------|
| **Session** | `backend/session.py` | 以 append-only 事件存完整持久任务事实；从历史派生上下文窗口（不等于上下文窗口本身） | `SessionStore`（Protocol）、`SqliteSessionStore`、`SessionRecord`、`SessionEvent`、`ContextWindow`、`run_session`、`main` | 标准库 `sqlite3`；被 Harness / Hands / Resources 依赖 |
| **Harness** | `backend/harness.py` | 读 Session 历史 → 派生上下文 → 请求 Brain 执行 → 写回事件；保持薄 | `Brain`（Protocol）、`BrainFactory`（Protocol）、`DeepAgentsBrainFactory`、`HarnessRuntime`、`HarnessTurn`、`create_harness` | 依赖 Session、Hands、Resources、Tools；调用 `deepagents`、`langchain`、`langgraph` |
| **Hands** | `backend/hands.py` | 通过 middleware 暴露模型/工具执行 trace，并把真实错误透传 | `Hands`（Protocol）、`TraceHands`、`TraceMiddleware` | 依赖 Session（emit_event）；调用 `langchain.agents.middleware`、`langgraph.types` |
| **Resources** | `backend/resources.py` | 持有持久存储（SQLite store/checkpointer）、检查点、产物路径、`CompositeBackend` 路由 | `ResourceConfig`、`AgentResources` | 依赖 Session（`SqliteSessionStore`）；调用 `deepagents.backends`、`langgraph.checkpoint.sqlite`、`langgraph.store.sqlite` |
| **Tools** | `backend/tools.py` | 暴露可调用能力，不绑定单一 runner | `ToolCatalog`、`ToolHandler`、`parse_document`、`default_tool_catalog` | 标准库 + `requests`；被 Harness 注入 |

> 注：`self_check.py` 是端到端自检脚本，不属于五大边界，是验证入口。DeepAgents 在本仓库是**可插拔的 Brain / 子 Harness**，由 `BrainFactory` Protocol 注入，`self_check.py` 用 `_FakeBrainFactory` 证明其可被替换。

## 4. 运行时数据流

一次完整运行（入口 `run_session` → `HarnessRuntime.run_turn`）：

```
run_session(message, session_id)
  └─ with AgentResources() 装配资源 (SQLite store/checkpointer + CompositeBackend)
     └─ create_harness(resources).run_turn(message, session_id)
        │
        ① resources.sessions.ensure_session(session_id)            # 确保会话存在
        ② sessions.emit_event(session_id, "user_message", {...})  # 写入用户事件
        ③ context = sessions.context_window(session_id)           # 从历史派生上下文窗口
              （SqliteSessionStore 取最近 CONTEXT_MESSAGE_LIMIT=20 条 user/assistant 事件，
               并裁剪到首个 user 起始）
        ④ brain = brain_factory.create(resources, middleware, tools, session_id)
              └─ TraceHands.middleware() → [TraceMiddleware]
        ⑤ result = brain.invoke(
              {"messages": [RemoveMessage(REMOVE_ALL_MESSAGES), *context.messages]},
              config={"configurable": {"thread_id": session_id}})   # 请求执行
              │
              │  执行期间 TraceMiddleware 透传 trace 并写回事件：
              │    wrap_model_call → emit_event("model_request" / "model_response" / "model_error")
              │    wrap_tool_call  → emit_event("tool_request"  / "tool_response"  / "tool_error")
              │  （异常时先 emit 对应 *_error 事件，再 raise 透传）
        ⑥ sessions.emit_event(session_id, "assistant_message", {...})  # 写回助手事件
        ⑦ return HarnessTurn(session_id, context, result)
```

要点：上下文窗口（步骤③）是从 append-only 事件历史**派生**出来的视图，派生前先写入了用户事件（步骤②），执行 trace 由 Hands 的 middleware 产生（步骤⑤内的 emit），最终助手回复再写回事件（步骤⑥）。`brain.invoke` 前用 `RemoveMessage(REMOVE_ALL_MESSAGES)` 重置 langgraph 内部消息，再用 Session 派生的上下文重建——这是 Session 作为"单一事实源"而非 langgraph thread 状态的具体体现。

## 5. 关键设计决策

- **Append-only 事件**：`SqliteSessionStore.emit_event` 只做 `insert`，从不 update/delete；事件表带自增 `event_id` 与 `(session_id, event_id)` 索引。**为什么**：保证任务事实可审计、可回放；任何派生视图（上下文、摘要）都可从原始事件重建，不会因修改而丢失历史。
- **Session ≠ 上下文窗口**：`context_window()` 只是把事件投影成最近 20 条 user/assistant 消息；原始事件仍是事实源。**为什么**：避免把"给模型看的裁剪视图"当成真相，摘要/裁剪可以丢失细节但不能替代 raw events（见根 AGENTS.md 明确约束）。
- **可恢复事件**：超大 payload（> `max_inline_bytes=262144`，即 256KiB）自动外溢到 `data/artifacts/session-events/<uuid>.json`，事件行只存指针 `{artifact_path, bytes}`，读取时透明回填。**为什么**：让事件表保持轻量可索引，同时不丢失大体积工具结果/模型日志，崩溃后仍可恢复。
- **薄 Harness**：`HarnessRuntime.run_turn` 只有 6 个步骤、无服务层/工作流引擎/策略框架。**为什么**：遵循根 AGENTS.md 的简洁约束——只有真实 caller 需要时才增加抽象，每个新抽象必须保护五大边界之一。
- **真实错误透传**：`TraceMiddleware.wrap_model_call` / `wrap_tool_call` 在 `except` 中 `emit_event(*_error)` 后 `raise`，`self_check.py` 显式断言错误必须被透传。**为什么**：错误是事实的一部分，必须可审计且不被吞掉；调用方能拿到真实异常而非被包装失真。
- **Tools 不绑定 runner**：`ToolCatalog` 只是一个 `tuple[ToolHandler]`，`as_list()` 转成 list 注入 Brain；工具与 Harness/runner 解耦。**为什么**：工具能力可被任意 Brain 复用，不绑定到 DeepAgents 单一 runner。
- **数据目录锁定在 `backend/` 下**：`resources.py` 用 `_BACKEND_DIR = Path(__file__).resolve().parent`（`resources.py:14`）把 `data_dir` 固定为 `backend/data/`，与运行时 CWD 无关。**为什么**：脚本可从任意工作目录运行，资源路径始终稳定。

## 6. 首个里程碑范围与实现状态

| 里程碑项 | 实现位置 | 状态 |
|----------|----------|------|
| 一个通用文档解析工具 | `backend/tools.py::parse_document` + `default_tool_catalog()` | 已实现：模型可见工具名为 `parse_document`，内部当前仍走 MinerU `POST /tasks` → 轮询 `GET /tasks/{id}` → 取 `GET /tasks/{id}/result`；调用时读取 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_EFFORT` / `MINERU_TIMEOUT_SECONDS`，输出写 `backend/data/document_outputs/{stem}.md` |
| 一个 DeepAgents 工厂 | `backend/harness.py::DeepAgentsBrainFactory`（实现 `BrainFactory` Protocol） | 已实现：默认模型 `openai:MiniMax-M3`，从环境派生 `OPENAI_API_KEY` / `OPENAI_API_BASE` |
| 一个 `CompositeBackend` 配置 | `backend/resources.py::AgentResources.__enter__` | 已实现：`default=StateBackend()`；`/memories/`、`/conversation_history/`、`/logs/` 路由到 `StoreBackend`；`/artifacts/`、`/large_tool_results/` 路由到 `FilesystemBackend` |
| 一个最小 session runner | `backend/session.py::run_session` | 已实现：`with AgentResources(ResourceConfig()): create_harness(resources).run_turn(...).result` |

- **自检**：`backend/self_check.py` 用 `_FakeBrainFactory` / `_FakeBrain` 端到端验证 Harness 单轮/多轮、trace 事件、错误透传、超大 payload 外溢，结尾打印 `self-check passed`。可作 `python self_check.py` 或 `cd backend && python -m self_check` 运行。
- **`main()` 冒烟入口**：`session.py::main()`（`session.py:222`）硬编码 `message = "你好"` + 随机 `session_id`，调用 `run_session` 后打印最后一条消息内容。可用 `python session.py` 或 `cd backend && python -m session` 触发；因 `session.py` 顶部 `load_dotenv`，会读取 `backend/.env`。
