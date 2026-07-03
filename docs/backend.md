# backend 架构与约定（根级视角）

> 根级 AGENTS.md 的详情文档之一。本文是**根级视角的 backend 摘要**，便于快速建立整体认知；backend 内部完整事实（目录、模块、集成明细、约定、风险）以 `backend/.planning/codebase/` 为准，本文不复制底层细节。

## 五大模块边界

`backend/` 是 Harness 级 agent 运行时底座，五个稳定模块边界各自一文件：

| 模块 | 职责（一句话） |
|------|----------------|
| **Session** | 以 append-only 事件存完整持久任务事实；在同一 SQLite 边界追加 `runs`/`run_events` 供 HTTP run 查询；从历史派生上下文窗口（不等于上下文窗口本身） |
| **Harness** | 读 Session 历史 → 派生上下文 → 请求 Brain 执行 → 写回事件；同时提供 HTTP run 的统一 streaming 执行路径（`execute_run`）；保持薄 |
| **Hands** | 通过 middleware 暴露模型/工具执行 trace，并把真实错误透传 |
| **Resources** | 持有持久存储（SQLite store/checkpointer）、检查点、产物路径、`CompositeBackend` 路由 |
| **Tools** | 暴露可调用能力（如 `parse_document`），不绑定单一 runner |

> DeepAgents 在此仓库是**可插拔的 Brain / 子 Harness**，由 `BrainFactory` Protocol 注入，`self_check.py` 用 `_FakeBrain` 证明其可被替换。

## 运行时数据流（单轮）

`run_session` → `with AgentResources()` 装配资源（三 SQLite 库 + CompositeBackend）→ `create_harness(resources).run_turn()`：
① `ensure_session` → ② `emit_event("user_message")` → ③ `context_window`（派生最近 20 条 user/assistant）→ ④ `brain_factory.create`（注入 middleware + tools + 后端）→ ⑤ `brain.invoke`（Hands 的 middleware 透传 trace 并写回事件；模型可调 `parse_document`）→ ⑥ `emit_event("assistant_message")` → 返回 `HarnessTurn`。

要点：上下文窗口是从 append-only 事件历史**派生**的视图；`brain.invoke` 前用 `RemoveMessage(REMOVE_ALL_MESSAGES)` 重置 langgraph 内部消息再用 Session 派生上下文重建——Session 是"单一事实源"而非 langgraph thread 状态。完整调用链与字段细节见 `backend/.planning/codebase/ARCHITECTURE.md` §4 与 `coding_maps/SYSTEM_MAP.md` §3。

## HTTP run 模型（薄适配层）

`backend/api.py` 在 FastAPI `lifespan` 启动时装配一次共享 `AgentResources` / `HarnessRuntime`，存于 `app.state`，所有请求复用同一实例（而非每请求新建资源）。HTTP 入口以 **run 为中心**：

- 三种 Agent POST（`POST /sessions/messages` 阻塞、`POST /sessions/messages/stream` SSE、`POST /sessions/messages/runs` 后台）统一由传输层生成 `run_id`，写入 immutable `runs` 基表 + append-only `run_events`（首条 `queued`），再统一走 `HarnessRuntime.execute_run(...)`（内部经 `brain.stream(..., stream_mode=["messages","custom","values"], version="v2")`）。
- 查询：`GET /runs/{run_id}`（支持 `after_event_id` 游标增量拉取）、`GET /sessions/{session_id}/runs`（按创建时间倒序）。
- 上传：`POST /files` 落盘到 `backend/data/artifacts/uploads/`，返回虚拟路径 `/artifacts/uploads/...`。
- 并发：同一 `session_id` 同时只允许一个 Agent run（进程内 `threading.Lock`，冲突返回 409）；进程重启时遗留 `queued`/`running` 的 run 在 `lifespan` 启动期被追加 `failed("执行已中断，请重试")`。

> 6 个端点的完整请求/响应 JSON 形状与状态码见 `INTERFACES.md` §1.1 与 `backend/.planning/codebase/INTEGRATIONS.md`；HTTP run 流程图见 `backend/.planning/codebase/ARCHITECTURE.md` §4。

## 关键设计决策（为什么）

- **Append-only 事件**：保证任务事实可审计、可回放；任何派生视图都可从原始事件重建。
- **Run 基表 immutable + run 事件 append-only**：`runs` 只插一次基行，状态/回复/错误全部由 `run_events` 投影——既可查询当前状态，又不退回 mutable job row。
- **Session ≠ 上下文窗口**：避免把"给模型看的裁剪视图"当成真相。
- **可恢复事件**：超大 payload（> 256KiB）外溢到 artifacts，DB 存指针，崩溃后仍可恢复。
- **薄 Harness / 薄 HTTP 层**：只有真实 caller 需要时才增加抽象，每个新抽象必须保护五大边界之一；`api.py` 只做 transport 适配，不下沉成第二套业务层。
- **HTTP 只锁 session，不引入队列**：同一 session 进程内单飞（stdlib `threading.Lock`），刻意拒绝 Redis/作业队列/恢复器；进程重启后由启动清理补 `failed`。
- **真实错误透传**：middleware 在 `except` 中 emit `*_error` 后 `raise`，错误不被吞掉。
- **Tools 不绑定 runner**：`ToolCatalog` 是 `tuple[ToolHandler]`，工具可被任意 Brain 复用。

## 存储与 provider 边界（根级摘要）

- **三 SQLite 库**（均锁定在 `backend/data/`，与 CWD 无关）：会话事件库（append-only）、LangGraph Store（持久记忆/历史/日志）、LangGraph Checkpoint（线程状态）。详见 `INTERFACES.md` §1.5。
- **CompositeBackend 路由**：`/memories/`、`/conversation_history/`、`/logs/` → StoreBackend（SQLite）；`/artifacts/`、`/large_tool_results/` → FilesystemBackend（磁盘）；default → StateBackend（图状态）。
- **MiniMax LLM**：经 **Anthropic 兼容协议**接入，`DeepAgentsBrainFactory` 用 `init_chat_model(f"anthropic:{MINIMAX_MODEL}", ..., thinking={"type": "adaptive"})` 构造 `ChatAnthropic`，仅读 `MINIMAX_MODEL`/`MINIMAX_API_KEY`/`MINIMAX_BASE_URL`，**无默认值、无 fallback**；SSE 流式接口输出 `thinking_delta`。详见 `INTERFACES.md` §1.7。
- **文档解析（MinerU）**：`parse_document` 在调用时读 `MINERU_*` 四个 env，三步同步任务 API（提交 → 轮询 → 取结果）。详见 `INTERFACES.md` §1.1。

> 深入 backend 内部（命名约定、类型规范、错误处理、配置约定、持久化细节、测试现状、已知风险）请直接阅读 `backend/.planning/codebase/` 下对应文档。
