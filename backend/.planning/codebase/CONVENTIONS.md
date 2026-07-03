# 代码约定 (CONVENTIONS)

> 事实来源：backend/ 源码（2026-07-03 刷新）

本文档只记录代码中真实可见的约定，写成可执行规则。每条附「在哪验证」（`文件:行` 或类/函数名）。

## 1. 命名约定

- **模块**：全小写 `snake_case`，模块名单数名词；`session.py`、`harness.py`、`hands.py`、`resources.py`、`tools.py`、`api.py`、`self_check.py`。→ 验证：`backend/` 顶层文件名。
- **类**：`PascalCase`；`HarnessRuntime`、`DeepAgentsBrainFactory`、`TraceMiddleware`、`SqliteSessionStore`、`AgentResources`、`TraceHands`、`MessageRequest`。→ 验证：各模块 `class` 定义。
- **函数/方法**：`snake_case`；`run_turn`、`emit_event`、`context_window`、`parse_document`、`create_harness`。→ 验证：各 `def`。
- **常量**：`UPPER_SNAKE_CASE`；`DEFAULT_SYSTEM_PROMPT`、`SUCCESS_STATES`、`FAILURE_STATES`、`RUN_STATUSES`、`RUN_PREVIEW_LIMIT`、`INTERRUPTED_RUN_ERROR`。→ 验证：`harness.py:21`、`session.py:16-17`、`api.py:21`。
- **私有**：单下划线前缀；`_required_env`、`_reset_messages`、`_safe`、`_utcnow`、`_find_value`、`_setup`。→ 验证：各模块 `_` 开头符号。测试替身沿用此风格：`_FakeBrain`、`_FakeBrainFactory`（`self_check.py:37/90`）。
- **工厂函数**：`create_*` 前缀；`create_harness`、`create_app`、`create_deep_agent`(三方)。→ 验证：`harness.py:252`、`api.py:29`。
- **事件类型（event_type，固定枚举字符串，不可臆造）**：
  - 会话级（`session_events`）：`user_message`、`assistant_message`、`model_request`、`model_response`、`model_error`、`tool_request`、`tool_response`、`tool_error`。
  - run 级（`run_events`）：`status`、`thinking`、`text_delta`、`tool_status`、`values`，以及复用的 `model_*`/`tool_*`。
  - → 验证：`hands.py:38-49/62-74`（model/tool）、`harness.py:111-116/236-239`（user/assistant）、`harness.py:135-166`（run 流式）。
- **表名（固定）**：`sessions`、`session_events`、`runs`、`run_events`。→ 验证：`session.py:466-530`（`_setup` 的 `create table`）。

每个 `.py` 第 1 行统一 `from __future__ import annotations`（延迟注解求值）。无 `__all__`。

## 2. 文件组织（扁平顶层模块、绝对导入、无 __init__.py）

- `backend/` 不是包：**无 `__init__.py`、无 `__main__.py`**。`pyproject.toml` 把每个 `.py` 声明为顶层模块。→ 验证：`backend/` 无 `__init__.py`。
- 模块间一律**绝对导入**，不带 `backend.` 前缀：`from session import ...`、`from harness import ...`、`from hands import Hands, TraceHands`、`from resources import AgentResources, ResourceConfig`、`from tools import ToolCatalog`。→ 验证：`api.py:17-18`、`harness.py:14-17`、`hands.py:10`。
- 「一文件一职责」映射五大边界：Session=`session.py`、Harness=`harness.py`、Hands=`hands.py`、Resources=`resources.py`、Tools=`tools.py`；HTTP 传输=`api.py`；自检=`self_check.py`。→ 验证：各模块顶部 docstring/职责。
- 打破循环：`session.run_session` 在函数体内惰性 `from harness import create_harness` / `from resources import ...`（`session.py:534-535`）。模块级无循环依赖。

## 3. 类型约定

- **接口用 `typing.Protocol`**（结构化子类型，不强制继承）：`Brain`、`BrainFactory`（`harness.py:29-48`）、`Hands`（`hands.py:13`）、`SessionStore`（`session.py:73`）。
- **值对象用 `@dataclass(frozen=True)`**：`SessionRecord`、`SessionEvent`、`RunRecord`、`RunEvent`、`RunSnapshot`、`ContextWindow`（`session.py:20-70`）、`HarnessTurn`（`harness.py:83`）、`ResourceConfig`（`resources.py:17`）、`ToolCatalog`（`tools.py:23`）。
- **类型别名**：`ToolHandler = Callable[..., Any]`（`tools.py:20`）。**不使用 `TypedDict`**（全仓无 `TypedDict` 引入）。
- **pydantic 仅限 HTTP 边界**：`MessageRequest(BaseModel)`（`api.py:24`）；业务层一律 dataclass/Protocol，不用 pydantic。
- 消息字典结构固定 `{"role": "user"|"assistant", "content": str}`；`SessionEvent.payload: Any`。→ 验证：`session.py:549-557`（`_event_to_message`）、`harness.py:236-239`。
- 类型注解全量标注（含返回类型），PEP 604 `X | None`、PEP 585 `list[...]`/`tuple[...]`。

## 4. 错误处理（真实错误透传）

