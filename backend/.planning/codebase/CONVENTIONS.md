# CONVENTIONS

> backend 子项目的开发约定。事实来源 = `backend/` 源码 + `pyproject.toml` + 根级 `docs/conventions.md`；本轮刷新（2026-07-11）已核对当前工作树：11 个顶层模块、10 个默认工具、`default_tool_catalog()`、Protocol 边界、run-first HTTP/ledger 约定均与代码逐一比对。
> 区分「已确认」（直接对应代码）与「需确认」（证据不足）。

## 1. 包管理器（已确认）

- 包管理器是 `uv`，**不是 pip**。安装/同步：

  ```powershell
  cd backend
  uv sync
  ```

- 锁文件：`backend/uv.lock`（已确认存在）。
- 打包后端：setuptools（`pyproject.toml` 中 `[build-system]` 用 `setuptools.build_meta`，`requires = ["setuptools>=68"]`），通过 `py-modules` 把扁平 `.py` 注册为顶层模块。
- **禁止**用 `pip install -e .` 之类绕过 `uv`（与 `uv.lock` 不一致）。
- 当前 `requires-python = ">=3.11,<4.0"`；核心运行时依赖（版本下限）见 `pyproject.toml`，如 `deepagents>=0.6.12` / `langchain-anthropic>=1.4.8` / `langchain>=1.3.11` / `langgraph>=1.2.7` / `fastapi>=0.116.1` / `uvicorn>=0.35.0` / `openpyxl>=3.1,<4` / `oracledb>=3,<4` / `python-multipart>=0.0.20` / `httpx2>=2.5.0`。
- `pyproject.toml` 当前**没有**任何 `[tool.ruff]` / `[tool.mypy]` / `[tool.black]` / `[tool.pytest...]` / `[tool.coverage]` 段（已确认），即未配置 lint / type-check / pytest 门禁。验证只靠测试脚本（见 TESTING.md）。

## 2. 模块组织（已确认）

- 扁平顶层模块：`backend/` 下的 `.py` 直接作为顶层模块，绝对导入写作 `from hands import ...`、`from resources import AgentResources`、`from run_ledger import RunEvent`，**不**带 `backend.` 前缀。测试侧通过 `from tests.test_support import ...` 引用共享替身。
- `pyproject.toml` 的 `py-modules` 当前显式列出 11 个：`api` / `artifact_names` / `hands` / `harness` / `philips_wgq_import` / `resources` / `run_ledger` / `subagents` / `tecan_import` / `tools` / `workflow_artifacts`。新增顶层 `.py` 必须同步追加。

  **PR 审查 checklist（py-modules 同步）**：
  1. 本次 PR 是否在 `backend/` 根新增了任何顶层 `.py` 文件？
  2. 若是，是否已把模块名（不含 `.py`）追加到 `pyproject.toml` 的 `[tool.setuptools].py-modules` 列表？
  3. 是否已确认 `uv sync` 后能从全新 venv 通过绝对导入（如 `from hands import ...`）成功导入该模块？

  > 漏配的后果：开发环境因源码在 `PYTHONPATH` 上可正常导入；生产安装（`uv pip install .`）后该模块不会被打包，绝对导入 `from hands import ...` 会以 `ModuleNotFoundError` 失败。
- **没有** `backend/__init__.py` / `backend/__main__.py`（已确认不存在）。
- **没有** `python -m backend.*` 这种调用方式（包不是这么组织的）。
- backend 测试源码目录是 `backend/tests/`，带 `tests/__init__.py`（使其成为可被 `python -m tests.test_xxx` 导入的包）；测试脚本统一命名为 `test_*.py`，可执行脚本保留 `run()` 并支持 `python -m tests.test_xxx` 直接运行（见 TESTING.md）。
- 数据目录固定锚定在 `backend/` 下，与 CWD 无关：`resources.py` 中 `_BACKEND_DIR = Path(__file__).resolve().parent`。

## 3. 运行入口（已确认）

