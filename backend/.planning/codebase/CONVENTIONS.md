# 代码约定 (CONVENTIONS)

> 事实来源：backend/ 源码、backend/pyproject.toml + uv.lock（2026-07-02 生成）

本文档只记录代码中真实可见的约定，不发明规范。代码证据以 `文件:行` 或类/函数名标注。

## 1. 命名约定

| 对象 | 风格 | 证据 |
|------|------|------|
| 文件名 | 全小写 `snake_case`，模块名单数名词 | `session.py`、`harness.py`、`hands.py`、`resources.py`、`tools.py`、`self_check.py` |
| 类名 | `PascalCase` | `HarnessRuntime`、`DeepAgentsBrainFactory`、`TraceMiddleware`、`SqliteSessionStore`、`AgentResources` |
| 函数名 | `snake_case` | `run_turn`、`create_mineru_harness`、`emit_event`、`context_window`、`parse_document_with_mineru` |
| 常量 | `UPPER_SNAKE_CASE` | `DEFAULT_MINIMAX_BASE_URL`、`MINERU_BASE_URL`、`SUCCESS_STATES`、`FAILURE_STATES`、`CONTEXT_MESSAGE_LIMIT` |
| 工厂函数 | `create_*` 前缀 | `create_mineru_agent`、`create_mineru_harness`、`create_deep_agent`(三方) |
| 内部/私有 | 单下划线前缀 | `_submit_task`、`_wait_for_result`、`_extract_markdown`、`_safe`、`_utcnow`、`_default_output_path`、`_read_event`、`_assistant_content`、`_reset_messages`、`_message_text`、`_json_or_text`、`_find_value`、`_setup` |
| 测试替身（self_check 内） | `_` 前缀的类 | `_FakeBrain`、`_FakeBrainFactory` |

所有模块顶部统一使用 `from __future__ import annotations`（每个 `.py` 第 1 行），使类型注解延迟求值。**没有** `__all__` 声明（`backend/` 不是常规包，没有 `__init__.py`）。

## 2. 模块组织（扁平顶层模块 + 一文件一职责）

`backend/` 不是常规 Python 包，没有 `__init__.py`。`pyproject.toml` 用 `[tool.setuptools] package-dir = {"" = "."}` + `py-modules = [...]` 把每个 `.py` 声明为**顶层模块**，因此模块间一律**绝对导入**（`from session import ...`、`from harness import ...` 等，不带 `backend.` 前缀）。五大边界按"一文件一职责"映射：

| 边界 | 文件 | 职责 | 关键符号 |
|------|------|------|----------|
| **Session** | `session.py` | 持久化的 append-only 事件存储、上下文窗口派生 | `SessionStore`(Protocol)、`SqliteSessionStore`、`SessionEvent`、`ContextWindow`、`run_session` |
| **Harness** | `harness.py` | 读 Session 历史 → 派生上下文 → 请求执行 → 写回事件 | `HarnessRuntime`、`HarnessTurn`、`Brain`/`BrainFactory`(Protocol)、`DeepAgentsBrainFactory`、`create_mineru_harness` |
| **Hands** | `hands.py` | middleware：暴露 model/tool 执行轨迹并透传真实错误 | `Hands`(Protocol)、`TraceHands`、`TraceMiddleware` |
| **Resources** | `resources.py` | 持有 store、checkpointer、CompositeBackend、产物路径 | `AgentResources`、`ResourceConfig` |
| **Tools** | `tools.py` | 可调用能力（不绑定到具体 runner） | `ToolCatalog`、`ToolHandler`、`parse_document_with_mineru`、`default_tool_catalog` |

- `self_check.py` 是自检/冒烟脚本，不属于五大边界，是验证入口。
- 无 `__init__.py`（无聚合导出层），无 `__main__.py`（无 `python -m backend`）。入口由 `python session.py` / `python self_check.py` 或 `cd backend && python -m session` / `python -m self_check` 提供（见 TESTING.md）。

依赖方向（import 关系）：`harness` → `{hands, resources, session, tools}`；`resources` → `session`；`hands` → `session`；`session.run_session` → 函数内惰性 `from harness import ...` / `from resources import ...`（避免模块级循环）；`self_check` → `{hands, harness, resources, session, tools}`。模块级无循环依赖（`session`→`harness` 的引用被放在 `run_session` 函数体内惰性导入）。

## 3. 异步 / 并发模式

**当前为纯同步实现，无 async/await。**