- **middleware 透传**：`TraceMiddleware.wrap_model_call`/`wrap_tool_call` 的 `except Exception` 块先 `emit_event(..., "model_error"|"tool_error", {"error": repr(exc)})`，随后 **`raise` 原样抛出**，不吞、不转译。→ 验证：`hands.py:42-46`（model）、`hands.py:66-71`（tool）。
- **`_required_env` fail-fast**：缺失环境变量抛 `RuntimeError(f"Missing required environment variable: {name}")`。→ 验证：`tools.py:84-88`。
- **原生异常暴露**：`int(_required_env("MINERU_TIMEOUT_SECONDS"))` 不包 `try`，非法整数直接暴露原生 `ValueError`。→ 验证：`tools.py:46`。
- **创建错误用内置异常**：`FileNotFoundError`/`RuntimeError`/`TimeoutError`/`KeyError`（未知 session）。→ 验证：`tools.py:38/109/134`、`session.py:150/391`。
- **HTTP 不包装**：`response.raise_for_status()` 直接抛（`tools.py:105/124/131`）。
- **契约自检**：`self_check.py:170-189` 显式断言 model/tool 错误必须透传（否则 `raise AssertionError`）。

## 5. 日志 / 可观测（middleware trace，repr 落事件，无 logger）

- **无独立 logger**：全仓无 `import logging`、无 `logger = ...`；控制台仅 `print(f"[model] ...")`、`print(f"[tool] {name} completed")`。→ 验证：`hands.py:50/76`。
- **错误以 `repr(exc)` 写入事件**（非 `str`，保留类型与引号），见 §1 事件类型与 §4。→ 验证：`hands.py:43/67`。
- **只记 model 可见信息**：middleware 截取 `request.messages` / `response.result`，不解析隐藏思维链；thinking/reasoning 仅在 harness 流式层作为 `thinking`/`text_delta` run 事件输出。→ 验证：`harness.py:133-149`。

## 6. 配置约定（.env 导入时加载、单一来源、缺失 fail-forward）

- **`.env` 在导入时加载**：`session.py:14` `load_dotenv(Path(__file__).with_name(".env"))`；`tools.py:16` 同样。任何触发 `session`/`tools` 导入的路径都会触发一次加载。→ 验证：`session.py:12-14`、`tools.py:12-16`。
- **MiniMax 接线（Anthropic 协议，无默认、无 fallback）**：`DeepAgentsBrainFactory.__init__` 仅 `os.getenv` 读 `MINIMAX_MODEL`/`MINIMAX_API_KEY`/`MINIMAX_BASE_URL`，构造 `init_chat_model(f"anthropic:{...}", api_key=..., base_url=..., thinking={"type":"adaptive"})`。→ 验证：`harness.py:52-61`。
- **MINERU 配置单一来源**：`parse_document` 经 `_required_env` 读 `MINERU_BASE_URL`/`MINERU_BACKEND`/`MINERU_EFFORT`/`MINERU_TIMEOUT_SECONDS`，缺失即 `RuntimeError`。→ 验证：`tools.py:43-46`。
- **缺失 fail-forward（fail-fast）**：无默认值兜底；env 不全则启动/调用即失败。→ 验证：`tools.py:84-88`；`self_check.py:121-129` 断言缺 `MINERU_BASE_URL` 必抛 `RuntimeError`。

## 7. 持久化约定（append-only、context_window 派生、三库独立）

- **append-only，只 insert 不 update/delete**：`emit_event`/`emit_run_event` 仅 `insert`；`event_id` 自增主键。→ 验证：`session.py:182-198/301-326`。
- **`context_window` 是派生视图，非事实源**：从 `session_events` 的 `user_message`/`assistant_message` 投影，取最近 `CONTEXT_MESSAGE_LIMIT=20` 条并剔除 leading 非 user 消息。→ 验证：`session.py:200-213/549-557`。
- **三 SQLite 库独立**：`dsagents_sessions.db`（会话/事件/runs）、`dsagents_store.db`（LangGraph Store）、`dsagents_checkpoints.db`（LangGraph checkpointer）。→ 验证：`resources.py:22-31/46-53`。
- **超大 payload 外溢**：默认阈值 `max_inline_bytes = 262_144`；超出则写 `artifacts/session-events/{uuid}.json` 或 `artifacts/run-events/{uuid}.json`，DB 内只存 `{"artifact_path","bytes"}` 指针。→ 验证：`session.py:117/447-459`。
- **会话幂等创建**：`ensure_session` 用 `insert or ignore`。→ 验证：`session.py:128-136`。

## 8. HTTP 约定（传输保持薄、run_id 统一创建、409 并发、启动清理）

- **传输保持薄**：`api.py` 只做请求解析、锁、调度 `HarnessRuntime.execute_run`、序列化 run 事件；业务逻辑在 harness/hands/session。→ 验证：`api.py:53-158`。
- **`run_id` 统一创建**：每个端点入口 `run_id = uuid.uuid4().hex`，由传输层生成，不下沉到 harness。→ 验证：`api.py:56/69/109`。
- **409 并发冲突锁**：按 session 维度的 `session_locks: dict[str, Lock]` + `active_runs` + `registry_lock`；运行中第二个请求返回 `JSONResponse(409, {"error":"该会话正在运行","active_run_id":...})`。→ 验证：`api.py:197-214/251-255`。
- **后台 run 进程内单飞**：`POST /sessions/messages/runs` 起 `threading.Thread(daemon=True)` 在进程内执行；非分布式队列。→ 验证：`api.py:119-130/172-179`。
- **启动重启 `fail_incomplete_runs`**：lifespan 进入时把遗留 `queued`/`running` 的 run 追加 `status=failed, error=INTERRUPTED_RUN_ERROR`。→ 验证：`api.py:40`、`session.py:356-378`。