| 场景 | 入口 |
| --- | --- |
| HTTP 上传文件 | `POST /upload`（multipart 字段名 `files`，支持 1 个或多个文件） |
| HTTP 提交 run | `POST /runs`（body `{session_id?, messages[]}`），轮询 `GET /runs/{run_id}` |
| 测试脚本 / 主要验证 | 按影响范围运行 `cd backend && python -m tests.test_xxx` |
| 启动 HTTP 服务 | `cd backend && uv run uvicorn api:app --host 0.0.0.0 --port 8500`（与根级 `scripts/start-backend.bat` 完全一致；端口与 `tests/test_real_image_run.py` 的 `DEFAULT_BASE_URL = "http://127.0.0.1:8500"` 一致。注意 `test_minimax_cache_baseline.py` 默认走 8000，见 TESTING.md） |
| 程序内调用 | `AgentResources(config)` + `create_harness(resources).execute_run(messages, session_id, run_id)` |

- **没有** `from session import run_session`（已确认 grep 无此导入，且无 `session.py`）。Session 概念已移除，改由 `thread_id=session_id` + run ledger 承载。

## 4. 核心运行时原则（来自根级 `docs/conventions.md`，在代码中落地）

- **能力可插拔**：`Brain` / `BrainFactory` / `Hands` 是 `typing.Protocol`（定义点见 `harness.py` 的 `Brain` / `BrainFactory`、`hands.py` 的 `Hands`）；运行时通过依赖注入接收 `brain_factory`、`hands`、`tools`。`create_harness` 用默认实现，本地测试用 `FakeBrainFactory` 注入。工具保持普通 callable + `ToolCatalog`，**不**为单实现工具新增 Protocol/ABC。
- **run 是事件源**：`run_events` 表 append-only；`runs` 表是当前快照。短期上下文靠 LangGraph `thread_id=session_id`（经 `checkpointer` + `store`），不再有 session 层。`values` snapshot 只保留在 raw，外部消费规范化事件。
- **保持运行时薄**：`HarnessRuntime.execute_run` 只做「派发 payload → 解析 stream chunk → 写 run event」。不在运行时内引入服务层 / 工作流引擎。
- **业务状态外置为 artifact**：A/B/C、裁决、canonical 与 Excel 只写唯一新文件；builder/generator 依靠调用方显式传路径，不扫描 session、历史上传或“最近任务”。
- **按业务保留确定性规则**：Philips/Tecan 各自实现投票与 Excel 规则，不抽象通用 A/B 引擎、插件注册表或工作流 DSL；共享模块（`workflow_artifacts`、`artifact_names`）只做路径和 JSON 读写。
- **真实错误透传**：见 §5。
- **优先删减范围**：HTTP 表面只保留 `POST /runs` / `GET /runs/{run_id}` / `POST /upload`（旧 session 端点与 `POST /files` 已删，见 §7）。

## 5. 错误处理（已确认）

- **真实错误透传，不吞**：
  - `ToolStatusMiddleware.wrap_tool_call`（`hands.py`）：捕获工具异常后先发 `error` status，再 `raise` 透传。
  - `HarnessRuntime.execute_run`：捕获 Brain 异常 → 发 `failed` run status（带 `error=_error_text(exc)` 与 `raw={..., "error": repr(exc)}`），不掩盖。
  - `api._run_background`：未捕获异常 → `_ensure_failed_run` 把 run 标记 `failed` 并写 `repr(exc)`。
- `_error_text(exc)` = `str(exc)` 去空白，为空则回退到异常类名。
- fail-fast 模式：`parse_documents` 在存在可提交文件且缺 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 时 `raise RuntimeError("Missing required environment variable: ...")`；`MINERU_EFFORT` 可留空（测试脚本显式断言此行为）。
- run 状态非法值：`SqliteRunLedger.emit_run_status` 对非 `RUN_STATUSES` 抛 `ValueError`。
- Oracle 是明确例外：Philips 单位查询失败必须转成人工校验并继续生成（单元格写 `"需确认：申报计量单位"`，已由 `test_philips_wgq_import.py` 断言）；其它业务合同、路径和工作簿错误保持可见，不由 harness 吞掉。