- 业务源文件中无 `async def` / `await` / `asyncio`。
- `HarnessRuntime.run_turn`（`harness.py:91`）为同步 `def`，直接调用 `brain.invoke(...)`。
- `parse_document_with_mineru`（`tools.py:26`）用同步 `requests` + `time.sleep(poll_interval_seconds)` 轮询 MinerU（`tools.py:96`）。
- MinerU 的 HTTP 任务 API（`POST /tasks` → 轮询 `GET /tasks/{id}` → `GET /tasks/{id}/result`）虽为"异步任务"，但客户端用同步轮询实现，未引入 `asyncio`。
- LangGraph/SqliteSaver 的异步能力在当前代码中未被使用（用的是 `SqliteSaver.from_conn_string` 的同步上下文管理器，`resources.py:50`）。

> 注：DeepAgents/LangGraph 本身支持异步，若上层需要可未来引入；当前里程碑未使用。需确认未来是否转向 async。

## 4. 类型与数据结构

约定：**接口用 `typing.Protocol`，值对象用 `@dataclass(frozen=True)`，不用 pydantic / TypedDict。**

- **Protocol 接口**（结构化子类型，不强制继承）：
  - `Brain`（`harness.py:26`）、`BrainFactory`（`harness.py:30`）
  - `Hands`（`hands.py:14`）、`SessionStore`（`session.py:41`）
- **不可变数据类** `@dataclass(frozen=True)`：
  - `HarnessTurn`（`harness.py:70`）、`ResourceConfig`（`resources.py:17`）
  - `SessionRecord`、`SessionEvent`、`ContextWindow`（`session.py:18/24/34`）
  - `ToolCatalog`（`tools.py:18`，内含 `tuple[ToolHandler, ...]`）
- **类型别名 / 可调用类型**：`ToolHandler = Callable[..., Any]`（`tools.py:15`）。
- **事件结构**：`SessionEvent` 固定字段 `event_id / session_id / event_type / created_at / payload: Any / artifact_path: str | None`（`session.py:24-31`）。`event_type` 为字符串（如 `user_message`、`assistant_message`、`model_request`、`model_response`、`tool_request`、`tool_response`、`model_error`、`tool_error`），`payload` 为任意可 JSON 序列化对象。
- **消息结构**：上下文消息为 `{"role": "user"|"assistant", "content": str}` 字典（见 `session.py:229-237` `_event_to_message`、`harness.py:93-96`）。
- 类型注解全量标注（含返回类型），用 PEP 604 联合 `X | None`、PEP 585 泛型 `list[...]` / `tuple[...]`。

## 5. 错误处理（透传真实错误）

核心原则（根 AGENTS.md）：**"真实错误透传"**，代码一致遵循。

- **创建错误**：用 Python 内置异常类型，携带可读上下文：
  - `FileNotFoundError`（`tools.py:35`）、`RuntimeError`（`tools.py:78/91/130`）、`TimeoutError`（`tools.py:97`）、`KeyError`（`session.py:87`，未知 session）。
- **HTTP 错误**：直接 `response.raise_for_status()`（`tools.py:74/87/94`），不包装、不吞掉。
- **middleware 透传**（Hands 的核心约定）：`TraceMiddleware.wrap_model_call` / `wrap_tool_call` 在 `except Exception` 中先 `emit_event(..., "model_error"|"tool_error", {"error": repr(exc)})` 记录，再 `raise` 原样抛出（`hands.py:40-42`、`hands.py:60-66`）。**不捕获后丢弃、不转译为通用错误。**
- **契约验证**：`self_check.py` 明确断言 model/tool 错误必须被透传（否则 `raise AssertionError`）。
- JSON 解析容错：`_json_or_text`（`tools.py:100-104`）对非 JSON 响应回退为纯文本，但不掩盖上游错误状态。

## 6. 日志 / trace（middleware 记录什么、不碰什么）

`TraceMiddleware`（`hands.py:26`）是唯一的 trace 通道，遵循根 AGENTS.md 的"只记 model 可见信息，不碰隐藏思维链"。

**记录（以 Session 事件 append-only 落盘）的事件类型：**
| event_type | 触发点 | payload | 代码位置 |
|------------|--------|---------|----------|
| `model_request` | `wrap_model_call` 入口 | `{"messages": request.messages}` | `hands.py:37` |
| `model_response` | 模型成功返回后 | `{"messages": response.result}` | `hands.py:43` |
| `model_error` | 模型抛异常 | `{"error": repr(exc)}` | `hands.py:41` |
| `tool_request` | `wrap_tool_call` 入口 | `{"name", "args"}` | `hands.py:53-57` |
| `tool_response` | 工具成功返回后 | `{"name", "result"}` | `hands.py:67-71` |
| `tool_error` | 工具抛异常 | `{"name", "error": repr(exc)}` | `hands.py:61-65` |
| `user_message` | `HarnessRuntime.run_turn` 入口 | `{"role":"user","content":...}` | `harness.py:93` |
| `assistant_message` | turn 结束 | `{"role":"assistant","content":...}` | `harness.py:111` |

