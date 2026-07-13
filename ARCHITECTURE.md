# ARCHITECTURE

> 系统级总览。底层实现事实以 [`backend/.planning/codebase/`](backend/.planning/codebase/) 为准；本文件只沉淀系统边界、子系统职责、理解路径与维护约定。
> 跨子项目系统视图见 [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md)。
> 本轮刷新（2026-07-13）已对齐 backend 全部事实文档（同日刷新）：旧 `backend/dsagents/` 包壳、旧带连字符 `skills/` 目录与旧顶层辅助模块已删除，源码顶层保留 `api.py` 并改为 `runtime/`、`integrations/`、`skills/` 三个顶层包；Philips/Tecan 业务代码归属各自内置 Skill 包；run-first HTTP/ledger、四个声明式 SubAgent、两个 middleware、新事件 schema（7 类）、run 状态机 + cancel、Oracle/Excel 边界均按当前源码重述。模型由 `BrainFactory` 注入，存储由 `AgentResources` 装配，二者边界保持分离。

## 1. 系统定位

`DsAgents` 是一个 **agent 运行时底座**：把能力做成可插拔，而不绑定具体 runner、容器、模型或工作流。整个产品收口在 `backend/` 顶层源码布局（`api.py`、`runtime/`、`integrations/`、`skills/`；绝对导入 `from runtime import ...`），不再保留旧式散落的顶层辅助模块或独立 `capabilities/`。

- **能力可插拔**：`Brain` / `BrainFactory` 是 `typing.Protocol`（`runtime/agent.py`）；工具保持普通 callable + `ToolCatalog`；资源 / ledger 保持具体类。默认装配从 `create_harness` 进入（`DeepAgentsBrainFactory` + `default_tool_catalog()`），运行时不写死具体模型实现（本地测试用 `FakeBrainFactory` 替换）。
- **工具静态注册**：`ToolCatalog`（`runtime/tools.py`）不是 Protocol；`default_tool_catalog()` 静态注册 6 个工具（2 个 MinerU 通用 + 每个 Skill 2 个业务）。运行时通过普通 Python import 拉入 Skill 工具，不自动扫描、无插件平台、无动态模块加载器。
- **业务能力按 Skill 打包**：两个内置 Skill 包 `skills/philipswgqimport/` 与 `skills/tecanimport/`（目录名同时满足 Agent Skills 命名与 Python 包标识符规则，故无需动态 loader）；每个含 `SKILL.md` + `references/` + `assets/` + `scripts/{tools.py, documents.py}`，仅暴露 2 个业务 Tool（抽取保存 + 一站式生成）。`workflow_subagents()`（`runtime/agent.py`）注册 4 个声明式 extractor SubAgent（A/B 各两个），每个 SubAgent 自装自己的 middleware。
- **run-first**：run 是唯一的执行单位与查询单位，`run_events` 表 append-only，`runs` 表是事件投影出的快照。`session_id` 标识符保留，但用途已收窄为 LangGraph `thread_id`（短期上下文键）和进程内串行保护键，不再是一等持久化对象。
- **入口形态**：HTTP（`POST /runs` 创建 run 并立即返回 `queued`，run 在后台 daemon 线程执行；纯轮询获取增量事件，无 SSE）+ 程序内组合（`AgentResources` + `create_harness(...).execute_run(...)`）；无单函数 one-shot API。
- **业务工作流形态**：业务意图由模型按需选择 Skill；A/B/C、裁决、canonical 与 Excel 都是显式 artifact 路径，不新增 workflow API、数据库表或持久化恢复状态。
- **单子项目**：仓库当前只有 `backend/` 一个产品子项目；当前源文档未确认任何前端子项目归属本仓库。

> 顶层 HTTP 入口 `api.py` 作为顶层模块保留；旧辅助模块（`harness.py`/`hands.py`/`resources.py`/`run_ledger.py`/`tools.py`/`subagents.py`/`workflow_artifacts.py`/`artifact_names.py`/`philips_wgq_import.py`/`tecan_import.py`）与旧带连字符 `skills/` 目录均已删除。

## 2. 子系统职责

| 子项目 | 目录 | 当前职责 | 边界（不做什么） |
|--------|------|----------|------------------|
| backend | `backend/` | 发行名 `dsagents`，源码顶层为 `api.py`、`runtime/`、`integrations/`、`skills/`：run-first agent runtime；维护 Brain/BrainFactory（Protocol）、`ToolCatalog` 与 6 个静态注册工具，挂载两个内置 Skill 包、4 个声明式 SubAgent 与两个 middleware | 不提供 session/业务状态表、SSE、鉴权/CORS、跨进程锁、队列、通用工作流引擎、沙箱 / 脚本执行、插件平台 |