## 6. run-first 架构（已确认）

- 已移除 session 层：无 `session.py`、无 `from session import run_session`、无 `run_turn` / `stream_turn`。
- 并发模型：同一 `session_id` 通过 `threading.Lock` 串行；冲突返回 `409 {"error":"该会话正在运行","active_run_id":...}`。
- 启动恢复：`fail_incomplete_runs` 在 app lifespan 启动时把遗留 `queued/running` run 标记 `failed`（错误文案 `INTERRUPTED_RUN_ERROR = "执行已中断，请重试"`）。
- 失败 run **不**回滚 thread；下一次同 `session_id` 继续用同一 `thread_id`（`FakeBrain` 用 `threads` dict 按序号验证此行为）。

## 7. HTTP 表面（已确认）

- 当前只保留：`POST /runs`、`GET /runs/{run_id}`（可选 `?after_event_id=` 取增量）、`POST /upload`。
- `POST /upload` 会保留 basename，但把文件名中的 Unicode 空白（如 `NBSP`、`\t`）归一成普通空格后再落盘并回传 `file_path`；实际落盘名为 `原名_上传时间戳(_n).ext`，返回里的 `name` 继续是清洗后的原始文件名，避免模型/tool call 因不可见空白或物理名污染而找不到文件。
- `POST /runs` 请求体走 pydantic 严格校验：`RunRequest` / `RunMessage` / `TextBlock` / `ArtifactBlock` 均为 `ConfigDict(extra="forbid")`，旧的单数 `message` 字段返回 `422`。
- 未知 run：`404 {"error":"Unknown run: <run_id>"}`。
- 已删除的旧语义：`session.py`、`context_window`、`RemoveMessage(REMOVE_ALL_MESSAGES)`、`run_turn`/`stream_turn`、`TraceHands`、旧 session 端点、`POST /files`（返回 `404`，由 `test_api.py` 断言）。

## 8. 类型与命名（已确认）

