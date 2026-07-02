# 代码约定 (CONVENTIONS)

> 事实来源：backend/ 源码、backend/pyproject.toml + uv.lock（2026-07-02 本轮刷新：MiniMax 接线改为 Anthropic 协议，移除历史 `OPENAI_*` 直接赋值与 fallback）

本文档只记录代码中真实可见的约定，不发明规范。代码证据以 `文件:行` 或类/函数名标注。

## 1. 命名约定

| 对象 | 风格 | 证据 |
|------|------|------|
| 文件名 | 全小写 `snake_case`，模块名单数名词 | `session.py`、`harness.py`、`hands.py`、`resources.py`、`tools.py`、`self_check.py` |
| 类名 | `PascalCase` | `HarnessRuntime`、`DeepAgentsBrainFactory`、`TraceMiddleware`、`SqliteSessionStore`、`AgentResources`、`TraceHands` |
| 函数名 | `snake_case` | `run_turn`、`create_harness`、`emit_event`、`context_window`、`parse_document` |
| 常量 | `UPPER_SNAKE_CASE` | `DEFAULT_SYSTEM_PROMPT`、`SUCCESS_STATES`、`FAILURE_STATES`、`CONTEXT_MESSAGE_LIMIT`、`REMOVE_ALL_MESSAGES`(导入的常量) |
| 模块级私有常量 | 小写 `_` 前缀 | `_BACKEND_DIR`、`max_inline_bytes`（虽为常量但沿用小写默认风格） |
| 工厂函数 | `create_*` 前缀 | `create_harness`、`create_deep_agent`(三方) |
| 内部/私有 | 单下划线前缀 | `_submit_mineru_task`、`_wait_for_mineru_result`、`_extract_markdown`、`_default_output_path`、`_find_value`、`_required_env`、`_safe`、`_utcnow`、`_read_event`、`_reset_messages`、`_assistant_content`、`_json_or_text`、`_setup`、`_message_text` |
| 测试替身（self_check 内） | `_` 前缀的类 | `_FakeBrain`、`_FakeBrainFactory` |

所有模块顶部统一使用 `from __future__ import annotations`（每个 `.py` 第 1 行），使类型注解延迟求值。**没有** `__all__` 声明（`backend/` 不是常规包，没有 `__init__.py`）。

## 2. 模块组织（扁平顶层模块 + 一文件一职责）

`backend/` 不是常规 Python 包，没有 `__init__.py`。`pyproject.toml` 用 `[tool.setuptools] package-dir = {"" = "."}` + `py-modules = [...]` 把每个 `.py` 声明为**顶层模块**，因此模块间一律**绝对导入**（`from session import ...`、`from harness import ...` 等，不带 `backend.` 前缀）。五大边界按"一文件一职责"映射：

| 边界 | 文件 | 职责 | 关键符号 |
|------|------|------|----------|
| **Session** | `session.py` | 持久化的 append-only 事件存储、上下文窗口派生 | `SessionStore`(Protocol)、`SqliteSessionStore`、`SessionEvent`、`ContextWindow`、`run_session` |
| **Harness** | `harness.py` | 读 Session 历史 → 派生上下文 → 请求执行 → 写回事件 | `HarnessRuntime`、`HarnessTurn`、`Brain`/`BrainFactory`(Protocol)、`DeepAgentsBrainFactory`、`create_harness` |
| **Hands** | `hands.py` | middleware：暴露 model/tool 执行轨迹并透传真实错误 | `Hands`(Protocol)、`TraceHands`、`TraceMiddleware` |
| **Resources** | `resources.py` | 持有 store、checkpointer、CompositeBackend、产物路径 | `AgentResources`、`ResourceConfig` |
| **Tools** | `tools.py` | 可调用能力（不绑定到具体 runner） | `ToolCatalog`、`ToolHandler`、`parse_document`、`default_tool_catalog` |

- `self_check.py` 是自检/冒烟脚本，**不属于五大边界**，是验证入口。
- 无 `__init__.py`（无聚合导出层），无 `__main__.py`（无 `python -m backend`）。入口由 `python session.py` / `python self_check.py` 或 `cd backend && python -m session` / `python -m self_check` 提供（见 TESTING.md）。

依赖方向（import 关系）：`harness` → `{hands, resources, session, tools}`；`resources` → `session`；`hands` → `session`；`session.run_session` → 函数体内惰性 `from harness import ...` / `from resources import ...`（打破循环）；`self_check` → `{hands, harness, resources, session, tools}`。模块级无循环依赖（`session`→`harness`/`resources` 的引用被放在 `run_session` 函数体内惰性导入）。

## 3. 异步 / 并发模式

**当前为纯同步实现，无 async/await/asyncio。**

