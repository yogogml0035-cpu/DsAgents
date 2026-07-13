# SYSTEM_MAP

> 系统层跨子项目理解手册。本文件只描述系统形态、边界与读图指南；底层实现细节以 [`backend/.planning/codebase/`](../backend/.planning/codebase/) 为事实来源。
> 上游事实：[`ARCHITECTURE.md`](../ARCHITECTURE.md)、[`INTERFACES.md`](../INTERFACES.md)、[`AGENTS.md`](../AGENTS.md)。
> 本轮刷新（2026-07-13）已核对当前工作树与全部 backend 事实文档（同日刷新）：旧扁平顶层模块（`backend/*.py`）与旧带连字符 `skills/` 目录已删除，整个产品收口进 Python 包 `backend/dsagents/`（子包 `runtime/` / `integrations/` / `skills/`），Philips/Tecan 业务代码归属各自内置 Skill 包；HTTP 改为四端点（含 cancel），事件 schema 改为 7 类，业务 Tool 收敛为每 Skill 2 个，调用链、provider 边界、按任务阅读指南与集成风险清单均按当前源码重述。

## 1. 系统目的和仓库形态

DsAgents 是一个 **agent 运行时底座**：把能力（Brain、工具）做成可插拔，而不绑定具体 runner、容器、模型或工作流。整个产品收口在一个 Python 包 `dsagents/`（绝对包内导入 `from dsagents.runtime import ...`）。

- **形态**：单子项目仓库，唯一产品子项目是 `backend/`（安装 Python 包 `dsagents/`，包管理器 `uv`）。无前端子项目（当前源文档未确认任何前端代码归属本仓库）。
- **架构**：run-first。run 是唯一的执行单位与查询单位，`run_events` 表 append-only，`runs` 表是事件投影出的快照；不再有 session 模块 / session 持久化层。
- **短期上下文**：完全交给 LangGraph `checkpointer` + `thread_id=session_id`，仓库不再自建 session 事件回放。`session_id` 标识符保留，但用途已收窄为 checkpointer 键和进程内串行保护键，不再是一等持久化对象。
- **能力可插拔**：`Brain` / `BrainFactory` 是 `typing.Protocol`（`dsagents/runtime/agent.py`）；工具保持普通 callable + `ToolCatalog`（`dsagents/runtime/tools.py`）。默认装配从 `create_harness` 进入（`DeepAgentsBrainFactory` + `default_tool_catalog()`），本地测试用 `FakeBrainFactory` 替换。运行时不写死具体模型实现。
- **工具静态注册**：`default_tool_catalog()` 静态注册 6 个工具（2 个 MinerU 通用 + 每个 Skill 2 个业务），通过普通 Python import 拉入 Skill 工具；不自动扫描、无插件平台、无动态模块加载器。
- **业务能力按 Skill 打包**：两个内置 Skill 包 `dsagents/skills/philipswgqimport/` 与 `dsagents/skills/tecanimport/`，每个仅暴露 2 个业务 Tool。4 个声明式 SubAgent（`workflow_subagents()`）各自装自己的 middleware。
- **入口形态**：HTTP（`POST /runs` 创建 run、立即返回 `queued`；纯轮询获取增量事件，无 SSE；含 cancel）+ 程序内组合（`AgentResources` + `create_harness(...).execute_run(...)`）；无单函数 one-shot API。
- **业务能力形态**：模型按明确业务目标加载 Skill；A/B/C、裁决、canonical 与 Excel 以显式 artifact 路径串联，不增加业务 HTTP、状态表或恢复接口。

详细运行时原则与维护规则见根级 [`docs/conventions.md`](../docs/conventions.md)（`AGENTS.md` 要求改动 backend 前必读）。

## 2. 子项目职责表

| 子项目 | 目录 | 当前职责 | 技术栈要点 | 边界 |
|--------|------|----------|------------|------|
| backend | `backend/` | 安装 Python 包 `dsagents/`（子包 `runtime/` / `integrations/` / `skills/`）：run-first runtime + 两个内置 Skill 包 + 4 个声明式 SubAgent + 两个运行时 middleware + 6 个静态注册工具 | Python `>=3.11,<4.0`；`uv`；FastAPI；DeepAgents/LangGraph；SQLite；MinerU（内网 HTTP）；openpyxl；可选 oracledb（thick mode） | 不提供 session/业务状态表、SSE、鉴权/CORS、通用工作流引擎、跨进程队列、沙箱 / 脚本执行、插件平台 |