- **Protocol 使用边界**：`typing.Protocol` 只用于可注入能力边界 —— `Brain`、`BrainFactory`（均在 `harness.py`）、`Hands`（在 `hands.py`）。默认实现从 `create_harness(...)` 追到 `DeepAgentsBrainFactory`、`ToolStatusHands`、`default_tool_catalog()`。**不**为单实现小功能新增 Protocol/ABC。
- 外部框架要求继承时才继承框架基类：如 `ToolStatusMiddleware(AgentMiddleware)`（`hands.py`）、`RunRequest(BaseModel)`（`api.py`）、`ToolStatusHands` 是 `Hands` Protocol 的结构性实现（不显式继承）。
- 简单值对象用 `@dataclass(frozen=True)`：`RunEvent`、`RunSnapshot`（`run_ledger.py`）、`ResourceConfig`、`ToolCatalog`（`tools.py` / `resources.py`）。工具签名别名为 `ToolHandler = Callable[..., Any]`，不引入工具 ABC。
- 命名：模块/函数/方法 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`（如 `RUN_STATUSES`、`INTERRUPTED_RUN_ERROR`、`DEFAULT_SYSTEM_PROMPT`、`MAIN_AGENT_NAME`、`MAIN_AGENT_MODEL`、`SKILLS_SOURCE`、`ARTIFACT_REFERENCE_HINT`）。私有前缀 `_`（如 `_BACKEND_DIR`、`_error_text`、`_PRICING_TIERS`、`_PRICEABLE_MODELS`、`_TIER_THRESHOLD_INPUT_TOKENS`）。
- 顶层文件统一 `from __future__ import annotations`（`api.py` / `hands.py` / `harness.py` / `resources.py` / `run_ledger.py` / `tools.py` 及测试文件均已确认），类型注解广泛使用 `X | None`、`dict[...]`、`list[...]`、`tuple[...]`、`Sequence[...]`。

## 9. 运行时 stream 约定（已确认）

- Brain 调用统一走 `brain.stream({"messages": normalized_messages}, config={"configurable":{"thread_id":session_id}}, stream_mode=["messages","custom","values"], version="v2")`（`FakeBrain.stream` 断言 `stream_mode` / `version` 完全匹配）。
- payload **只**传当前请求的 `messages[]`；`text` block 原样保留，`artifact` block 会在进入 Brain 前转成文本路径提示（`ARTIFACT_REFERENCE_HINT.format(path=...)`，由 `test_api.py` 断言 Brain 侧只收到 text blocks，证明不再直接透传 artifact block，也不再注入 `RemoveMessage`）。
- run ledger 事件类型固定 8 种：`status` / `thinking` / `text_delta` / `assistant_message` / `tool_call` / `tool_status` / `tool_result` / `model_usage`。
- `model_usage` 是唯一的成本/缓存观测事件：在 `messages` 分支的 subagent 过滤**之前**提取终态 `AIMessageChunk.usage_metadata`，每个模型调用仅在非空时写一次；payload 固定为 `{model, scope, agent_name, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}`。`cache_creation_input_tokens` 汇总 `input_token_details.cache_creation + ephemeral_5m_input_tokens + ephemeral_1h_input_tokens`（库在 5m/1h 存在时会把 generic 置 0，相加安全）。`model` 固定为常量 `MAIN_AGENT_MODEL = "MiniMax-M3"`。它不计入 `latest_content_event`，也不进 AgentState/checkpointer/store。
- `raw.type=="values"` 只保留在原始 snapshot；业务层从 snapshot 中派生 `tool_call` / `tool_result` / `assistant_message`，不再把 `values` 当作业务事件写库，外部调用方也不应依赖 `values` 事件。
- 最终 AIMessage 同时含 `thinking` 与 `text` block 时，`assistant_message.payload` 保留最后一个 `thinking` 文本和最终 `text`；改动该结构必须同步 `INTERFACES.md`、`SYSTEM_MAP.md` 和 `tests/test_api.py` / `tests/test_harness.py` 断言。
- 临时 subagent 的 `messages` chunk 通过 `lc_agent_name` 过滤，不写公开 `thinking` / `text_delta`；但其 `model_usage` 仍按 `scope="subagent"` + 真实 `agent_name` 计入。task 调用、task 结果和 artifact 路径仍按现有 snapshot 规则进入事件。
- 工具状态中间件只发 `started` / `completed` / `error` 三态（`test_harness.py` 断言）。
- `GET /runs/{run_id}` 顶层 `usage` 字段始终从该 run 全部 `model_usage` 事件汇总，不受 `after_event_id` 影响；含 `model_calls`、四类 token 总量、`cache_hit_rate`（无输入为 `null`）、`estimated_cost_cny` / `estimated_savings_cny`（按调用 input ≤/>512k 分 tier 后汇总）、`pricing_as_of`（当前 `PRICING_AS_OF = "2026-07-12"`）、估算说明与 `by_agent` 分项。模型不可计价（不在 `_PRICEABLE_MODELS = {"MiniMax-M3"}`）时金额为 `null`，token 原始事实保留。tier 阈值 `_TIER_THRESHOLD_INPUT_TOKENS = 512 * 1024`。

## 10. 工具与 Skill 约定（已确认）

- `default_tool_catalog()`（`tools.py`）当前注册 **10 个工具**，按声明顺序：`parse_documents`、`extract_archives`、`save_philips_wgq_extraction`、`build_philips_wgq_canonical`、`save_philips_wgq_adjudication`、`generate_philips_wgq_documents`、`save_tecan_extraction`、`build_tecan_canonical`、`save_tecan_adjudication`、`generate_tecan_documents`（2 个通用 MinerU/解压 + 4+4 Philips/Tecan 业务）。`ToolCatalog` 是 `@dataclass(frozen=True)` 的 `tuple[ToolHandler, ...]`，仅靠 `.as_list()` 暴露，**没有**工具 Protocol/ABC。
- 四个 subagent（`philips-wgq-extractor-a/b`、`tecan-extractor-a/b`）由 `subagents.workflow_subagents()` 装配；每个只挂 1 个业务工具，写权限全 deny（`FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")`），并使用 `ToolStrategy` 做 structured response（由 `test_workflow_setup.py` 断言）。
- Skill 根固定为 `backend/skills/`（当前两个 Skill：`philips-wgq-import` / `tecan-import`），通过 `/skills/` 虚拟路由挂载（`SKILLS_SOURCE = "/skills/"`）；主 agent 写权限对 `/skills/**` deny，extractor 的内置文件能力为只读。
- `SKILL.md` 行数上限断言为 `<= 100`（`test_workflow_setup.py` 当前断言值；实际两个 `SKILL.md` 分别约 42 行 / 39 行，远低于上限）。字段/规则只放一层 `references/`，模板放同 Skill 的 `assets/`。普通 PDF 阅读/通用抽取不属于业务 Skill 触发条件（`DEFAULT_SYSTEM_PROMPT` 明确）。
- extraction/canonical/adjudication 都采用当前严格合同，不提供旧 envelope 或字段别名；缺失字段用正常 status 返回，不增加图 state schema、HTTP 恢复接口或持久化游标。
- `/artifacts/downloads/` 中的业务 JSON/Excel 不覆盖、不原地编辑；generator 只接受 canonical artifact 路径。

