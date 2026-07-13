# CONVENTIONS

> backend 子项目的开发约定。事实来源 = `backend/` 源码 + `pyproject.toml`；本轮刷新（2026-07-13）已核对当前工作树：`dsagents/` 包（runtime / integrations / skills 子包）、6 个静态注册工具、`Protocol` 边界、run-first HTTP/ledger/cancel 约定均与代码逐一比对。
> 区分「已确认」（直接对应代码）与「需确认」（证据不足）。

## 1. 包管理器（已确认）

- 包管理器是 `uv`，**不是 pip**。安装/同步：

  ```powershell
  cd backend
  uv sync
  ```

- 锁文件：`backend/uv.lock`（已确认存在）。
- 打包后端：setuptools（`pyproject.toml` 中 `[build-system]` 用 `setuptools.build_meta`，`requires = ["setuptools>=68"]`）。`backend/` 作为安装根，`dsagents` 是一个 Python 包，通过 `[tool.setuptools.packages.find]` 的 `include = ["dsagents*"]` 发现子包；`[tool.setuptools.package-data]` 把两个内置 Skill 的 `SKILL.md` / `references/*.md` / `assets/*` 随 wheel 打包。
- **禁止**用 `pip install -e .` 之类绕过 `uv`（与 `uv.lock` 不一致）。
- 当前 `requires-python = ">=3.11,<4.0"`；核心运行时依赖（版本下限）见 STACK.md。
- `pyproject.toml` 当前**没有**任何 `[tool.ruff]` / `[tool.mypy]` / `[tool.black]` / `[tool.pytest...]` / `[tool.coverage]` 段（已确认），即未配置 lint / type-check / pytest 门禁。验证只靠测试脚本（见 TESTING.md）。

## 2. 模块组织与导入约定（已确认）

- 唯一产品包是 `dsagents/`，子包 `runtime/`、`integrations/`、`skills/`（含两个内置 Skill 包 `philipswgqimport` / `tecanimport`）。旧扁平顶层模块（`api.py`/`harness.py`/`hands.py`/`resources.py`/`run_ledger.py`/`tools.py`/`subagents.py`/`workflow_artifacts.py`/`artifact_names.py`/`philips_wgq_import.py`/`tecan_import.py`）与旧带连字符 `skills/` 目录均已删除。
- 模块内一律使用**绝对包内导入**，不带 `backend.` 前缀：

  - `from dsagents.runtime import AgentResources, create_harness`
  - `from dsagents.runtime.runs import SqliteRunLedger, RunEvent`
  - `from dsagents.integrations.artifacts import resolve_artifact_path, write_json_artifact`
  - `from dsagents.skills.philipswgqimport.scripts.tools import generate_philips_wgq_import`
  - `from dsagents.skills.tecanimport.scripts.tools import generate_tecan_import`

  调用前提是 `backend/` 在 `sys.path`（开发时 `cd backend` 运行；安装后由包提供）。
- **没有** `backend/__init__.py` / `backend/__main__.py`（已确认不存在）；**没有** `python -m backend.*` 调用方式。
- backend 测试源码目录是 `backend/tests/`，带 `tests/__init__.py`（使其成为可被 `python -m tests.test_xxx` 导入的包）；测试脚本统一命名为 `test_*.py`，可执行脚本保留 `run()` 并支持 `python -m tests.test_xxx` 直接运行（见 TESTING.md）。
- 数据目录固定锚定在 `backend/data/` 下，与 CWD 无关（由 `ResourceConfig` 决定路径）。

## 3. 运行入口（已确认）

| 场景 | 入口 |
| --- | --- |
| HTTP 上传文件 | `POST /upload`（multipart 字段名 `files`，支持 1 个或多个文件） |
| HTTP 提交 run | `POST /runs`（body `{session_id?, messages[]}`），轮询 `GET /runs/{run_id}` |
| HTTP 取消 run | `POST /runs/{run_id}/cancel`（404 未知 / 409 终态 / 200 已 cancelling/cancelled / 202 活跃 drain） |
| 测试脚本 / 主要验证 | 按影响范围运行 `cd backend && python -m tests.test_xxx` |
| 启动 HTTP 服务 | `cd backend && uv run uvicorn dsagents.api:app --host 0.0.0.0 --port 8500` |
| 程序内调用 | `AgentResources(config)` + `create_harness(resources).execute_run(messages, session_id, run_id)` |

- Session 概念已收窄：无 session 模块、无 session 表；`session_id` 只作 LangGraph `thread_id` + 单飞锁键。

## 4. 核心运行时原则（在代码中落地）