## 3. 跨子项目调用链和数据流

当前是**单子项目**，以下描述 backend 内部主调用链与外部 provider 边界（详细分层见 [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) §3）。

### 3.1 主调用链（HTTP 入口）

```text
POST /upload  multipart files[]
  └─ 保存到 /artifacts/uploads/<cleaned-stem>_<upload-ts>(_n).ext，返回元数据

POST /runs  {messages, session_id?}
  ├─ session_id 为空 → 生成 uuid4().hex；run_id = uuid4().hex
  ├─ 进程内按 session_id 取 threading.Lock（单飞锁）；冲突 → 409
  ├─ resources.runs.create_run(run_id, session_id, input_messages_json)   # runs.py
  ├─ 起 daemon 线程 → HarnessRuntime.execute_run(messages, session_id, run_id)
  └─ 立即返回 {run_id, session_id, status:"queued"}

HarnessRuntime.execute_run(...)   # dsagents/runtime/execution.py
  ├─ emit status=running
  ├─ 归一化 content blocks：
  │    ├─ text     → 原样保留
  │    └─ artifact → "Uploaded artifact: /artifacts/uploads/..."  (ARTIFACT_REFERENCE_HINT)
  ├─ brain_factory.create(resources, middleware=runtime_middlewares(), tools=tools.as_list())
  ├─ brain.stream({"messages": normalized_messages},
  │                config={"configurable":{"thread_id":session_id}},
  │                stream_mode=["messages","custom","updates"],
  │                subgraphs=True,
  │                version="v2",
  │                control=RunControl())
  │    ├─ messages chunk → 先在 subagent 过滤之前提取 model_usage（覆盖主 agent + subagent 调用），再仅主 agent thinking / text_delta
  │    │                  （subagent 文本 token 按 lc_agent_name 过滤丢弃，但 subagent 的 model_usage 仍计入）
  │    ├─ custom   chunk → tool_execution（ToolTelemetry.wrap_tool_call，started/completed/error + 计时 + scope）
  │    │                  + tool_progress（parse_documents / extract_archives 自发提交/轮询/下载进度）
  │    └─ updates  chunk → _update_events 派生 assistant_message / tool_execution
  │                        （assistant_message 保留最终 AIMessage 的最后一个 thinking 文本与最终 text；同时更新 reply 候选）
  ├─ 成功 → emit status=succeeded(reply=...)
  ├─ GraphDrained → emit status=cancelled   （来自 POST /runs/{id}/cancel 的 RunControl drain）
  └─ 异常 / NoProgressLoop → emit status=failed(error=...)（真实错误透传，不吞）

GET /runs/{run_id}?after_event_id=N  → 读 runs 快照 + 增量 run_events + latest_content_event + usage
POST /runs/{run_id}/cancel            → 协作 drain；未知 404 / 终态 409 / 已 cancelling/cancelled 200 / 活跃 202
```

业务分支由同一主链中的工具调用完成（流程由对应 `SKILL.md` 指令驱动）：

```text
parse_documents (dsagents/integrations/mineru.py)
  → 主 agent 回合并行 A/B 声明式 SubAgent（workflow_subagents()，各自装 middleware）
  → 每个 SubAgent 调 save_*_extraction 保存 extraction artifact（save_philips_wgq_extraction / save_tecan_extraction）
  → 必要时 extractor C 回查；A/B/C 仍冲突则主 agent 形成最小 decisions
  → 主 agent 调 generate_*_import（extraction_artifacts + tracking_artifact? + international_forwarder? + customs_mode? + decisions）
      ├─ 成功 → 一次性 canonical 构建 / 匹配 / 计算 / Excel 写入 / 输出复核
      │         返回 {status:generated, canonical_artifact, artifacts, manual_checks}
      └─ 业务问题 → 返回 {code:input_problems, problems:[{source,location,issue,action}]}
                    run 结束，用户修正材料后重新显式传路径
```

