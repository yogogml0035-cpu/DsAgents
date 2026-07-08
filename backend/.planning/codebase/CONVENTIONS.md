# CONVENTIONS

> backend 子项目的开发约定。事实来源 = `backend/` 源码 + `pyproject.toml` + 根级 `docs/conventions.md`；本轮刷新（2026-07-08）已核对当前 HEAD `349357b`。
> 区分「已确认」（直接对应代码）与「需确认」（证据不足）。

## 1. 包管理器（已确认）

- 包管理器是 `uv`，**不是 pip**。安装/同步：

  ```powershell
  cd backend
  uv sync
  ```

- 锁文件：`backend/uv.lock`（已确认存在）。
- 打包后端：setuptools（`pyproject.toml` 中 `[build-system]` 用 `setuptools.build_meta`），通过 `py-modules` 把扁平 `.py` 注册为顶层模块。
- **禁止**用 `pip install -e .` 之类绕过 `uv`（与 `uv.lock` 不一致）。

## 2. 模块组织（已确认）

- 扁平顶层模块：`backend/` 下的 `.py` 直接作为顶层模块，绝对导入写作 `from hands import ...`、`from resources import AgentResources`，**不**带 `backend.` 前缀。
- `pyproject.toml` 的 `py-modules` 当前显式列出：`api` / `hands` / `harness` / `resources` / `run_ledger` / `tools`。新增顶层 `.py` 必须同步追加到此处。
- **没有** `backend/__init__.py` / `backend/__main__.py`（已确认不存在）。
- **没有** `python -m backend.*` 这种调用方式（包不是这么组织的）。
- backend 测试源码目录是 `backend/tests/`；测试脚本统一命名为 `test_*.py`，可执行脚本保留 `run()` 并支持 `python -m tests.test_xxx` 直接运行（见 TESTING.md）。

## 3. 运行入口（已确认）

| 场景 | 入口 |
| --- | --- |
| HTTP 上传文件 | `POST /upload`（multipart 字段名 `files`，支持 1 个或多个文件） |
| HTTP 提交 run | `POST /runs`（body `{session_id?, messages[]}`），轮询 `GET /runs/{run_id}` |
| 测试脚本 / 主要验证 | 按影响范围运行 `cd backend && python -m tests.test_xxx` |
| 启动 HTTP 服务 | `cd backend && uv run uvicorn api:app --host 0.0.0.0 --port 8500 --reload`（端口与 `scripts/start-backend.bat`、`tests/test_real_image_run.py` 的 `DEFAULT_BASE_URL` 一致） |
| 程序内调用 | `AgentResources(config)` + `create_harness(resources).execute_run(messages, session_id, run_id)` |

- **没有** `from session import run_session`（已确认 grep 无此导入，且无 `session.py`）。Session 概念已移除，改由 `thread_id=session_id` + run ledger 承载。

## 4. 核心运行时原则（来自根级 `docs/conventions.md`，在代码中落地）

- **能力可插拔**：`Brain` / `BrainFactory` / `Hands` 是 `typing.Protocol`；运行时通过依赖注入接收 `brain_factory`、`hands`、`tools`。`create_harness` 用默认实现，本地测试用 `FakeBrainFactory` 注入。工具保持普通 callable + `ToolCatalog`，不为单实现工具新增 Protocol/ABC。
- **run 是事件源**：`run_events` 表 append-only；`runs` 表是当前快照。短期上下文靠 LangGraph `thread_id=session_id`，不再有 session 层。
- **保持运行时薄**：`HarnessRuntime.execute_run` 只做「派发 payload → 解析 stream chunk → 写 run event」。不在运行时内引入服务层 / 工作流引擎。
- **真实错误透传**：见 §5。
- **优先删减范围**：HTTP 表面只保留 `POST /runs` / `GET /runs/{run_id}` / `POST /upload`（旧 session 端点与 `POST /files` 已删，见 §7）。

## 5. 错误处理（已确认）

- **真实错误透传，不吞**：
  - `ToolStatusMiddleware.wrap_tool_call`：捕获工具异常后先发 `error` status，再 `raise` 透传。
  - `HarnessRuntime.execute_run`：捕获 Brain 异常 → 发 `failed` run status（带 `error=_error_text(exc)` 与 `raw={..., "error": repr(exc)}`），不掩盖。
  - `api._run_background`：未捕获异常 → `_ensure_failed_run` 把 run 标记 `failed` 并写 `repr(exc)`。
- `_error_text(exc)` = `str(exc)` 去空白，为空则回退到异常类名。
- fail-fast 模式：`parse_documents` 在存在可提交文件且缺 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 时 `raise RuntimeError("Missing required environment variable: ...")`；`MINERU_EFFORT` 可留空（测试脚本显式断言此行为）。
- run 状态非法值：`SqliteRunLedger.emit_run_status` 对非 `RUN_STATUSES` 抛 `ValueError`。

## 6. run-first 架构（已确认）