- **能力可插拔**：`Brain` / `BrainFactory` 是 `typing.Protocol`（`dsagents/runtime/agent.py`）；运行时通过依赖注入接收 `brain_factory`、`tools`。`create_harness` 用默认实现，本地测试用 `FakeBrainFactory` 注入。工具保持普通 callable + `ToolCatalog`，**不**为单实现工具新增 Protocol/ABC。
- **run 是事件源**：`run_events` 表 append-only；`runs` 表是当前快照。短期上下文靠 LangGraph `thread_id=session_id`（经 `checkpointer` + `store`），不再有 session 层。
- **保持运行时薄**：`HarnessRuntime.execute_run` 只做「派发 payload → 解析 stream chunk → 写 run event」。业务规则全部下沉到 Skill 的 `scripts/`，不在运行时内引入服务层 / 工作流引擎。
- **业务状态外置为 artifact**：A/B/C、裁决、canonical 与 Excel 只写唯一新文件；`generate_*_import` 只接受显式 artifact 路径，不扫描 session、历史上传或「最近任务」。无游标、无暂停/恢复、无跨 run 状态。
- **按业务保留确定性规则**：Philips/Tecan 各自实现 canonical 构建与 Excel 规则，不抽象通用 A/B 引擎、插件注册表或工作流 DSL；共享模块（`dsagents/integrations/artifacts.py`）只做路径和 JSON 读写。
- **真实错误透传**：见 §5。
- **优先删减范围**：HTTP 表面只保留 `POST /runs` / `GET /runs/{run_id}` / `POST /runs/{run_id}/cancel` / `POST /upload`。

## 5. 错误处理（已确认）

- **真实错误透传，不吞**：
  - `HarnessRuntime.execute_run`：捕获 Brain 异常（含 `NoProgressLoop`）→ 发 `failed` run status（带 `error` 与 `raw`），不掩盖。
  - `api._run_background`：未捕获异常 → `_ensure_failed_run` 把 run 标记 `failed`。
- fail-fast：`parse_documents`（`dsagents/integrations/mineru.py`）在存在可提交文件且缺 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 时 `raise RuntimeError`；`MINERU_EFFORT` 可留空。
- run 状态非法值：`SqliteRunLedger.emit_run_status` 对非 `RUN_STATUSES` 抛 `ValueError`。
- Oracle 是明确例外：`generate_philips_wgq_import` 中 Oracle 单位查询失败 / thick client 缺失必须转成人工校验并继续生成，业务问题统一以 `{"code":"input_problems","problems":[{"source","location","issue","action"}]}` 返回，run 结束（由 `test_philips_wgq_import.py` 断言）。

## 6. run-first 架构（已确认）

- 已移除 session 层：无 session 模块、无 `run_turn` / `stream_turn`。
- 状态机：`queued → running → succeeded|failed`；`queued → cancelled`；`running → cancelling → cancelled`。
- 取消：`POST /runs/{run_id}/cancel` 协作 drain（LangGraph `RunControl`），`GraphDrained` 投影为 `cancelled`。
- 并发模型：同一 `session_id` 通过 `threading.Lock` 串行；冲突返回 `409 {"error":"该会话正在运行","active_run_id":...}`。
- 启动恢复：`fail_incomplete_runs` 在 app lifespan 启动时把遗留 `queued/running/cancelling` run 标记 `failed`（错误文案 `INTERRUPTED_RUN_ERROR = "执行已中断，请重试"`）。
- 失败 run **不**回滚 thread；下一次同 `session_id` 继续用同一 `thread_id`。

## 7. HTTP 表面（已确认）

- 当前只保留：`POST /runs`、`GET /runs/{run_id}`（可选 `?after_event_id=` 取增量）、`POST /runs/{run_id}/cancel`、`POST /upload`。
- `POST /upload` 会保留 basename，但把文件名中的 Unicode 空白归一成普通空格后再落盘并回传 `file_path`；实际落盘名为 `原名_上传时间戳(_n).ext`，返回里的 `name` 继续是清洗后的原始文件名。
- `POST /runs` 请求体走 pydantic 严格校验（`ConfigDict(extra="forbid")`）。
- 未知 run：`404 {"error":"Unknown run: <run_id>"}`。
- 已删除的旧语义：session 模块、`context_window`、旧 session 端点、`POST /files`、旧 `tool_call`/`tool_status`/`tool_result` 事件类型。

## 8. 类型与命名（已确认）