- **事件获取靠轮询**，当前无 `StreamingResponse` / `text/event-stream`（[`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §1）。
- 事件类型固定 7 类：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`。旧 `tool_call`/`tool_status`/`tool_result` 已删除。
- run 状态机：`queued → running → succeeded | failed`；`queued → cancelled`；`running → cancelling → cancelled`。启动恢复把遗留 `queued/running/cancelling` 标 `failed("执行已中断，请重试")`。
- 程序内等价路径：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(...)` → `Iterator[RunEvent]`。

### 3.2 外部 provider 边界

| 边界 | 用途 | 集成方式 | 证据 |
|------|------|----------|------|
| MiniMax via Anthropic adapter（生产） | LLM | `DeepAgentsBrainFactory` 用 `init_chat_model("anthropic:<MINIMAX_MODEL>", api_key=..., base_url=..., thinking={"type":"adaptive"})` → `ChatAnthropic`，注入 `create_deep_agent(...)`；实际端点指向 MiniMax（OpenAI/Anthropic 兼容） | `dsagents/runtime/agent.py` |
| MinerU（内网 HTTP） | 文档解析（`parse_documents` 工具）+ ZIP 解压（`extract_archives` 工具） | `dsagents/integrations/mineru.py` 用 `requests` 一次 `POST {MINERU_BASE_URL}/tasks` 提交多文件、轮询 `GET {status_url}`（默认每 `MINERU_POLL_INTERVAL_SECONDS=30.0` 秒）、`GET {result_url}` 取 JSON/ZIP | `dsagents/integrations/mineru.py` |
| Oracle（可选） | Philips 计量单位查询 | 仅在 `ORACLE_DSN/USERNAME/PASSWORD` 齐备 + `ORACLE_CLIENT_LIB_DIR` 指向有效 instant client 时由 `oracledb` thick mode 查询；缺配置/失败继续生成并标人工校验 | `dsagents/skills/philipswgqimport/scripts/tools.py` |
| LangGraph savers | checkpointer / store 持久化 | `SqliteSaver`（`thread_id=session_id`）/ `SqliteStore`（`namespace=("dsagents",)`，本地 SQLite） | `dsagents/runtime/resources.py` |
| DeepAgents / LangGraph runtime | 协作 drain | `langgraph.runtime.RunControl`（per-run，存于 `HarnessRuntime.run_controls: dict[run_id → RunControl]`）；`GraphDrained` → `cancelled` | `dsagents/runtime/execution.py` |

provider/集成键名（不含值）见 [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §2/§5/§6 与 [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md) §5。

## 4. 接口边界

### 4.1 HTTP API 边界

| 方法 / 路径 | 行为 | 返回 |
|---|---|---|
| `POST /runs` | body `{messages, session_id?}`；`messages[]` 的 `content` 只接受 `text` / `artifact` blocks（`RunRequest` 用 `ConfigDict(extra="forbid")`）；同 session 已有运行中 run → `409` | `200 {run_id, session_id, status:"queued"}`；校验失败 `422` |
| `GET /runs/{run_id}` | query `after_event_id?`；未知 run → `404` | `200 {run, events[], latest_content_event, usage}`（`usage` 始终从该 run 全部 `model_usage` 事件汇总，无模型调用时为 `null`） |
| `POST /runs/{run_id}/cancel` | 协作 drain；未知 `404` / 终态（`succeeded`/`failed`）`409` / 已 `cancelling`/`cancelled` `200` / 活跃 run `202` | `202 {status:"cancelling"}` 等 |
| `POST /upload` | multipart `files[]`；支持一个或多个文件；只保存不解析 | `200 {files:[{file_path,name,mime_type,size}]}` |

完整契约（请求/响应 JSON 形状、错误码、取消流细节）见 [`INTERFACES.md`](../INTERFACES.md) §1/§2 与 [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §1。明确**已删除**的旧 session 接口清单亦见 [`INTERFACES.md`](../INTERFACES.md) §1。
`after_event_id` 只裁剪 `events[]`，不会影响 `latest_content_event`，也不会影响 `usage`（两者都按 run 全量计算）。

`dsagents/api.py` 通过 `create_app(*, resource_config=None, harness_factory=create_harness)` 工厂构造 FastAPI 应用，支持注入测试用的 `ResourceConfig` 与 `Brain` 工厂（本地测试用 `FakeBrainFactory`）；模块级 `app = create_app()` 是生产装配。启动命令 `uvicorn dsagents.api:app --host 0.0.0.0 --port 8500`（无 `--reload`）。
`assistant_message.payload` 的公开形状可包含最终 `thinking` 与 `text`，来自 `updates` channel 派生的最终 AIMessage；调用方不应直接依赖 LangGraph `values` 事件类型（旧的 values-snapshot 去重已删除）。
声明式 subagent 的模型文本 token 不形成公开 `thinking`/`text_delta`，但 subagent 的 `model_usage` 仍计入（在过滤之前提取）；`tool_execution` 载荷含 scope 路径以重建「主 Agent → SubAgent → Tool」调用链。

### 4.2 LLM provider 边界

- 生产 Brain 强耦合 Anthropic 客户端协议与 `thinking={"type":"adaptive"}`（`init_chat_model("anthropic:...")`）；环境变量 `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 由 `dsagents/runtime/agent.py` 在导入时 `load_dotenv` 加载。
- **不新增自定义 cache middleware**：`create_deep_agent` 尾栈自动挂 `AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore")`，因 MiniMax 走 `ChatAnthropic` 对 MiniMax-M3 生效；固定前缀不可注入动态内容（时间/run_id 等）。
- 本地测试 Brain `FakeBrain` 不触达真实 provider。
- `register_harness_profile("anthropic", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))` 在进程级全局禁用 DeepAgents 默认 general-purpose subagent，只保留 `workflow_subagents()` 的四个 extractor。锁定的 `deepagents==0.6.12` 不支持构造参数形式的 `harness_profile=...`，需用该 profile 注册 API；升级依赖时需重新核对。

### 4.3 持久化边界

`backend/data/` 固定三条**逻辑** SQLite 通道（`runs`/`store`/`checkpoints`，文件按需创建，互不共享连接），完整文件→通道→写入方映射、表结构与 `CompositeBackend` 路由规则详见 [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) §9。run ledger 时间字段统一写 UTC ISO-8601 毫秒（如 `2026-07-13T08:18:59.250Z`），fresh schema、无迁移代码（部署切换时停服务、清空整个 `backend/data/`）。大 run event payload/raw 外溢到 `data/internal/run-events/*.json`（`max_inline_bytes=262_144`）。

### 4.4 文件 / artifacts 边界

- 上传：`POST /upload` → `data/artifacts/uploads/<cleaned-stem>_<upload-ts>(_n).ext`，返回虚拟路径与元数据数组（`make_timestamped_name` 同请求共用 batch 时间戳，只在物理重名时追加序号；`clean_filename` 清洗）。
- 工具层 `dsagents/integrations/artifacts.py` 的 `resolve_artifact_path` 把 `/artifacts/...` 解析回物理路径，并拒绝 `..` 越权（`Invalid /artifacts path`）；`to_virtual_artifact_path` 反向生成。
- `parse_documents` 默认把 task 级结果 JSON 写到 `data/artifacts/downloads/<stem>.json`（只开 `return_content_list=true`）；用户要 Markdown、图片、原始文件或完整下载包时传五个输出参数全 true，写 ZIP 到 `<stem>.zip`。单文件复用源 stem；多文件为 `<first-stem>_etc_<batch-ts>.json/.zip`（`make_unique_name`）；`extract_archives` 把 ZIP 解压到 `data/artifacts/downloads/<zip-stem>/`。
- 业务 extraction / canonical / Excel 同样写到 `downloads/`，每次生成唯一新文件（`unique_download_path` / `write_json_artifact`，不覆盖旧文件）；输入路径必须显式给出，上传原件和模板不被编辑。
- `/skills/` 映射源码 `dsagents/skills/`（两个内置 Skill 包）；主 agent 与 extractor 的文件权限阻止写入 `/skills/**`。
- `artifact` block 是项目 API 语义；进入 Brain 前会被转成文本路径提示，再由 agent 通过 `read_file` / `parse_documents` 处理。常见办公文件和任意图片都可以上传保存，但能否被解析或理解取决于 DeepAgents、MinerU 与模型能力。
- API 请求只接受显式 `/artifacts/...` 路径；`parse_documents` 为便于测试和程序内调用另外保留本地路径入口，业务 Skill 的 generator 仍只消费显式 artifact JSON/Excel 路径。

### 4.5 业务工具 / Skill 边界

- 每个 Skill 暴露 2 个业务 Tool（extraction 保存 + 一站式生成），全部由 `dsagents/runtime/tools.py` 的 `default_tool_catalog()` 静态注册（普通 import，不自动扫描）。
- 业务错误统一形状：`generate_*_import` 遇业务问题返回 `{"code":"input_problems","problems":[{"source","location","issue","action"}]}`，run 结束；成功返回 `{"status":"generated","canonical_artifact","artifacts","manual_checks"}`。
- 已删除的旧业务工具/状态机：`build_*_canonical` / `save_*_adjudication` / `generate_*_documents` / `needs_input` / `needs_c` / `needs_adjudication` / `info_source_preference` / `pn_info_source_overrides`（Tecan 信息来源冲突一律作 `input_problems`）。

### 4.6 鉴权 / 跨域边界（已确认缺失）

- `dsagents/api.py` 未注册任何 auth middleware；四个端点全部匿名可调。
- 代码无 `CORSMiddleware` 注册 → 浏览器跨域实际不会被处理。

## 5. 依赖和归属规则

- **后端代码改动**归属 `backend/`：先更新 [`backend/.planning/codebase/`](../backend/.planning/codebase/) 对应事实文档，再视影响回看 [`ARCHITECTURE.md`](../ARCHITECTURE.md) / [`INTERFACES.md`](../INTERFACES.md) / 本文件（[`AGENTS.md`](../AGENTS.md) §关键约定明确此规则）。
- **文档分层归属**：
  - 根级 `AGENTS.md` / `ARCHITECTURE.md` / `INTERFACES.md` — 系统边界与导航。
  - `coding_maps/SYSTEM_MAP.md`（本文件）— 系统层跨子项目视图。
  - `docs/*.md` — 详细说明（项目总览、约定、命令、阅读顺序、backend 摘要）。
  - `backend/.planning/codebase/*` — backend 实现细节的事实来源。
- **包管理**：`uv`（非 pip）；安装 `cd backend && uv sync`；禁止 `pip install -e .` 绕过 `uv.lock`。
- **包布局**：`backend/` 安装根下唯一产品包是 `dsagents/`（`pyproject.toml` 用 `package-dir = {"" = "."}` + `packages.find include=["dsagents*"]`）；模块内一律绝对包内导入（如 `from dsagents.runtime import create_harness`、`from dsagents.skills.<skill>.scripts.tools import ...`）；新增 Skill 需在 `[tool.setuptools.package-data]` 追加该 Skill 的 `SKILL.md`/`references`/`assets`。无 `python -m backend.*`。

## 6. 按任务分类的阅读指南

| 任务类型 | 先读 |
|----------|------|
| backend 整体 / runtime / 存储修改 | [`docs/conventions.md`](../docs/conventions.md)（改动前必读）→ [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) + [`backend/.planning/codebase/STRUCTURE.md`](../backend/.planning/codebase/STRUCTURE.md) → 目标模块（`dsagents/runtime/execution.py` / `agent.py` / `runs.py` / `resources.py`） |
| 改 HTTP 契约 / 入口 | [`INTERFACES.md`](../INTERFACES.md) §1/§2 → [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §1 → `dsagents/api.py` |
| 改 run 状态 / 事件 / 持久化 | [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) §5/§6/§9 → `dsagents/runtime/runs.py` + `dsagents/runtime/execution.py` |
| 改模型流式行为 / Brain / middleware | [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §2 → `dsagents/runtime/agent.py` + `dsagents/runtime/observability.py` |
| 改 Skill / Philips/Tecan 业务流程 | 对应 `dsagents/skills/<skill>/SKILL.md` + `references/` → `dsagents/skills/<skill>/scripts/tools.py` + `scripts/documents.py` → `backend/tests/test_*_import.py` |
| 改 MinerU 集成 | [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §6 → `dsagents/integrations/mineru.py` |
| 改 Oracle 集成 | [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §5/§7 + [`backend/.planning/codebase/CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md) §8 → `dsagents/skills/philipswgqimport/scripts/tools.py` |
| 改测试策略 | [`backend/.planning/codebase/TESTING.md`](../backend/.planning/codebase/TESTING.md) |
| 跨系统接口修改 | [`INTERFACES.md`](../INTERFACES.md)（provider/存储/artifacts 边界）→ 本文件 §3.2/§4 |
| 文档维护 | [`AGENTS.md`](../AGENTS.md) 关键约定与末尾维护规则 → [`backend/.planning/codebase/CONVENTIONS.md`](../backend/.planning/codebase/CONVENTIONS.md) |

完整任务→阅读顺序映射见根级 [`docs/reading-order.md`](../docs/reading-order.md)。

## 7. 集成风险检查清单和验证入口

提炼自 [`backend/.planning/codebase/CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md)（每条证据见该文档）。改动涉及以下面时按提示核对：

- **配置完整性**：`parse_documents` 在存在可提交文件时对 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 必需键 fail-fast；`MINERU_EFFORT` 可留空；本地/部署环境需按示例键名补齐，长期文档不记录私有值。
- **配置文档边界**：长期文档只保留配置键、消费者与归属规则，不抄录本地 `.env` 的真实值、连接串或服务地址。
- **文档同步**：四层文档需手工保持一致（根三件套 → 本文件 → `docs/*.md` → `backend/.planning/codebase/*`）。
- **私有配置**：`backend/.env` 被 `.gitignore` 排除且不应进入长期文档；provider key 经 `os.getenv` 直读，无统一脱敏或 secret manager 封装。
- **错误透传**：真实错误（含 provider 4xx/5xx body、MinerU 内网地址、文件路径）原样落 `runs.error` 与 `run_events.raw`，无脱敏护栏。
- **并发语义**：单飞锁仅进程内 `threading.Lock`；多 worker（`uvicorn --workers N`）部署同 `session_id` 可跨进程并发，锁失效。`dsagents_runs.db` 每次操作短连接。`run_controls: dict[run_id → RunControl]` 是进程内字典，仅用于 cancel；多进程部署时跨进程无法协作 drain。
- **Oracle thick client 部署依赖**：`oracledb` thick mode 需要 `ORACLE_CLIENT_LIB_DIR` 指向 Oracle instant client 目录；该 instant client 已从仓库删除（见 [`backend/.planning/codebase/CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md) §3/§8），生产部署需外部提供。缺失或初始化失败时 `generate_philips_wgq_import` 优雅降级，生成的核注清单将缺法定单位字段并返回人工校验项。Tecan Skill 不消费任何 Oracle 键。
- **运行时数据留存**：`run_events` 只增不删，raw chunk 长期留存（含模型输出与错误细节）；无 TTL/归档/压缩。
- **测试覆盖**：backend 当前有 10 个 `test_*.py` 脚本，其中 3 个显式真实集成脚本；本地 assert 脚本（`cd backend && python -m tests.<name>`，**非 pytest**，无总控 runner/CI/lint gate）覆盖 Skills/Subagents 配置、新事件序列（`tool_execution`/`tool_progress`）、A/B/C 与 Excel 关键单元格、`POST /runs/{id}/cancel`、prompt-cache usage 观测（`model_usage` 事件、`GET /runs` 顶层 `usage`、tier 计价、failed run 保留）。真实模型/MinerU/Oracle 集成脚本（`test_real_image_run.py` / `test_real_multi_pdf_run.py` / `test_minimax_cache_baseline.py`）默认不运行、env 守卫，仍需独立验证。

**验证入口**：

- 仅文档变更：`git diff --check`。
- backend 代码变更：按影响范围运行对应脚本，例如 `cd backend && python -m tests.test_api`、`python -m tests.test_harness`、`python -m tests.test_workflow_setup`、`python -m tests.test_philips_wgq_import`、`python -m tests.test_tecan_import`。
- HTTP 行为变更（含 cancel）：已被 `backend/tests/test_api.py` 用 `TestClient` 覆盖，无需手动起服务。
- **部署切换**：新 schema 无迁移代码；切换部署时停服务、清空整个 `backend/data/`（runs/events/checkpoints/store/uploads/downloads）。

## 8. 使用过的源文档索引

根级（系统边界与导航）：

- [`AGENTS.md`](../AGENTS.md)
- [`ARCHITECTURE.md`](../ARCHITECTURE.md)
- [`INTERFACES.md`](../INTERFACES.md)

子项目事实（backend 实现细节事实来源）：

- [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md)
- [`backend/.planning/codebase/STRUCTURE.md`](../backend/.planning/codebase/STRUCTURE.md)
- [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md)
- [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)
- [`backend/.planning/codebase/CONVENTIONS.md`](../backend/.planning/codebase/CONVENTIONS.md)
- [`backend/.planning/codebase/TESTING.md`](../backend/.planning/codebase/TESTING.md)
- [`backend/.planning/codebase/CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md)

本轮（2026-07-13）在 backend 7 份事实文档（ARCHITECTURE / STRUCTURE / STACK / INTEGRATIONS / CONVENTIONS / TESTING / CONCERNS）同日刷新后做同步改写：旧扁平顶层模块与旧带连字符 `skills/` 目录已删除，产品收口进 `dsagents/` 包；HTTP 端点、事件 schema、业务 Tool 收敛、调用链、provider 边界、按任务阅读指南与集成风险清单均按当前源码逐项重述，不再保留旧架构描述。
