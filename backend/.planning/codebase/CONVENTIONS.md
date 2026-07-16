---
last_mapped_commit: 3a3a6e5c3f608a05ae5a076b99812723c097613e
---

# Coding Conventions

**Analysis Date:** 2026-07-16

> 事实来源：`backend/` 源码（`api.py`、`runtime/*`、`integrations/*`、`skills/*`、`tests/*`、`pyproject.toml`）。约定以可执行代码为准。

## Naming Patterns

- **模块 / 函数 / 方法**：`snake_case`（如 `create_harness`、`execute_run`、`default_tool_catalog`、`parse_documents`、`runtime_middlewares`）。
- **类**：`PascalCase`（如 `HarnessRuntime`、`SqliteRunLedger`、`AgentResources`、`ToolCatalog`、`DeepAgentsBrainFactory`、`StructuredOutputRecovery`）。
- **常量**：`UPPER_SNAKE_CASE`（如 `RUN_STATUSES`、`INTERRUPTED_RUN_ERROR`、`MAIN_AGENT_NAME`、`MAIN_AGENT_MODEL`、`NO_PROGRESS_WINDOW`、`DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES`、`ARTIFACT_REFERENCE_HINT`、`BACKEND_ENV_PATH`、`RUNTIME_AGENTS_PATH`、`WORKFLOW`）。
- **私有符号**：单下划线前缀 `_`（如 `_normalize_messages`、`_error_text`、`_problem`、`_problems`、`_required_env`、`_retry_or_give_up`）。测试内部检查函数同惯例：`_check_*`。
- **类型别名**：`ToolHandler = Callable[..., Any]`（`runtime/tools.py`）；协议名用名词：`Brain`、`BrainFactory`。
- **Skill / 包标识符**：Python 包目录不含连字符（`philipswgqinboundrecognition`、`tecanimport`）；`SKILL.md` frontmatter 名可使用连字符（`philips-wgq-inbound-recognition`）。声明式 SubAgent 显示名用连字符（`tecan-extractor-a` / `tecan-extractor-b`）。
- **业务工具函数名**：按实际职责命名。Philips 使用单一查询工具 `lookup_philips_wgq_master_data`；Tecan 保留 `save_tecan_extraction` + `generate_tecan_import`；通用 MinerU 工具为 `parse_documents`、`extract_archives`。
- **HTTP / 事件字段**：请求与事件 payload 用 `snake_case` JSON 键（`session_id`、`run_id`、`after_event_id`、`input_tokens`、`cache_read_input_tokens`、`structured_response`）。
- **run 状态字面量**：`queued` / `running` / `succeeded` / `failed` / `cancelling` / `cancelled`（集合 `RUN_STATUSES` 于 `runtime/runs.py`）。
- **事件类型字面量**（固定 7 种，不可随意扩展）：`status`、`tool_execution`、`tool_progress`、`thinking`、`text_delta`、`assistant_message`、`model_usage`。
- **Philips workflow 字面量**：仅 `philips_wgq_inbound_recognition`（`skills/philipswgqinboundrecognition/schema.py` 的 `WORKFLOW`，HTTP `RunRequest.workflow` 同 `Literal`）。
- **Philips 结构化结果字段**：英文字段名（`product_id`、`currency`、`unit_price` 等），`extra="forbid"`，无中文 JSON alias。

## Code Style

- **包管理器是 `uv`，不是 pip**。同步依赖：

  ```powershell
  cd backend
  uv sync
  ```

  锁文件为 `backend/uv.lock`。禁止 `pip install -e .` 绕过锁文件。