- **Protocol 使用边界**：`typing.Protocol` 只用于可注入能力边界 —— `Brain`、`BrainFactory`（均在 `dsagents/runtime/agent.py`）。默认实现从 `create_harness(...)` 追到 `DeepAgentsBrainFactory` 与 `default_tool_catalog()`。**不**为单实现小功能新增 Protocol/ABC。
- 外部框架要求继承时才继承框架基类：如 `ToolTelemetry(AgentMiddleware)`、`NoProgressMiddleware(AgentMiddleware)`（`dsagents/runtime/agent.py`）、`RunRequest(BaseModel)`（`dsagents/api.py`）。
- 简单值对象用 `@dataclass(frozen=True)`：`RunEvent`、`RunSnapshot`（`dsagents/runtime/runs.py`）、`ResourceConfig`、`ToolCatalog`（`dsagents/runtime/tools.py` / `resources.py`）。工具签名别名为 `ToolHandler = Callable[..., Any]`，不引入工具 ABC。
- 命名：模块/函数/方法 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`（如 `RUN_STATUSES`、`INTERRUPTED_RUN_ERROR`、`MAIN_AGENT_NAME`、`MAIN_AGENT_MODEL`、`NO_PROGRESS_WINDOW`、`MINERU_POLL_INTERVAL_SECONDS`、`ARTIFACT_REFERENCE_HINT`）。私有前缀 `_`。
- 顶层文件统一 `from __future__ import annotations`，类型注解广泛使用 `X | None`、`dict[...]`、`list[...]`、`tuple[...]`、`Sequence[...]`。

## 9. 运行时 stream 约定（已确认）

- Brain 调用统一走（`dsagents/runtime/execution.py`）：

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

  `FakeBrain.stream` 断言 `stream_mode` / `version` / `subgraphs` 完全匹配。
- payload **只**传当前请求的 `messages[]`；`text` block 原样保留，`artifact` block 在进入 Brain 前转成文本路径提示（`ARTIFACT_REFERENCE_HINT.format(path=...)`），不再直接透传 artifact block。
- run ledger 事件类型固定 **7 种**：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`。旧 `tool_call` / `tool_status` / `tool_result` 已删除。
- `model_usage` 是唯一的成本/缓存观测事件：在 `messages` 分支的 subagent 过滤**之前**提取终态 usage，每个模型调用仅在非空时写一次；payload 固定为 `{model, scope, agent_name, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}`。`model` 固定为常量 `MAIN_AGENT_MODEL = "MiniMax-M3"`。它不计入 `latest_content_event`，也不进 AgentState/checkpointer/store。
- 最终 AIMessage 同时含 `thinking` 与 `text` block 时，`assistant_message.payload` 保留最后一个 `thinking` 文本和最终 `text`；改动该结构必须同步测试断言。
- 临时 SubAgent 的 `messages` chunk 通过 `lc_agent_name` 过滤，不写公开 `thinking` / `text_delta`；但其 `model_usage` 仍按 `scope="subagent"` + 真实 `agent_name` 计入。
- `GET /runs/{run_id}` 顶层 `usage` 字段始终从该 run 全部 `model_usage` 事件汇总，不受 `after_event_id` 影响（定价细节见 STACK.md / INTEGRATIONS.md）。

## 10. 工具与 Skill 约定（已确认）

- `default_tool_catalog()`（`dsagents/runtime/tools.py`）当前**静态注册 6 个工具**，按声明顺序：`parse_documents`、`extract_archives`、`save_philips_wgq_extraction`、`generate_philips_wgq_import`、`save_tecan_extraction`、`generate_tecan_import`（2 个通用 MinerU/解压 + 每个 Skill 2 个业务）。`ToolCatalog` 是 `@dataclass(frozen=True)`，仅靠 `.as_list()` 暴露，**没有**工具 Protocol/ABC，**没有**插件平台，**没有**动态 Skill loader。
- 四个 SubAgent（`philips-wgq-extractor-a/b`、`tecan-extractor-a/b`）由 `workflow_subagents()`（`dsagents/runtime/agent.py`）装配；每个只挂 1 个业务工具（extraction 保存），写权限全 deny（`FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")`），并**各自显式装** `runtime_middlewares()`（声明式 SubAgent 不继承主 Agent middleware）。
- Skill 目录名 = Skill 名 = Python 包标识符（`philipswgqimport` / `tecanimport`，无连字符），故无需动态 loader；每个 Skill 含 `SKILL.md` + `references/` + `assets/` + `scripts/{tools.py, documents.py}`。Skill 通过 `/skills/` 虚拟路由只读挂载，主 agent 写权限对 `/skills/**` deny。
- 每个 Skill 只暴露两个业务 Tool：extraction 保存（`save_*_extraction`，返回 `{extractor, artifact_path}`）+ 一站式生成（`generate_*_import`，一次完成校验、canonical 构建、匹配、计算、模板写入与输出复核）。业务问题统一返回 `{"code":"input_problems","problems":[{"source","location","issue","action"}]}`；成功返回 `{"status":"generated","canonical_artifact","artifacts","manual_checks"}`。
- 不再有 `build_*_canonical` / `save_*_adjudication` / `generate_*_documents` / `needs_input` / `needs_c` / `needs_adjudication` / `info_source_preference` / `pn_info_source_overrides`。
- `/artifacts/downloads/` 中的业务 JSON/Excel 不覆盖、不原地编辑；`generate_*_import` 只接受显式 artifact 路径。