**控制台输出**：仅 `print(f"[model] ...")`（`hands.py:44`）和 `print(f"[tool] {name} completed")`（`hands.py:72`）。

**不记录**：隐藏思维链（hidden chain-of-thought）。middleware 只截取 model 可见的 `messages`，不解析/打印隐藏推理内容。

## 7. 配置约定

- **`.env` 加载**：`backend/session.py:15` 在导入时 `load_dotenv(Path(__file__).with_name(".env"))`，即读取 `backend/.env`（该文件存在）。任何触发 `session` 模块导入的路径都会触发一次加载。**没有** `__init__.py`，因此不存在"导入 `backend` 包即加载 .env"的语义。
- **环境变量（MiniMax / OpenAI 兼容）**：`DeepAgentsBrainFactory.__init__`（`harness.py:42-50`）从环境读取，并以**直接赋值**写入 OpenAI 兼容变量：
  - `MINIMAX_API_KEY` → 若存在则 `os.environ["OPENAI_API_KEY"] = api_key`（**直接赋值、覆盖**已存在的旧值）。
  - `MINIMAX_BASE_URL`（默认 `https://api.minimaxi.com/v1`）→ `os.environ["OPENAI_API_BASE"] = ...`（直接赋值）。
  - `MINIMAX_MODEL`（默认 `MiniMax-M3`）→ 最终模型字符串 `openai:{model}`。
  - 为什么用直接赋值而非 `setdefault`：当系统环境变量里已预置旧的 `OPENAI_API_KEY`（如 DeepSeek 等）时，`setdefault` 不会覆盖，会导致 MiniMax 收到错误 key 而 401；直接赋值确保 MiniMax 用自己的 key 与 base url。
- **MinerU 固定参数**（根 AGENTS.md 规定不可由用户配置）：
  - `MINERU_BASE_URL = "http://10.11.0.110:6006"`（`tools.py:12`，模块级常量，**未读取** `.env` 中的同名键）。
  - 请求固定字段：`backend="hybrid-engine"`、`effort="high"`、`return_md="true"`、`response_format_zip="false"`（`tools.py:66-71`）。
  - 轮询参数为函数默认值：`timeout_seconds=900`、`poll_interval_seconds=2.0`（`tools.py:29-30`）。
- **数据/产物路径**（`ResourceConfig`，`resources.py:17-35`，`data_dir = _BACKEND_DIR / "data"`，锁定在 `backend/data/`）：
  - 会话库 `backend/data/dsagents_sessions.db`
  - store 库 `backend/data/dsagents_store.db`
  - checkpoint 库 `backend/data/dsagents_checkpoints.db`
  - 产物目录 `backend/data/artifacts/`
  - MinerU 输出默认落 `backend/data/mineru_outputs/{stem}.md`（`tools.py:56-57`）。

## 8. 持久化约定

- **append-only 事件**：`SqliteSessionStore.emit_event`（`session.py:114`）只 `insert`，从不 `update`/`delete`。表 `session_events` 自增主键 `event_id`（`session.py:192-202`）。根 AGENTS.md："Session 不是上下文窗口；摘要/裁剪视图可作为事件追加，但不得替代原始事件。"
- **StateBackend vs StoreBackend(SQLite)**（根 AGENTS.md + `resources.py:55-66`）：
  - DeepAgents 文件系统默认走 `StateBackend()`（`resources.py:58`，默认路由）。
  - 持久历史/记忆路由到 `StoreBackend(store=SqliteStore, namespace=("dsagents",))`，路径前缀 `/memories/`、`/conversation_history/`、`/logs/`（`resources.py:55,59-61`）。
  - 大产物/大工具结果路由到 `FilesystemBackend(root_dir=artifacts_dir, virtual_mode=True)`，路径前缀 `/artifacts/`、`/large_tool_results/`（`resources.py:56,62-63`）。
  - 三者组合为 `CompositeBackend(default=StateBackend(), routes={...})`（`resources.py:57-66`）。
- **checkpointer**：`SqliteSaver.from_conn_string(checkpoint_db)`（`resources.py:50-52`），用于 LangGraph 线程状态。
- **大 payload 落盘**：`emit_event` 对超过 `max_inline_bytes`（默认 262144，`session.py:56`）的 payload，写入 `backend/data/artifacts/session-events/{uuid}.json`，DB 内只存 `{"artifact_path", "bytes"}` 占位（`session.py:121-128`）。
- **资源生命周期**：`AgentResources` 是上下文管理器（`resources.py:43/69`），用 `ExitStack` 管理 `SqliteStore`、`SqliteSaver` 的关闭（`resources.py:41,48,50`）。进入时 `mkdir` 数据/产物目录并 `setup()` 两库（`resources.py:44-53`）。
- **会话存在性**：`emit_event` / `ensure_session` 用 `insert or ignore`（`session.py:66-73`）保证幂等创建。