- **Python 版本**：`requires-python = ">=3.11,<4.0"`（`backend/pyproject.toml`）。
- **无 lint / type-check / pytest 门禁**：`pyproject.toml` 无 `[tool.ruff]` / `[tool.mypy]` / `[tool.black]` / `[tool.pytest...]` / `[tool.coverage]`。验证靠 `backend/tests/` 下 assert 脚本（见 `TESTING.md`）。
- **文件头**：源码与测试统一 `from __future__ import annotations`。
- **类型注解风格**：广泛使用现代联合与内置泛型——`str | None`、`list[...]`、`dict[...]`、`tuple[...]`、`Sequence[...]`、`Iterator[...]`；HTTP 模型用 pydantic `BaseModel` + `ConfigDict(extra="forbid")` + `Literal` + `Annotated` discriminator。
- **值对象**：简单不可变快照用 `@dataclass(frozen=True)`（`RunEvent`、`RunSnapshot`、`ResourceConfig`、`ToolCatalog`）。需要校验/序列化的 HTTP 请求体与业务合同用 pydantic。
- **继承边界**：仅在外部框架要求时继承——`ToolTelemetry(AgentMiddleware)`、`NoProgressMiddleware(AgentMiddleware)`、`StructuredOutputRecovery(AgentMiddleware)`、`RunRequest(BaseModel)` 等。不为单实现业务工具发明 ABC。
- **依赖注入优于硬编码**：`HarnessRuntime` 构造接收 `resources`、`tools: ToolCatalog`、`brain_factory: BrainFactory`；`create_app` 接收可选 `resource_config` 与 `harness_factory`，便于测试注入 `FakeBrainFactory`。
- **运行时保持薄**：`HarnessRuntime.execute_run` 只做「规范化 messages → 调 Brain stream → 解析 chunk → 写 run event」。业务规则下沉到 `skills/*/scripts/`，不在 runtime 内建工作流引擎。
- **工具静态注册**：`default_tool_catalog()` 在 `runtime/tools.py` 用静态 import 注册 **5** 个 callable；新增 Skill 时追加 import + 注册行，不自动扫描、不插件化。
- **配置键只读 env 名**：代码从 `MINIMAX_*` / `MINERU_*` / `ORACLE_*` 读环境变量；文档与约定只记键名，不写入本地 `.env` 值。
- **版本锁定**：以 `uv.lock` 与 `pyproject.toml` 下限为准（如 `deepagents>=0.6.12`）；不写面向未来 deepagents 版本的参数 shim。
- **注释语言**：模块 docstring 与关键业务注释可用简体中文；标识符、API、路径保持英文/原文。

## Module Organization

- **源码顶层**（安装根 = `backend/`）：
  - `api.py` — FastAPI HTTP 入口（`create_app`、`app`）
  - `runtime/` — 运行时：`agent.py`（Brain/工厂/SubAgent 装配）、`middleware.py`（可复用 middleware）、`execution.py`（Harness）、`resources.py`（资源装配）、`runs.py`（ledger）、`tools.py`（ToolCatalog）、`observability.py`（纯提取器）
  - `integrations/` — 外部集成：`artifacts.py`（路径/JSON）、`mineru.py`（解析与解压）
  - `skills/` — 内置 Skill 包：`philipswgqinboundrecognition/`（`SKILL.md`、`schema.py`、`scripts/tools.py`）与 `tecanimport/`（`SKILL.md`、`references/`、`assets/`、`scripts/{tools.py,documents.py}`）
- **发行名**仍为 `dsagents`；`[tool.setuptools] py-modules = ["api"]`，包发现 `runtime*` / `integrations*` / `skills*`；Philips 打包 `SKILL.md`，Tecan 另打包 `references/*.md` / `assets/*`。
- **导入一律绝对顶层**，无 `backend.` 前缀：

  ```python
  from runtime.execution import create_harness
  from runtime.runs import SqliteRunLedger
  from integrations.artifacts import resolve_artifact_path
  from skills.philipswgqinboundrecognition.scripts.tools import lookup_philips_wgq_master_data
  ```

  前提：`cd backend` 或安装后包在 `sys.path`。**没有** `python -m backend.*`。
- **数据目录锚定** `backend/data/`（`ResourceConfig` 用 `Path(__file__).resolve().parents[1] / "data"`），与 CWD 无关。
- **`runtime/__init__.py`** 只 re-export 稳定入口：`AgentResources`、`ResourceConfig`、`HarnessRuntime`、`create_harness`、`RunEvent`、`RunSnapshot`、`SqliteRunLedger`。
- **循环依赖处理**：`create_harness` 对 `DeepAgentsBrainFactory` / `default_tool_catalog` 使用函数内 local import；`_extractor` 同理 local import `default_tool_catalog`。
- **测试包**：`backend/tests/` 含 `__init__.py`，以 `python -m tests.test_xxx` 运行。