- 已移除 session 层：无 `session.py`、无 `from session import run_session`、无 `run_turn` / `stream_turn`。
- 并发模型：同一 `session_id` 通过 `threading.Lock` 串行；冲突返回 `409 {"error":"该会话正在运行","active_run_id":...}`。
- 启动恢复：`fail_incomplete_runs` 在 app lifespan 启动时把遗留 `queued/running` run 标记 `failed`（错误文案 `INTERRUPTED_RUN_ERROR = "执行已中断，请重试"`）。
- 失败 run **不**回滚 thread；下一次同 `session_id` 继续用同一 `thread_id`（测试脚本的 `FakeBrain` 用 `threads` dict 按序号验证此行为）。

## 7. HTTP 表面（已确认）

- 当前只保留：`POST /runs`、`GET /runs/{run_id}`（可选 `?after_event_id=` 取增量）、`POST /upload`。
- `POST /upload` 会保留 basename，但把文件名中的 Unicode 空白（如 `NBSP`、`\t`）归一成普通空格后再落盘并回传 `file_path`，避免模型/tool call 把不可见空白改写后找不到磁盘文件。
- 未知 run：`404 {"error":"Unknown run: <run_id>"}`。
- 已删除的旧语义：`session.py`、`context_window`、`RemoveMessage(REMOVE_ALL_MESSAGES)`、`run_turn`/`stream_turn`、`TraceHands`、旧 session 端点（完整清单见根级 `INTERFACES.md` §1）。

## 8. 类型与命名（已确认）

- `typing.Protocol` 只用于可注入能力边界：`Brain`、`BrainFactory`、`Hands`。默认实现从 `create_harness(...)` 追到 `DeepAgentsBrainFactory`、`ToolStatusHands`、`default_tool_catalog()`。
- 外部框架要求继承时才继承框架基类：如 `ToolStatusMiddleware(AgentMiddleware)`、`RunRequest(BaseModel)`；不要为单实现小功能新增 Protocol/ABC。
- 简单值对象用 `@dataclass(frozen=True)`：`RunEvent`、`RunSnapshot`、`ResourceConfig`、`ToolCatalog`。
- 命名：模块/函数/方法 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`（如 `RUN_STATUSES`、`INTERRUPTED_RUN_ERROR`、`DEFAULT_SYSTEM_PROMPT`）。
- 顶层文件统一 `from __future__ import annotations`，类型注解广泛使用 `X | None`、`dict[...]`、`list[...]`。

## 9. 运行时 stream 约定（已确认）

- Brain 调用统一走 `brain.stream({"messages": normalized_messages}, config={"configurable":{"thread_id":session_id}}, stream_mode=["messages","custom","values"], version="v2")`。
- payload **只**传当前请求的 `messages[]`；`text` block 原样保留，`artifact` block 会在进入 Brain 前转成文本路径提示（测试脚本的 `FakeBrain` 断言 Brain 侧只收到 text blocks，证明不再直接透传 artifact block，也不再注入 `RemoveMessage`）。
- run ledger 事件类型固定 7 种：`status` / `thinking` / `text_delta` / `assistant_message` / `tool_call` / `tool_status` / `tool_result`。
- `raw.type=="values"` 只保留在原始 snapshot；业务层从 snapshot 中派生 `tool_call` / `tool_result` / `assistant_message`，不再把 `values` 当作业务事件写库，外部调用方也不应依赖 `values` 事件。
- 最终 AIMessage 同时含 `thinking` 与 `text` block 时，`assistant_message.payload` 保留最后一个 `thinking` 文本和最终 `text`；改动该结构必须同步 `INTERFACES.md`、`SYSTEM_MAP.md` 和 API / harness 测试断言。
- 工具状态中间件只发 `started` / `completed` / `error` 三态（测试脚本断言）。

## 10. 持久化约定（已确认）

- `runs` 行 = 当前 run 快照（状态机：`queued→running→succeeded|failed`）。
- `run_events` = append-only 事件流。
- 大 payload/raw（默认 `max_inline_bytes=262_144`，256 KiB）外溢到 `data/artifacts/run-events/*.json`，行内只留指针 `{"artifact_path":..., "bytes":...}`。
- 数据目录固定锚定在 `backend/data/`（`_BACKEND_DIR = Path(__file__).resolve().parent`，与 CWD 无关）。
- 不做清理策略、不做历史迁移。

## 11. LLM / Brain 约定（已确认）

- 默认 `DeepAgentsBrainFactory`：从 `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 环境变量构造，映射到 LangChain 的 Anthropic 客户端（`init_chat_model("anthropic:...", thinking={"type":"adaptive"})`）。
- 普通本地测试用 `FakeBrainFactory` / `FakeBrain`：验证 Brain 可替换、payload 接收当前请求的 `messages[]`、`thread_id` 路由、失败 run 后同 thread 续跑不回滚。

## 12. 文档与交付约定（来自根级 `docs/conventions.md`）

- **事实层在子项目**：`backend/.planning/codebase/` 是 backend 实现细节的事实来源；根级只放导航与稳定全局原则。
- **改代码后同步事实层**：改 `backend/` 实现后，先更新本目录对应文档，再视影响回看 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`。
- **文档语言**：简体中文说明性正文；保留代码标识符、文件路径、命令、配置键、API 名称、IP/端口原文。
- **不外泄密钥**：文档不写入任何密钥 / token / 连接串。
- **证据不足标注**：用「需确认 / 初步判断」表达，不写成硬规则。