## 11. 持久化约定（已确认）

- `runs` 行 = 当前 run 快照（状态机：`queued→running→succeeded|failed`）。
- `run_events` = append-only 事件流。
- 大 payload/raw（默认 `max_inline_bytes=262_144`，256 KiB）外溢到 `data/internal/run-events/*.json`，行内只留指针 `{"artifact_path":..., "bytes":...}`；`data/artifacts/` 继续只保留用户可见的 `uploads/` 与 `downloads/`。
- 数据目录固定锚定在 `backend/data/`（`_BACKEND_DIR = Path(__file__).resolve().parent`，与 CWD 无关）。
- 不做清理策略、不做历史迁移。

## 12. LLM / Brain 约定（已确认）

- 默认 `DeepAgentsBrainFactory`：从 `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 环境变量构造，映射到 LangChain 的 Anthropic 客户端（`init_chat_model("anthropic:...", api_key=..., base_url=..., thinking={"type":"adaptive"})`）。模型实例在 `__init__` 内解析，可被 `model` 参数注入覆盖。
- 当前 `deepagents==0.6.12`（`>=0.6.12`）通过 `skills` / `subagents` / `permissions` / `response_format` / `name` 装配；同时通过 `register_harness_profile("anthropic", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))` 关闭其自动添加的第五个通用 subagent，保留四个显式 workflow extractor。官方新文档与该版本不一致处以实际签名为准，不写未来兼容层。
- **硬规则（版本锁定）**：以锁定版本签名为准（`deepagents>=0.6.12` / `langchain-anthropic>=1.4.8`），不写面向未来版本的兼容层或参数 shim。升级 `deepagents` 前，必须在 `harness.py` `register_harness_profile`（及调用处）先验证目标版本是否真正暴露该参数/Profile 字段，确认后再切换，禁止盲改。
- 普通本地测试用 `FakeBrainFactory` / `FakeBrain`：验证 Brain 可替换、payload 接收当前请求的 `messages[]`、`thread_id` 路由、失败 run 后同 thread 续跑不回滚。

## 13. 文档与交付约定（来自根级 `docs/conventions.md`）

- **事实层在子项目**：`backend/.planning/codebase/` 是 backend 实现细节的事实来源；根级只放导航与稳定全局原则。
- **改代码后同步事实层**：改 `backend/` 实现后，先更新本目录对应文档，再视影响回看 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`。
- **文档语言**：简体中文说明性正文；保留代码标识符、文件路径、命令、配置键、API 名称、IP/端口原文。
- **配置键记录规则**：文档只记录配置键名与用途（如 `MINIMAX_MODEL` / `MINERU_BASE_URL` / `ORACLE_CLIENT_LIB_DIR`），**不**把本地 `.env` 值写进长期文档。
- **不外泄密钥**：文档不写入任何密钥 / token / 连接串。
- **证据不足标注**：用「需确认 / 初步判断」表达，不写成硬规则。