## Error Handling

- **真实错误透传，不吞**：
  - `HarnessRuntime.execute_run`：`NoProgressLoop` 与其它 `Exception` → `emit_run_status(..., "failed", error=..., raw=...)`；`GraphDrained` → `cancelled`。
  - Philips workflow 结束时缺 `structured_response` → `ValueError("structured_response missing for philips_wgq_inbound_recognition")` → run `failed`。
  - `api._run_background`：未捕获异常 → `_ensure_failed_run`（仅当 run 尚未终态时标 `failed`）。
- **fail-fast 配置**：
  - `parse_documents`（`integrations/mineru.py`）：存在可提交文件时缺 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` → `RuntimeError`（`_required_env`）；`MINERU_EFFORT` 可空。
  - `SqliteRunLedger.emit_run_status`：非法 status → `ValueError`。
  - 未知 run → `KeyError`（HTTP 层转 `404 {"error":"Unknown run: ..."}`）。
  - `StructuredOutputRecovery(max_retries=...)`：`max_retries < 0` → `ValueError`。
- **业务问题 vs 异常**：
  - 工具入参/契约错误（缺文件、非法 extractor、Philips 缺少/非法 `structured_response` 等）→ `raise ValueError` / Pydantic `ValidationError`（可导致 run `failed`）。
  - Tecan 可恢复的业务校验（A/B 冲突、缺 C、字段缺失等）→ **不抛异常**，返回：

    ```python
    {"code": "input_problems", "problems": [{"source", "location", "issue", "action"}]}
    ```

    由 `skills/tecanimport/scripts/tools.py` 的 `_problems` / `_problem` 构造；成功形状为：

    ```python
    {"status": "generated", "canonical_artifact", "artifacts", "manual_checks"}
    ```

  - Philips 业务完整度由验证后的 `result.outcome` 表示：`success` / `partial_success` / `input_problems`；其中 `input_problems` 要求 `data=null` 且至少一个 `problems` 项，且 **run 仍为 `succeeded`**（业务问题 ≠ 执行失败）。`success` 可带非空 `problems`（字段缺口、主数据未命中等）；`partial_success` 要求至少一个 problem。
  - Philips 工具侧 `problems` 条目形状：`{source, location, issue, action}`（与 Tecan 一致），由 `_problem` 构造。
- **Oracle 例外**：Philips 主数据工具遇到配置缺失、查询失败或未命中时把原因写入 `problems`，保留 PDF/Tracking 结果；不写“需确认”等占位值。
- **HTTP 状态约定**：
  - 同 `session_id` 并发冲突 → `409`（`{"error":"该会话正在运行","active_run_id":...}`）
  - cancel 未知 run → `404`；已终态 `succeeded`/`failed` → `409`；已 `cancelling`/`cancelled` → `200` 幂等；活跃 drain → `202`
  - pydantic 校验失败 → FastAPI `422`（`extra="forbid"` 拒绝旧字段如单数 `message`；未知 `workflow` 或 Philips workflow 复用非空 `session_id` 同样 `422`）
- **启动恢复**：lifespan 内 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)`，文案 `"执行已中断，请重试"`，把遗留 `queued`/`running`/`cancelling` 标为 `failed`。
- **部分失败不抛**：MinerU 多文件解析时单文件失败进入 `failed[]`，整体仍返回结果；全无效输入也不抛，返回空 `succeeded`。

## Type Patterns

- **`typing.Protocol` 仅用于可注入 Brain 边界**（`runtime/agent.py`）：

  ```python
  class Brain(Protocol):
      def stream(
          self,
          payload: dict[str, Any],
          config: dict[str, Any] | None = None,
          **kwargs: Any,
      ) -> Iterator[dict[str, Any] | Any]: ...

  class BrainFactory(Protocol):
      def create(
          self,
          *,
          resources: Any,
          middleware: Sequence[AgentMiddleware],
          tools: Sequence[Any],
          workflow: str | None = None,
      ) -> Brain: ...
  ```

  默认实现：`DeepAgentsBrainFactory`；测试实现：`FakeBrain` / `FakeBrainFactory`（`tests/test_support.py`）。**不为工具、ledger、资源再加 Protocol/ABC**。