## 11. 持久化约定（已确认）

- `runs` 行 = 当前 run 快照（状态机见 §6）。
- `run_events` = append-only 事件流。
- 时间字段统一写 UTC ISO-8601 毫秒（`%Y-%m-%dT%H:%M:%S.<ms>Z`，如 `2026-07-13T08:18:59.250Z`）；fresh schema，无迁移代码。
- 大 payload/raw（默认 `max_inline_bytes=262_144`，256 KiB）外溢到 `data/internal/run-events/*.json`，行内只留指针 `{"artifact_path":..., "bytes":...}`；`data/artifacts/` 继续只保留用户可见的 `uploads/` 与 `downloads/`。
- 数据目录固定锚定在 `backend/data/`（与 CWD 无关）。
- 不做清理策略、不做历史迁移。部署切换 = 停服务 + 整体清空 `backend/data/`（runs/events/checkpoints/store/uploads/downloads 全清，fresh schema）。

## 12. 中间件约定（已确认）

- 运行时恰好两个 middleware，由 `runtime_middlewares()` 返回新实例列表：
  - `ToolTelemetry`（`wrap_tool_call`）：发 `tool_execution` 三态 + 计时 + scope 路径。
  - `NoProgressMiddleware`（`before_model`）：连续 3 次（`NO_PROGRESS_WINDOW`）同一 `tool + 归一化 args` 抛 `NoProgressLoop`。
- 主 Agent 与每个 SubAgent 都各自装这两个 middleware（声明式 SubAgent 不继承主 Agent middleware）。
- **不使用**：`ToolCallLimitMiddleware`、`wrap_model_call`、`before_agent`/`after_agent`、自定义 state schema、自定义 stream transformer、v3 stream、sandbox / 脚本执行。

## 13. LLM / Brain 约定（已确认）

- 默认 `DeepAgentsBrainFactory`：从 `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 环境变量构造，映射到 LangChain 的 Anthropic 客户端（`init_chat_model("anthropic:...", api_key=..., base_url=..., thinking={"type":"adaptive"})`）。模型实例可被 `model` 参数注入覆盖。
- 当前 `deepagents==0.6.12` 通过 `skills` / `subagents` / `permissions` / `response_format` / `name` / `middleware` 装配；同时通过 `register_harness_profile("anthropic", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))` 关闭其自动添加的第五个通用 SubAgent，保留四个显式 workflow extractor。
- **硬规则（版本锁定）**：以锁定版本签名为准（`deepagents>=0.6.12` / `langchain-anthropic>=1.4.8`），不写面向未来版本的兼容层或参数 shim。升级 `deepagents` 前，必须先验证目标版本是否真正暴露该参数/Profile 字段。
- 普通本地测试用 `FakeBrainFactory` / `FakeBrain`：验证 Brain 可替换、payload 接收当前请求的 `messages[]`、`thread_id` 路由、`updates + subgraphs + v2` stream、失败 run 后同 thread 续跑不回滚。

## 14. 文档与交付约定

- **事实层在子项目**：`backend/.planning/codebase/` 是 backend 实现细节的事实来源；根级只放导航与稳定全局原则。
- **改代码后同步事实层**：改 `backend/` 实现后，先更新本目录对应文档，再视影响回看 `ARCHITECTURE.md` / `coding_maps/SYSTEM_MAP.md`。
- **文档语言**：简体中文说明性正文；保留代码标识符、文件路径、命令、配置键、API 名称、IP/端口原文。
- **配置键记录规则**：文档只记录配置键名与用途（如 `MINIMAX_MODEL` / `MINERU_BASE_URL` / `ORACLE_CLIENT_LIB_DIR`），**不**把本地 `.env` 值写进长期文档。
- **不外泄密钥**：文档不写入任何密钥 / token / 连接串。
- **证据不足标注**：用「需确认 / 初步判断」表达，不写成硬规则。