- 业务源文件中无 `async def` / `await` / `asyncio`。
- `HarnessRuntime.run_turn`（`harness.py`）为同步 `def`，直接调用 `brain.invoke(...)`。
- `parse_document`（`tools.py`）用同步 `requests` + `time.sleep(poll_interval_seconds)` 轮询当前文档解析 provider（私有 helper 仍命名为 MinerU）。
- MinerU 的 HTTP 任务 API（`POST /tasks` → 轮询 `GET /tasks/{id}` → `GET /tasks/{id}/result`）虽为"服务端异步任务"，但客户端用同步阻塞轮询实现，未引入 `asyncio`。
- LangGraph/SqliteSaver 的异步能力在当前代码中未被使用（用的是 `SqliteSaver.from_conn_string` 的同步上下文管理器形式，`resources.py`）。

> 注：DeepAgents/LangGraph 本身支持异步，若上层需要可未来引入；当前里程碑未使用。需确认未来是否转向 async。

## 4. 类型与数据结构

约定：**接口用 `typing.Protocol`，值对象用 `@dataclass(frozen=True)`，不用 pydantic / TypedDict。**

- **Protocol 接口**（结构化子类型，不强制继承）：
  - `Brain`、`BrainFactory`（`harness.py`）
  - `Hands`（`hands.py`）、`SessionStore`（`session.py`）
- **不可变数据类** `@dataclass(frozen=True)`：
  - `HarnessTurn`（`harness.py`）、`ResourceConfig`（`resources.py`）
  - `SessionRecord`、`SessionEvent`、`ContextWindow`（`session.py`）
  - `ToolCatalog`（`tools.py`，内含 `tuple[ToolHandler, ...]`）
- **类型别名 / 可调用类型**：`ToolHandler = Callable[..., Any]`（`tools.py`）。
- **事件结构**：`SessionEvent` 固定字段 `event_id / session_id / event_type / created_at / payload: Any / artifact_path: str | None`。`event_type` 为字符串（如 `user_message`、`assistant_message`、`model_request`、`model_response`、`tool_request`、`tool_response`、`model_error`、`tool_error`），`payload` 为任意可 JSON 序列化对象。
- **消息结构**：上下文消息为 `{"role": "user"|"assistant", "content": str}` 字典。
- 类型注解全量标注（含返回类型），用 PEP 604 联合 `X | None`、PEP 585 泛型 `list[...]` / `tuple[...]`。

## 5. 错误处理（透传真实错误）

核心原则（根 AGENTS.md）：**"真实错误透传"**，代码一致遵循。

- **创建错误**：用 Python 内置异常类型，携带可读上下文：
  - `FileNotFoundError`（`tools.py`）、`RuntimeError`（`tools.py` 多处）、`TimeoutError`（`tools.py`）、`KeyError`（`session.py`，未知 session）。
- **HTTP 错误**：直接 `response.raise_for_status()`，不包装、不吞掉。
- **middleware 透传**（Hands 的核心约定）：`TraceMiddleware.wrap_model_call` / `wrap_tool_call` 在 `except Exception` 中先 `emit_event(..., "model_error"|"tool_error", {"error": repr(exc)})` 记录，再 `raise` 原样抛出。**不捕获后丢弃、不转译为通用错误。**
- **契约验证**：`self_check.py` 明确断言 model/tool 错误必须被透传（否则 `raise AssertionError`）。
- JSON 解析容错：`_json_or_text`（`tools.py`）对非 JSON 响应回退为纯文本，但不掩盖上游错误状态。

## 6. 日志 / trace（middleware 记录什么、不碰什么）

`TraceMiddleware`（`hands.py`）是**唯一**的 trace 通道，遵循根 AGENTS.md 的"只记 model 可见信息，不碰隐藏思维链"。

**记录（以 Session 事件 append-only 落盘）的事件类型：**

| event_type | 触发点 | payload | 备注 |
|------------|--------|---------|------|
| `model_request` | `wrap_model_call` 入口 | `{"messages": request.messages}` | TraceMiddleware |
| `model_response` | 模型成功返回后 | `{"messages": response.result}` | TraceMiddleware |
| `model_error` | 模型抛异常 | `{"error": repr(exc)}` | TraceMiddleware，记后 re-raise |
| `tool_request` | `wrap_tool_call` 入口 | `{"name", "args"}` | TraceMiddleware |
| `tool_response` | 工具成功返回后 | `{"name", "result"}` | TraceMiddleware |
| `tool_error` | 工具抛异常 | `{"name", "error": repr(exc)}` | TraceMiddleware，记后 re-raise |
| `user_message` | `HarnessRuntime.run_turn` 入口 | `{"role":"user","content":...}` | 由 HarnessRuntime 发出 |
| `assistant_message` | turn 结束 | `{"role":"assistant","content":...}` | 由 HarnessRuntime 发出 |

**控制台输出**：仅 `print(f"[model] ...")` 和 `print(f"[tool] {name} completed")`。

**不记录**：隐藏思维链（hidden chain-of-thought）。middleware 只截取 model 可见的 `messages`，不解析/打印隐藏推理内容。

## 7. 配置约定