- **工具**：普通 callable + 冻结 `ToolCatalog(handlers: tuple[ToolHandler, ...])`，经 `.as_list()` 交给 Brain。当前 5 个：`parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`、`save_tecan_extraction`、`generate_tecan_import`。
- **资源与 ledger**：具体类 `AgentResources`、`ResourceConfig`、`SqliteRunLedger`；上下文管理器用 `ExitStack` 管理 store/checkpointer。
- **声明式 SubAgent**：`workflow_subagents()` 返回两个 Tecan `SubAgent`（dict 形配置：`name` / `description` / `system_prompt` / `tools` / `permissions` / `response_format` / `middleware`）。每个 SubAgent **各自** `runtime_middlewares()`（无 `memory_backend`；声明式 SubAgent 不继承主 Agent middleware）——共 **4** 个：`StructuredOutputRecovery`、`ToolTelemetry`、`NoProgressMiddleware`、`StructuredOutputCompatibility`。Philips workflow 显式使用空 `subagents`。
- **主 Agent middleware**：`runtime_middlewares(memory_backend=...)` 在上述 4 个之上再挂一个受限 `MemoryMiddleware`（共 **5** 个；`add_cache_control=True`）；handbook 路径 `/memories/AGENTS.md`。
- **运行时操作手册**：`AgentResources` 在 `/memories/AGENTS.md` 缺失时写入 ZIP/`result_path` 消费基线；主 Agent 经 `MemoryMiddleware` 自动注入，不依赖模型先 `read_file`；失败后追加由提示词约束 + `edit_file`，不做自动写回。
- **结构化输出**：Tecan extractor 使用 `ToolStrategy(ExtractionReference, ...)`；Philips 主 Agent 使用 `ToolStrategy(PhilipsWgqRecognitionResult, handle_errors=philips_structured_output_error_message, ...)`，Harness 从 `updates` 捕获后再次 Pydantic 校验并投影 `result_json`。
- **Philips 结构化提交硬约束**：禁止 `data: {}`；`success`/`partial_success` 必须带齐 `shipment`/`header`/`items`（未知填 `null`）；`input_problems` 才允许 `data=null`。Skill 与 `PHILIPS_WORKFLOW_PROMPT` 双写。
- **智能体可见文案**：system prompt、Skill、`RUNTIME_AGENTS_BASELINE`、工具 docstring/`Annotated` 参数说明、结构化纠错提示统一**简体中文**；代码标识符、工具名、schema 英文字段名、路径与 API 名保持英文。
- **StructuredOutputRecovery 有界重试（硬约定）**：
  - `@hook_config(can_jump_to=["model", "end"])` 必须同时声明 `"end"`。
  - 解析/校验失败、空文本、或空 `data` 壳：`jump_to: "model"`，最多 `max_retries`（默认 `DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES = 2`）。
  - 空壳用 `EMPTY_DATA_SHELL_HINT`；**不**在 recovery 中编造业务字段。
  - 达到 `max_retries` 或无法产出 `structured_response` 时显式 `jump_to: "end"`。
  - **禁止**只返回 `None` 依赖默认边退出——在仅有 `ToolStrategy`、无业务 tool 的图上会触发 model↔model 无限循环。
  - 验证：`cd backend && python -m tests.test_harness`（断言重试封顶与耗尽 `jump_to: "end"`、空壳专用纠错）。
  - 共享列表顺序只改 `runtime_middlewares()`；Philips 工厂仅在缺失时 `insert(0)` Recovery / append Compatibility，勿破坏「Recovery 在列表最前」约定。
- **权限**：`FilesystemPermission(operations=["write"], paths=[...], mode="deny")`；主 Agent deny `/skills/**`，SubAgent deny `/**` 写。
- **Brain stream 契约**（`runtime/execution.py`）：

  ```python
  brain.stream(
      {"messages": normalized_messages},
      config={"configurable": {"thread_id": session_id}},
      stream_mode=["messages", "custom", "updates"],
      subgraphs=True,
      version="v2",
      control=RunControl(),
  )
  ```

  `artifact` block 进入 Brain 前归一为文本提示 `ARTIFACT_REFERENCE_HINT`；payload **只**含当前请求 `messages[]`。`BrainFactory.create(..., workflow=workflow)` 明确接收可选 workflow；当前固定值仅 `philips_wgq_inbound_recognition`。