backend 内部架构、目录组织、配置加载、事件源模型等实现事实见 [`backend/.planning/codebase/ARCHITECTURE.md`](backend/.planning/codebase/ARCHITECTURE.md) 与 [`backend/.planning/codebase/STRUCTURE.md`](backend/.planning/codebase/STRUCTURE.md)。

## 3. 推荐理解路径

按任务类型的阅读顺序见 [`docs/reading-order.md`](docs/reading-order.md)（权威）与 [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §6（系统层视图）。

系统级导航要点：理解系统边界与接口从本文件 → [`INTERFACES.md`](INTERFACES.md) → [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md)；理解子系统职责从本文件 §2；理解稳定目录职责从本文件 §4。

## 4. 稳定目录职责（`backend/` 顶层源码内模块）

`backend/` 安装根下源码顶层为 `api.py` 与 `runtime/`、`integrations/`、`skills/`，模块内使用绝对导入。包内模块的系统级职责概览（不展开实现，详见 [`backend/.planning/codebase/STRUCTURE.md`](backend/.planning/codebase/STRUCTURE.md)）：

| 模块 | 系统级职责 |
|------|-----------|
| `api.py` | FastAPI HTTP 适配层（run-first 四端点：`POST /upload` / `POST /runs` / `GET /runs/{run_id}` / `POST /runs/{run_id}/cancel`）+ 同 session 单飞锁 + 启动恢复 + 顶层 `usage`/tier 计价 |
| `runtime/agent.py` | `Brain` / `BrainFactory` Protocol、`DeepAgentsBrainFactory`、`workflow_subagents()`（4 个声明式 extractor SubAgent，各自装自己的 middleware）、两个运行时 middleware（`ToolTelemetry` / `NoProgressMiddleware`）、`MAIN_AGENT_NAME` / `MODEL` |
| `runtime/execution.py` | `HarnessRuntime.execute_run`（stream chunk → `RunEvent`）、`create_harness`；`run_controls: dict[run_id → RunControl]` 协作 drain；`GraphDrained` 投影为 `cancelled` |
| `runtime/observability.py` | 纯内容/元数据提取器：`model_usage` / `thinking_delta` / `message_delta` / `assistant_message_payload` / `tool_call_payload`（按 `lc_agent_name` 区分主 agent 与 subagent） |
| `runtime/resources.py` | `AgentResources`（context manager）+ `ResourceConfig` + `CompositeBackend` 装配（`/memories/` `/artifacts/` `/large_tool_results/` `/skills/` 四路由） |
| `runtime/runs.py` | `SqliteRunLedger` + `RunEvent` + `RunSnapshot`；fresh schema、UTC ISO-8601 毫秒时间戳、无迁移；`aggregate_model_usage`；`RUN_STATUSES={queued,running,succeeded,failed,cancelled,cancelling}` |
| `runtime/tools.py` | `ToolCatalog` dataclass + `default_tool_catalog()` 静态注册 6 个工具（普通 Python import Skill 工具，无自动扫描） |
| `integrations/artifacts.py` | `/artifacts/` 安全路径解析、唯一下载名、不可覆盖 JSON 读写、上传命名清洗 |
| `integrations/mineru.py` | MinerU 通用工具 `parse_documents` / `extract_archives` + `MINERU_POLL_INTERVAL_SECONDS`；`.env` 加载（`MINERU_*`） |
| `skills/philipswgqimport/` | Philips 外高桥进境 Skill：`SKILL.md` + `references/` + `assets/`（`invoice,packing进境.xlsx`、`核注清单导入模板.xlsx`）+ `scripts/tools.py`（`save_philips_wgq_extraction` + `generate_philips_wgq_import`，含可选 Oracle thick mode 法定单位查询）+ `scripts/documents.py`（3 个 Excel 写入器 + 共享 openpyxl helper） |
| `skills/tecanimport/` | Tecan 帝肯进口 Skill：`SKILL.md` + `references/` + `assets/`（`Tecan_进口_发票箱单_空运.xlsx`）+ `scripts/tools.py`（`save_tecan_extraction` + `generate_tecan_import`，join 订单 + 信息工作簿）+ `scripts/documents.py`（发票箱单写入器 + `insert_rows`） |

固定数据目录 `backend/data/`（路径由 `ResourceConfig` 决定，与 CWD 无关）：三条逻辑 SQLite 通道（`dsagents_runs.db` / `dsagents_checkpoints.db` / `dsagents_store.db`，互不共享连接）+ `artifacts/`（`uploads/` 上传源、`downloads/` MinerU/解压产物与唯一命名的业务 JSON/Excel）+ `internal/run-events/`（大 payload 外溢，仅真正 spill 时创建）。

## 5. run-first 执行模型与事件流

run 是唯一的执行单位与查询单位。短期上下文全交给 LangGraph checkpointer + `thread_id=session_id`，不再有 session 持久化层。

### 两个等价入口

1. **HTTP 入口**（`api.py`）：`POST /runs` 创建 run 并立即返回 `queued`，run 在后台 daemon 线程执行；`GET /runs/{run_id}?after_event_id=N` 轮询增量事件（非 SSE，纯轮询）；`POST /runs/{run_id}/cancel` 协作式 drain。
2. **程序内入口**：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(messages, session_id, run_id)`，返回 `Iterator[RunEvent]`。本地测试脚本与 `FakeBrain` 测试也走这条路。

### stream → 事件 pipeline（`runtime/execution.py execute_run`）

```text
HTTP 层 (api.py)
  POST /upload(files[])        -> 保存到 /artifacts/uploads/<cleaned-stem>_<upload-ts>(_n).ext
  POST /runs(messages, session_id?)
     -> create_run(run_id, session_id, input_messages_json)   # runs.py
     -> threading.Thread(target=_run_background, daemon=True)
        -> HarnessRuntime.execute_run(messages, session_id, run_id)
  GET  /runs/{run_id}?after_event_id=N
     -> run, events[], latest_content_event, usage            # 纯轮询，非 SSE
  POST /runs/{run_id}/cancel
     -> 协作 drain：RunControl；GraphDrained -> cancelled

HarnessRuntime.execute_run(...)
  -> emit status=running
  -> artifact block 归一化为文本提示 (ARTIFACT_REFERENCE_HINT)
  -> brain_factory.create(resources, middleware=runtime_middlewares(), tools=tools.as_list())
  -> brain.stream(
       {"messages": normalized_messages},
       config={"configurable":{"thread_id": session_id}},
       stream_mode=["messages","custom","updates"],
       subgraphs=True,
       version="v2",
       control=RunControl(),                                 # 协作 drain 入口
     )
  -> chunk[type=messages]   => model_usage / thinking / text_delta（按 lc_agent_name 丢弃 subagent 文本，subagent 模型 token 仍计入 model_usage）
  -> chunk[type=custom]     => tool_progress / tool_execution（ToolTelemetry 自发）
  -> chunk[type=updates]    => _update_events 派生 assistant_message / tool_execution
  -> 结束 => status=succeeded(reply=assistant_text 或拼接 text_parts)
       GraphDrained          => status=cancelled
       NoProgressLoop / 其它异常 => status=failed(error=...)
```

- `messages` channel 先在 subagent 过滤之前提取 `model_usage`（覆盖主 agent 与 subagent 调用），再仅主 agent 文本规范化为 `thinking` / `text_delta`；subagent 文本 token 由 `lc_agent_name` 过滤丢弃。
- `custom` channel：`ToolTelemetry`（`wrap_tool_call`）自发 `tool_execution`（`started/completed/error` + 计时 + scope 路径）；MinerU 通用工具（`parse_documents`/`extract_archives`）自发 `tool_progress`（提交/轮询/下载进度）。两套独立 custom 事件。
- `updates` channel：`_update_events` 派生 `assistant_message`（由 `observability.assistant_message_payload` 构造，保留最终 `text` 与最后一个 `thinking` 文本）与 `tool_execution`。
- raw 完整 v2 chunk 整体落库（`run_events.raw_*`）。

### 事件源模型

每个 run 的进展以**事件**形式不可变追加到 `run_events` 表，`event_id` 单调递增。`GET /runs/{run_id}?after_event_id=N` 仅靠事件表增量回放，无需额外会话状态；`latest_content_event` 由 `run_id + type not in ('status','model_usage') + event_id desc limit 1` 取得。

事件类型固定 7 种（`runtime/execution.py` 写库）：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`。

> 旧事件 `tool_call` / `tool_status` / `tool_result` 已删除；旧的 values-snapshot 去重 helper 已删除。

`status` 事件同时驱动 `runs` 表的 `status` / `reply` / `error` / `updated_at` 列更新（即 run 状态是事件投影）。`model_usage` 是成本/缓存观测事件，不算内容事件，因此被 `latest_content_event` 排除。

## 6. run 状态机与 cancel

```text
queued → running → succeeded | failed
queued → cancelled
running → cancelling → cancelled
```

取消流（`POST /runs/{run_id}/cancel`，`api.py`）：

- 未知 run → `404 {"error":"Unknown run: ..."}`。
- 终态（`succeeded`/`failed`）→ `409 {"error":"Run already terminal: ...","status":...}`。
- 已 `cancelling`/`cancelled` → `200 {"status":...}`。
- 活跃 run（`queued`/`running`）→ 投影 `cancelling` → `harness.request_cancel(run_id)` 经 LangGraph `RunControl` 协作 drain → `GraphDrained` 投影为 `cancelled`；若 run 尚未进入 `execute_run`（`queued` 或未注册 `RunControl`），直接置 `cancelled`，返回 `202 {"status":"cancelling"}`。

`run_controls: dict[run_id → RunControl]` 是进程内字典，仅用于 cancel；取消不回滚已生成文件，不实现多进程强杀。`fail_incomplete_runs` 在 app lifespan 启动时把遗留 `queued`/`running`/`cancelling` run 标记为 `failed("执行已中断，请重试")`。

## 7. 中间件边界

运行时恰好两个 middleware（`runtime/agent.py` `runtime_middlewares()` 每次返回新实例列表）：

- `ToolTelemetry`（`wrap_tool_call`）：工具调用前/异常/成功后经 `get_stream_writer()` 发 `tool_execution` 三态（`started|error|completed` + `agent_name` + `duration_ms` + scope 路径）。
- `NoProgressMiddleware`（`before_model`）：自最近一条 `HumanMessage` 之后，若同一 `tool + 归一化 args` 连续出现 `NO_PROGRESS_WINDOW`（=3）次则抛 `NoProgressLoop`，由 `execute_run` 投影为 `failed`。

**关键约束**：主 Agent 与每个 SubAgent 都各自装这两个 middleware——声明式 SubAgent **不继承**主 Agent 的 middleware，故 `workflow_subagents()` 通过 `_extractor(...)` 给每个 SubAgent 显式注入 `runtime_middlewares()`。

明确**不使用**：`ToolCallLimitMiddleware`、`wrap_model_call`、`before_agent`/`after_agent`、自定义 state schema、自定义 stream transformer、v3 stream、sandbox / 脚本执行、Skill file-sync middleware。

## 8. Skill 边界

- **每个 Skill 只暴露 2 个业务 Tool**：`save_*_extraction`（抽取保存，返回 `{extractor, artifact_path}`）+ `generate_*_import`（一站式 canonical + 匹配 + 计算 + Excel 写入 + 输出复核）。旧 `build_*_canonical` / `save_*_adjudication` / `generate_*_documents` / `needs_input` / `needs_c` / `needs_adjudication` 状态机均删除；旧 `info_source_preference` / `pn_info_source_overrides` 删除（Tecan 信息来源冲突一律作 `input_problems`）。
- **业务错误形状统一**：`generate_*_import` 遇业务问题返回 `{"code":"input_problems","problems":[{"source","location","issue","action"}]}`，run 结束、用户修正材料后重新显式传路径；成功返回 `{"status":"generated","canonical_artifact","artifacts","manual_checks"}`。
- **业务流程由 SKILL.md 指令驱动**：主 agent 调 `parse_documents` → 并行 A/B SubAgent（各 save extraction）→ 必要时 extractor C → 仍冲突则形成最小 `decisions` → `generate_*_import`。无游标、不暂停/恢复、不跨 run 状态。
- **静态注册，无插件平台**：新增 Skill = 新增一个 Skill 包目录 + 在 `default_tool_catalog()` 追加一行 import + 一行注册；不复制 runtime、不自动扫描、无动态加载器。

## 9. 存储边界

`backend/data/` 固定三条**逻辑持久化通道**（路径由 `ResourceConfig` 决定，与 CWD 无关；文件按需创建）：

| 文件 | 通道 | 写入方 |
|------|------|--------|
| `dsagents_runs.db` | run ledger | `SqliteRunLedger`（fresh schema，无迁移，UTC ISO-8601 毫秒时间戳） |
| `dsagents_checkpoints.db` | LangGraph checkpointer | `SqliteSaver`（`thread_id=session_id`） |
| `dsagents_store.db` | LangGraph store | `SqliteStore`（`namespace=("dsagents",)`） |

三者互不共享连接（每次 `SqliteRunLedger` 方法都新开 `sqlite3.connect`）。用户可见文件只落在 `data/artifacts/uploads/`（`POST /upload`）与 `data/artifacts/downloads/`（MinerU JSON/ZIP、解压目录、唯一命名的业务 JSON/Excel）。内部大 payload spill 独立落在 `data/internal/run-events/`（`max_inline_bytes=262_144`）。`CompositeBackend` 路由：`/memories/` → `StoreBackend`；`/artifacts/`、`/large_tool_results/` → `FilesystemBackend`（同实例，`virtual_mode=True`）；`/skills/` → `FilesystemBackend`（只读 Skill 源）；其它（含 `/conversation_history/`、未用 `/logs/`）→ `StateBackend`。

## 10. 系统层面维护约定

- **改动归属**：改 backend 代码后，**先更新** [`backend/.planning/codebase/`](backend/.planning/codebase/) 对应事实文档，**再视影响回看**根级 `ARCHITECTURE.md` / `INTERFACES.md` 与 `coding_maps/SYSTEM_MAP.md`（详见 [`AGENTS.md`](AGENTS.md) 关键约定）。
- **文档分层维护**：根级三件套（系统边界与导航）→ `coding_maps/SYSTEM_MAP.md`（系统层跨子项目视图）→ `docs/*.md`（详细说明）→ `backend/.planning/codebase/*`（实现事实来源）。四层需手工保持一致。
- **系统级文档不堆实现**：本文件与 `INTERFACES.md` 只描述系统边界与接口契约；具体表结构、主调用链细节、配置键清单归 backend 事实文档。
- **包管理**：`uv`（**非 pip**）；安装 `cd backend && uv sync`；禁止 `pip install -e .` 绕过 `uv.lock`。
- **包布局**：`backend/` 安装根下源码顶层为 `api.py` 与 `runtime/`、`integrations/`、`skills/`；模块内一律绝对顶层导入（如 `from runtime import AgentResources, create_harness`、`from skills.<skill>.scripts.tools import ...`）；无 `python -m backend.*`。

## 11. 当前风险（系统级）

提炼自 [`backend/.planning/codebase/CONCERNS.md`](backend/.planning/codebase/CONCERNS.md)（每条证据见该文档），改动涉及以下面时按提示核对：

- **配置完整性**：`parse_documents` 在存在可提交文件时对 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 必需键 fail-fast；`MINERU_EFFORT` 可留空；本地/部署环境需按示例键名补齐，长期文档不记录私有值。
- **配置文档边界**：长期文档只记录配置键与消费者，不抄录本地 `.env` 中的真实值、连接串或服务地址。
- **run 锁单进程语义**：单飞锁仅进程内 `threading.Lock`；多 worker（`uvicorn --workers N`）部署同 `session_id` 可跨进程并发，锁失效。
- **文档同步**：四层文档手工保持一致。
- **运行时数据留存**：`run_events` 只增不删，raw chunk 长期留存（含模型输出与错误细节）；无 TTL/归档/压缩。
- **错误透传**：真实错误（含 provider 4xx/5xx body、MinerU 内网地址、文件路径）原样落 `runs.error` 与 `run_events.raw`，无脱敏护栏。
- **测试覆盖**：本地 assert 脚本（`cd backend && python -m tests.<name>`，**非 pytest**）覆盖 Skills/Subagents 配置、A/B/C 与 Excel 关键单元格、新事件序列（tool_execution/tool_progress）、`POST /runs/{id}/cancel`、tier 计价；无 pytest/CI/lint gate，真实模型/MinerU/Oracle 仍需独立集成验证（默认关闭、env 守卫）。
- **Oracle thick client 部署依赖**：Philips 法定单位查询（`skills/philipswgqimport/scripts/tools.py`）走 `oracledb` thick mode，需 `ORACLE_CLIENT_LIB_DIR` 指向外部 Oracle instant client 目录（仓库不存放）；缺失或初始化失败时优雅降级——核注清单缺法定单位字段并返回人工校验项，不崩溃。证据见 `backend/.planning/codebase/CONCERNS.md` §8。