- **`.env` 加载**：
  - `backend/session.py:15` 在导入时 `load_dotenv(Path(__file__).with_name(".env"))`，即读取 `backend/.env`。
  - `backend/tools.py` 同样在导入时以相同方式加载 `.env`。
  - 任何触发 `session` / `tools` 模块导入的路径都会触发一次加载。**没有** `__init__.py`，因此不存在"导入 `backend` 包即加载 .env"的语义。

- **MiniMax 接线（Anthropic 协议，无默认、无 fallback）**：
  `DeepAgentsBrainFactory.__init__`（`harness.py`）当 `model` 为 `None` 时：
  - 仅通过 `os.getenv` 读取三个环境变量：`MINIMAX_MODEL`、`MINIMAX_API_KEY`、`MINIMAX_BASE_URL`。
  - 构造 `init_chat_model(f"anthropic:{os.getenv('MINIMAX_MODEL')}", api_key=os.getenv("MINIMAX_API_KEY"), base_url=os.getenv("MINIMAX_BASE_URL"))`，得到 LangChain `ChatAnthropic`。
  - MiniMax 通过其 **Anthropic 兼容端点**以 **Anthropic 协议**（非 OpenAI 协议）访问。
  - **无默认值、无 fallback**：commit `9c78cf2` 显式移除了 fallback，保持配置单一来源、对缺失配置 fail-fast。

  > **本轮更正（commits a30bb99 / 9c78cf2）**：以下历史写法均已废弃，请勿沿用——
  > - ❌ `MINIMAX_API_KEY` → `os.environ["OPENAI_API_KEY"] = api_key`（直接赋值覆盖）——**已废弃**。
  > - ❌ `MINIMAX_BASE_URL`（默认 `https://api.minimaxi.com/v1`）→ `os.environ["OPENAI_API_BASE"]`——**已废弃**。
  > - ❌ `MINIMAX_MODEL`（默认 `MiniMax-M3`）→ `openai:{model}`——**已废弃**。
  > - ❌ "为什么用直接赋值而非 setdefault（避免旧 OPENAI_API_KEY 冲突）"的整段理由——**已废弃**。
  > - ❌ 任何 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` fallback 的说法——**已废弃**。

- **当前文档解析 provider 配置**：
  - `parse_document(...)` 在调用时通过 `_required_env` 读取 `MINERU_BASE_URL`、`MINERU_BACKEND`、`MINERU_EFFORT`、`MINERU_TIMEOUT_SECONDS`（缺失抛 `RuntimeError`）。
  - 请求固定字段：`return_md="true"`、`response_format_zip="false"`；轮询间隔固定为内部默认 `poll_interval_seconds=2.0`。
  - `MINERU_TIMEOUT_SECONDS` 通过 `int(...)` 转换；缺失抛 `RuntimeError`，非法整数直接暴露原生 `ValueError`。

- **数据/产物路径**（`ResourceConfig`，`resources.py`，`data_dir = _BACKEND_DIR / "data"`，锁定在 `backend/data/`）：
  - 会话库 `backend/data/dsagents_sessions.db`
  - store 库 `backend/data/dsagents_store.db`
  - checkpoint 库 `backend/data/dsagents_checkpoints.db`
  - 产物目录 `backend/data/artifacts/`
  - 文档解析输出默认落 `backend/data/document_outputs/{stem}.md`（`tools.py::_default_output_path`）。

## 8. 持久化约定

- **append-only 事件**：`SqliteSessionStore.emit_event` 只 `insert`，从不 `update`/`delete`。表 `session_events` 自增主键 `event_id`。根 AGENTS.md："Session 不是上下文窗口；摘要/裁剪视图可作为事件追加，但不得替代原始事件。"
- **StateBackend vs StoreBackend(SQLite)**（根 AGENTS.md + `resources.py`）：
  - DeepAgents 文件系统默认走 `StateBackend()`（默认路由）。
  - 持久历史/记忆路由到 `StoreBackend(store=SqliteStore, namespace=("dsagents",))`，路径前缀 `/memories/`、`/conversation_history/`、`/logs/`。
  - 大产物/大工具结果路由到 `FilesystemBackend(root_dir=artifacts_dir, virtual_mode=True)`，路径前缀 `/artifacts/`、`/large_tool_results/`。
  - 三者组合为 `CompositeBackend(default=StateBackend(), routes={...})`。
- **checkpointer**：`SqliteSaver.from_conn_string(checkpoint_db)`，用于 LangGraph 线程状态。
- **大 payload 落盘**：`emit_event` 对超过 `max_inline_bytes` 的 payload，写入 `backend/data/artifacts/session-events/{uuid}.json`，DB 内只存 `{"artifact_path", "bytes"}` 占位。
- **资源生命周期**：`AgentResources` 是上下文管理器，用 `ExitStack` 管理 `SqliteStore`、`SqliteSaver` 的关闭。进入时 `mkdir` 数据/产物目录并 `setup()` 两库。
- **会话存在性**：`emit_event` / `ensure_session` 用 `insert or ignore` 保证幂等创建。