- **session_id 角色**：不是持久化对象，只作 LangGraph `thread_id` 与进程内单飞锁键（`app.state.session_locks` + `app.state.active_runs`）。
- **可观察 payload 形状**（`runtime/observability.py`）：`model_usage` 固定键 `model`（常量 `MAIN_AGENT_MODEL = "MiniMax-M3"`）、`scope`、`agent_name`、四类 token 计数；`assistant_message` 可含 `thinking` + `text`。

## Logging / Observability conventions

- **无标准 logging 门禁**：运行时不以 `logging` 模块作为主观测面；观测走 **run 事件账本**（append-only `run_events` + `runs` 快照）。
- **事件写入**：一律经 `SqliteRunLedger.emit_run_event` / `emit_run_status`；时间戳 UTC ISO-8601 毫秒（`...Z`）。
- **事件职责**：
  - `status` — 状态机迁移
  - `tool_execution` — ToolTelemetry / updates 派生的工具调用与结果摘要
  - `tool_progress` — `parse_documents` / `extract_archives` 经 custom stream 的进度
  - `thinking` / `text_delta` — 主 Agent 流式内容（SubAgent 文本经 `lc_agent_name` 过滤）
  - `assistant_message` — 终态助手消息（可带 `thinking`）
  - `model_usage` — 成本/缓存事实；**不**进入 `latest_content_event`；HTTP 顶层 `usage` 由 `aggregate_model_usage` + `api._usage_summary` 汇总计价
- **middleware 观测**（实现集中于 `runtime/middleware.py`，由 `runtime/agent.py` re-export 部分符号）：
  - `ToolTelemetry.wrap_tool_call`：`started` / `completed` / `error` + `duration_ms` + `agent_name`
  - `NoProgressMiddleware.before_model`：从当前消息状态派生连续 `NO_PROGRESS_WINDOW=3` 次同一 tool+args → `NoProgressLoop`；不把调用历史写入 graph state 或 middleware 实例
  - `StructuredOutputCompatibility.wrap_model_call`：ToolStrategy 路径关闭 thinking，避免与结构化工具冲突
  - `StructuredOutputRecovery.after_model`：从纯文本 JSON 恢复 `structured_response`，有界 `jump_to`
- **stream writer 安全**：`_safe_writer` / MinerU 侧对 `get_stream_writer` 的 `KeyError`/`RuntimeError` 静默降级为 no-op，避免无 graph 上下文时炸工具。
- **大 payload 外溢**：默认 `max_inline_bytes=262_144`，超限写入 run-events 目录下 JSON，行内留指针；用户可见物仍在 `data/artifacts/{uploads,downloads}/`。
- **SubAgent 隔离**：SubAgent `messages` 文本不写公开 thinking/text_delta；其 `model_usage` 仍以 `scope="subagent"` 计入。

## Import Patterns

- **标准库 → 第三方 → 本包** 分组（源码大致遵循；无 isort 强制）。
- **本包绝对导入**（见 Module Organization）；测试同样：`from api import create_app`、`from tests.test_support import FakeBrainFactory`。
- **延迟 / 局部 import** 用于：
  - 打破环：`create_harness`、`_extractor`、`artifacts_root` 内读 `ResourceConfig`
  - 可选重量依赖路径（如 Oracle 客户端在业务函数内按需 import）
- **环境加载**：`runtime/agent.py` 与 `integrations/mineru.py` 在模块级 `load_dotenv(BACKEND_ENV_PATH)`，路径固定 `backend/.env`。
- **测试替身 import**：`unittest.mock.patch` / `patch.dict(os.environ, ..., clear=True)`；HTTP 用 `fastapi.testclient.TestClient`；业务测试 patch `integrations.artifacts.artifacts_root`。
- **禁止模式**：不从 `backend.xxx` 导入；不在约定层依赖相对跨包 `from ...` 穿透 Skill 边界以外的随意路径；工具目录不动态 `importlib` 扫描；不重新引入已删除的 session API、SSE 或旧顶层辅助模块。

---
*Conventions analysis: 2026-07-16*
