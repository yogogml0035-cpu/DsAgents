# ARCHITECTURE

> 系统级总览。底层实现事实以 [`backend/.planning/codebase/`](backend/.planning/codebase/) 为准；本文件只沉淀系统边界、子系统职责、理解路径与维护约定。
> 跨子项目系统视图见 [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md)。
> 本轮刷新（2026-07-20）对齐 backend 全部 7 份事实文档（Analysis Date: 2026-07-20，`last_mapped_commit` 555bca7）与 `SYSTEM_MAP`：固定 `philips_wgq_inbound_recognition` workflow、经 Pydantic 校验的 `run.result` 通道、独立 `runtime/middleware.py`（含 `StructuredOutputRecovery` 有界重试、空 data 壳纠错与耗尽 all-null skeleton）、workflow **denylist** 收窄（保留共享 MinerU）、主 Agent middleware 共约 5 个 / Tecan SubAgent 各 4 个、5 静态工具、2 个 Tecan SubAgent；**OMS 旁路索引** `runtime/oms_log.py`（HTTP `create_run` 成功后 best-effort 写 `backend/log/oms_log.log` JSONL，`event=run_created`，非 `run_events`）；时间戳统一为中国时区 UTC+8 本地 `YYYY-MM-DD HH:MM:SS`（ledger 与 OMS 一致）；Skill 采用 **kebab-case 资源目录 + 可 import Python 包** 成对布局与 `package-data` 打包。run-first、四 HTTP 端点、7 类事件、通用/Tecan 行为和无 SSE/session 持久化层边界保持。

## 1. 系统定位

`DsAgents` 是一个 **agent 运行时底座**：把能力做成可插拔，而不绑定具体 runner、容器、模型或工作流。整个产品收口在 `backend/` 顶层源码布局（`api.py`、`runtime/`、`integrations/`、`skills/`；绝对导入 `from runtime import ...`），发行名仍为 `dsagents`。

- **能力可插拔**：`Brain` / `BrainFactory` 是 `typing.Protocol`（`runtime/agent.py`）；middleware 实现集中在 `runtime/middleware.py`，由 `runtime_middlewares()` 与 agent 工厂装配；工具保持普通 callable + `ToolCatalog`；资源 / ledger 保持具体类。默认装配从 `create_harness` 进入（`DeepAgentsBrainFactory` + `default_tool_catalog()`）；本地测试用 `FakeBrainFactory` 替换。
- **工具静态注册**：`default_tool_catalog()` 静态注册 5 个工具（2 个 MinerU 通用、Philips 1 个主数据工具、Tecan 2 个业务工具）；普通 Python import，不自动扫描、无插件平台。
- **业务能力按 Skill 打包（成对目录）**：每个内置业务同时有 **kebab-case Agent Skill 资源目录**（挂载 `/skills/`，含 `SKILL.md` 等）与 **可 import 的 Python 包目录**（合法包名）。Philips：`skills/philips-wgq-inbound-recognition/` + `skills/philipswgqinboundrecognition/`（固定响应合同与 `lookup_philips_wgq_master_data`）；Tecan：`skills/tecan-import/` + `skills/tecanimport/`（抽取保存与 Excel）。`workflow_subagents()` 当前只注册 2 个 Tecan extractor（`tecan-extractor-a` / `tecan-extractor-b`），Philips workflow 不使用 SubAgent。
- **run-first**：run 是唯一执行与查询单位；`run_events` append-only，`runs` 为事件投影快照。`session_id` 仅作 LangGraph `thread_id` 与进程内单飞锁键，不是一等持久化对象。
- **入口形态**：HTTP（`POST /runs` 立即返回 `queued`，后台 daemon 线程执行；纯轮询增量事件，无 SSE）+ 程序内组合（`AgentResources` + `create_harness(...).execute_run(...)`）；无 one-shot 单函数 API。
- **业务工作流形态**：`POST /runs` 可显式选择唯一固定 workflow `philips_wgq_inbound_recognition`；Philips 用 `ToolStrategy(PhilipsWgqRecognitionResult)` 将验证后的业务 JSON 投影到 `run.result` / `runs.result_json`，Tecan 仍按 Skill 驱动 A/B 抽取与 Excel。两者均不新增业务 HTTP、状态表或跨 run 恢复接口。
- **单子项目**：仓库当前只有 `backend/` 一个产品子项目；当前源文档未确认任何前端子项目归属本仓库。

## 2. 子系统职责

| 子项目 | 目录 | 当前职责 | 边界（不做什么） |
|--------|------|----------|------------------|
| backend | `backend/` | 发行名 `dsagents`；源码顶层 `api.py`、`runtime/`、`integrations/`、`skills/`：run-first agent runtime；Brain/BrainFactory、`ToolCatalog`（5 工具）、Philips/Tecan 两个内置 Skill、2 个 Tecan SubAgent、运行时 middleware（含有界 structured recovery）、OMS 旁路索引 | 不提供 session/业务状态表、SSE、鉴权/CORS、跨进程锁/队列、通用工作流引擎、沙箱 / 脚本执行、插件平台、健康检查端点、OMS 查询 API |

backend 内部分层、目录与配置事实见 [`backend/.planning/codebase/ARCHITECTURE.md`](backend/.planning/codebase/ARCHITECTURE.md) 与 [`STRUCTURE.md`](backend/.planning/codebase/STRUCTURE.md)。分层调用视图与跨边界数据流见 [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §3。

## 3. 推荐理解路径

按任务类型的阅读顺序见 [`docs/reading-order.md`](docs/reading-order.md)（权威）与 [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §6（系统层速查）。

系统级导航要点：

1. 边界与定位：本文件 §1–§2、§4
2. 对外契约：[`INTERFACES.md`](INTERFACES.md)
3. 调用链与风险清单：[`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md)
4. 实现事实：`backend/.planning/codebase/` 对应 fact docs（Analysis Date: 2026-07-20，`last_mapped_commit` 555bca7）
5. 改 backend 前必读：[`docs/conventions.md`](docs/conventions.md)

## 4. 稳定目录职责（`backend/` 顶层源码）

`backend/` 安装根下源码顶层为 `api.py` 与 `runtime/`、`integrations/`、`skills/`，模块内使用绝对导入。系统级职责概览（实现细节见 [`STRUCTURE.md`](backend/.planning/codebase/STRUCTURE.md)）：

| 模块 | 系统级职责 |
|------|-----------|
| `api.py` | FastAPI HTTP 适配层（四端点）+ workflow/session 校验 + 同 session 单飞锁 + 启动恢复 + 顶层 `workflow`/`result`/`usage`；`create_run` 成功后触发 OMS 旁路索引 |
| `runtime/agent.py` | `Brain` / `BrainFactory` Protocol、`DeepAgentsBrainFactory`、Philips ToolStrategy / denylist 工具裁剪、Tecan SubAgent 声明与 middleware 装配 |
| `runtime/middleware.py` | `StructuredOutputRecovery`（含空 data 壳）、`ToolTelemetry`、`NoProgressMiddleware`、`StructuredOutputCompatibility` 与 `runtime_middlewares()` |
| `runtime/execution.py` | `HarnessRuntime.execute_run`（stream → `RunEvent`）、结构化响应捕获/复验、`create_harness`、协作 cancel |
| `runtime/observability.py` | 纯函数：chunk → `model_usage` / thinking / text / assistant payload（按 `lc_agent_name` 区分主 agent 与 subagent） |
| `runtime/oms_log.py` | HTTP `create_run` 成功后 best-effort JSONL 旁路索引（`run_created` → `backend/log/oms_log.log`）；**不是** `run_events` |
| `runtime/resources.py` | `AgentResources` + `ResourceConfig` + `CompositeBackend`（`/memories/` `/artifacts/` `/large_tool_results/` `/skills/`） |
| `runtime/runs.py` | `SqliteRunLedger` + `RunEvent` / `RunSnapshot`；`workflow` / `result_json` 投影；fresh schema；大 payload spill；中国时区时间戳 |
| `runtime/tools.py` | `ToolCatalog` + `default_tool_catalog()` 静态 5 工具 |
| `integrations/artifacts.py` | `/artifacts/` 安全路径、唯一下载名、不可覆盖 JSON、上传命名 |
| `integrations/mineru.py` | `parse_documents` / `extract_archives` + `tool_progress` |
| `skills/philips-wgq-inbound-recognition/` + `skills/philipswgqinboundrecognition/` | Philips：kebab 资源（`SKILL.md`）+ 可 import 包（Pydantic schema + Tracking/Oracle 主数据 Tool） |
| `skills/tecan-import/` + `skills/tecanimport/` | Tecan：kebab 资源（`SKILL.md` / references / assets）+ 可 import 包（2 业务 Tool + Excel） |

固定数据目录 `backend/data/`（`ResourceConfig` 锚定，与 CWD 无关）：

| 路径 | 通道 |
|------|------|
| `dsagents_runs.db` | run ledger |
| `dsagents_checkpoints.db` | LangGraph checkpointer（`thread_id=session_id`） |
| `dsagents_store.db` | LangGraph store（`namespace=("dsagents",)`） |
| `artifacts/uploads/` | `POST /upload` 源文件 |
| `artifacts/downloads/` | MinerU / 解压 / Tecan 业务 JSON·Excel（唯一命名，不覆盖） |
| `internal/run-events/` | 大 payload spill（`max_inline_bytes=262_144`，按需创建） |

旁路路径（锚定 `backend/`，**不在** `data/` 下）：

| 路径 | 通道 |
|------|------|
| `log/oms_log.log` | OMS `run_created` JSONL 旁路索引（`runtime/oms_log.py`；HTTP create 后 best-effort；非 ledger / 非 `run_events`） |

时间戳约定：ledger（`runs` / `run_events`）与 OMS `created_at` 统一为中国时区 UTC+8 本地 `YYYY-MM-DD HH:MM:SS`。上传/下载**文件名**中的时间戳可能使用解释器本地时区（与库内 UTC+8 可能不一致，见风险清单）。

三库互不共享连接；无跨库事务。

## 5. run-first 执行模型与事件流

run 是唯一执行与查询单位。短期上下文交给 LangGraph checkpointer + `thread_id=session_id`，无 session 持久化层。

### 两个等价入口

1. **HTTP**（`api.py`）：`POST /runs` 创建并立即返回 `queued`；`GET /runs/{run_id}?after_event_id=N` 轮询增量事件；`POST /runs/{run_id}/cancel` 协作 drain。
2. **程序内**：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(messages, session_id, run_id, workflow=None)` → `Iterator[RunEvent]`。

### 主路径（概览）

```text
POST /upload → /artifacts/uploads/...（不写 OMS）
POST /runs   → 校验 workflow/session → create_run(queued, workflow)
  → best-effort OMS append_run_created_log → backend/log/oms_log.log（JSONL run_created；失败不挡 run）
  → daemon → execute_run
  → status=running；artifact block → 文本路径提示
  → brain_factory.create(..., workflow)；主 Agent 装 runtime_middlewares(memory_backend=...)（共 5 个）
  → Philips：ToolStrategy + **denylist** 排除帝肯工具（保留 parse_documents/extract_archives/lookup_philips_wgq_master_data）；无 SubAgent
  → 通用/Tecan：default tools + tecan-extractor-a/b（各装无 memory 的 middleware，共 4 个）
  → brain.stream(messages/custom/updates, v2, subgraphs, RunControl)
  → messages → model_usage（含 subagent）/ thinking / text_delta（仅主 agent 文本）
  → custom   → tool_execution（ToolTelemetry）+ tool_progress（MinerU）
  → updates  → assistant_message / tool_execution / 可选 structured_response
  → Philips 再次 Pydantic 校验 → status(succeeded, result)；缺失/非法 → failed
     （空壳 recovery 耗尽可写 all-null skeleton + partial_success → succeeded）
  → 通用/Tecan succeeded(reply, result=null) | GraphDrained→cancelled | 异常/NoProgressLoop→failed
GET /runs/{id} → run + 顶层 workflow/result + events[] + latest_content_event + usage
POST .../cancel → cancelling → drain → cancelled
程序内 create_run + execute_run：不经 OMS 旁路
```

完整逐步调用链见 [`SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §3 与 backend ARCHITECTURE §Data Flow。

### 事件源

- 固定 7 类：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`。
- `status` 投影 `runs.status` / `reply` / `error` / `result_json` / `updated_at`；`workflow` 在创建 run 时写入。
- Philips 业务 JSON 走 `result` / `result_json`（终态 status payload 与 GET 顶层同形）；`outcome=input_problems` 时 run 仍为 `succeeded`。
- `model_usage` 为成本/缓存观测，**不计入** `latest_content_event`。
- `after_event_id` 只裁剪 `events[]`，不影响 `latest_content_event` 与 `usage`。
- 旧事件 `tool_call` / `tool_status` / `tool_result` 已删除。

**Philips `result.outcome` 与 run 终态（系统层）：**

| `outcome` | `data` | `problems` | run 终态 |
|-----------|--------|------------|----------|
| `success` | 完整 `RecognitionData` | 可为非空 | `succeeded` |
| `partial_success` | 完整 `RecognitionData` | **至少一个** | `succeeded` |
| `input_problems` | **必须 `null`** | **至少一个** | `succeeded`（业务问题 ≠ 执行失败） |
| （无/非法 structured_response） | — | — | `failed` |

说明：空 `data: {}` 壳在 recovery 重试耗尽时由 middleware 写入 schema 合法的 all-null `data` + `partial_success` + runtime problem（**不是** `data:null` / `input_problems`），harness 可投影 `succeeded`；其它失败模式耗尽后无 `structured_response` → `failed`。实现细节见 backend ARCHITECTURE。

### 状态机与 cancel（概览）

```text
queued → running → succeeded | failed
queued → cancelled
running → cancelling → cancelled
```

活跃 cancel：投影 `cancelling` → `request_cancel` → `GraphDrained` → `cancelled`；尚未注册 `RunControl` 时直接 `cancelled`。终态再 cancel → `409`。取消不回滚已写 artifacts，不跨进程强杀。启动 lifespan 将遗留 `queued`/`running`/`cancelling` 标为 `failed("执行已中断，请重试")`。契约细节见 [`INTERFACES.md`](INTERFACES.md) §2。

### 系统级边界摘要

| 面 | 约定 |
|----|------|
| Middleware | 实现只放 `runtime/middleware.py`；`runtime_middlewares()` 顺序为 `StructuredOutputRecovery` → `ToolTelemetry` → `NoProgressMiddleware` → `StructuredOutputCompatibility`（主 Agent 再挂受限 `MemoryMiddleware`，**共 5 个**）。Tecan SubAgent **不继承**主 Agent middleware，须各自注入无 memory 实例（**各 4 个**） |
| Structured recovery | `after_model` 有界 `jump_to: "model"`（含空文本、空 `data: {}` 壳；默认 `max_retries=2`）；空壳 ToolMessage 以 `tool_call_id` 精确读取同 AIMessage 的合法文本 JSON 并直接 `jump_to: "end"`；`can_jump_to` 必须含 `"end"`；其它耗尽或无法产出 `structured_response` 时显式 `jump_to: "end"`，禁止只返回 `None`；空壳耗尽写 all-null skeleton（见上表说明），**不**编造业务字段 |
| 工具收窄 | workflow 用 **denylist** 排除他业务工具，保留共享 MinerU（`parse_documents` / `extract_archives`）；禁止业务-only allowlist |
| Skill | Philips：结构化 `success\|partial_success\|input_problems` + 单一主数据 Tool → `run.result`；Tecan：2 Tool + `status=generated\|input_problems`；无跨 run 状态机 |
| 工具注册 | 5 工具静态清单；新增 Skill = kebab 资源目录 + 可 import 包 + `default_tool_catalog()` 静态注册 + `package-data`；无动态 loader |
| Provider | 生产 LLM：MiniMax via Anthropic 兼容；文档解析：MinerU HTTP；可选 Oracle（仅 Philips，thick mode 可降级） |
| OMS 旁路 | 实现只放 `runtime/oms_log.py`；触发在 `api.py`；`create_run` 成功后 best-effort JSONL；**不**进入 `run_events`、**无**查询 API；写失败不阻塞已创建 run；程序内路径不经 OMS |
| 时间戳 | ledger 与 OMS 统一 UTC+8 本地 `YYYY-MM-DD HH:MM:SS`；文件名时间戳可能为本机本地时区 |

## 6. 维护约定

- **改动归属**：改 backend 代码后，**先更新** [`backend/.planning/codebase/`](backend/.planning/codebase/) 对应事实文档，**再视影响回看**本文件 / [`INTERFACES.md`](INTERFACES.md) / [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md)（见 [`AGENTS.md`](AGENTS.md)）。
- **文档分层**：根级三件套（边界与导航）→ `SYSTEM_MAP`（系统层视图）→ `docs/*.md`（约定/命令/阅读顺序）→ `backend/.planning/codebase/*`（实现事实）。四层手工保持一致。
- **系统级文档不堆实现**：表结构、完整配置键、主调用链细节归 backend 事实文档；接口形状归 `INTERFACES.md` + `INTEGRATIONS.md`。
- **包管理**：`uv`（非 pip）；`cd backend && uv sync`；禁止 `pip install -e .` 绕过 `uv.lock`。
- **包布局**：安装根 `backend/`；`py-modules=["api"]`，packages `runtime*` / `integrations*` / `skills*`；绝对顶层导入；无 `python -m backend.*`。`[tool.setuptools.package-data]` 当前打包 `philips-wgq-inbound-recognition/SKILL.md` 与 `tecan-import/` 下的 `SKILL.md`、`references/*.md`、`assets/*`；新增 Skill 须同步 package-data。
- **文档语言**：简体中文；标识符/路径/命令/配置键/API 名保持原文；不写密钥与私有连接串。

## 7. 当前风险（系统级）

提炼自 [`backend/.planning/codebase/CONCERNS.md`](backend/.planning/codebase/CONCERNS.md) 与 [`SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §7；改动触及下列面时回读证据原文：

- **配置完整性**：MinerU 必需键 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS`（缺则 fail-fast）；`MINERU_EFFORT` 可空；MiniMax 三键在首次 `create`/调用前可用（工厂无启动期强校验）。
- **配置文档边界**：长期文档只记键名与消费者，不抄录 `.env` 真实值。
- **并发与 cancel**：单飞锁 / `run_controls` 仅进程内；多 worker 同 `session_id` 可交错写 checkpointer；cancel 为协作 drain，非强杀，不回滚 artifacts。
- **安全面**：HTTP 匿名、无 CORS、无用户隔离；`/upload` 无大小/类型/数量限制；错误与 raw 未脱敏可经 `GET /runs` 回传；`parse_documents` 的 `allow_local` 仅宜测试/程序内使用。
- **存储与留存**：`runs.db` 无 WAL / 无 busy_timeout（高频 emit+轮询可能锁冲突）；`run_events` 与 spill 只增不删；fresh schema 无迁移，破坏性变更需整清 `backend/data/`；OMS `backend/log/oms_log.log` 只追加、无轮转/TTL，备份/清理与 `data/` 分开。
- **OMS best-effort / 时区一致性**：OMS 写失败由 API 吞异常，run 仍 `queued` 并执行（运维不可依赖索引完备）；改 `created_at` 格式须同步 `SqliteRunLedger` 与 `oms_log`；文件名 `strftime` 用解释器本地时区，主机 TZ 非 `Asia/Shanghai` 时可能与库内/OMS UTC+8 差若干小时。
- **middleware / 结构化输出 / 工具裁剪**：改 `StructuredOutputRecovery` 时必须保留 `can_jump_to` 含 `"end"` 与耗尽时 `jump_to: "end"`（含空 data 壳路径与 skeleton 回退，不编造字段）；空壳须用 `ToolMessage.tool_call_id` 对齐**同一** AIMessage 的 schema call，禁止扫历史消息；用 `python -m tests.test_harness` 验证重试封顶。SubAgent 勿误传 `memory_backend`。**注意**：`runtime_middlewares()` 无参时 recovery 默认 schema 仍为 `PhilipsWgqRecognitionResult`，Tecan SubAgent 的 `response_format` 是 `ExtractionReference`——工具路径正常，但文本后备 recovery 语义错位；改 SubAgent 结构化输出须传入正确 schema 或禁用文本 recovery。workflow 收窄用 denylist，用 `python -m tests.test_workflow_setup` 断言 Philips 含 `extract_archives`、不含帝肯工具。
- **测试门禁**：本地 7 个 assert 脚本人工选择（`python -m tests.*`，非 pytest）；无 CI/lint；真实模型/MinerU/Oracle 脚本 opt-in，勿并入默认回归；改 OMS 索引时跑 `python -m tests.test_api`（含 `_check_oms_run_created_log`）。
- **Oracle thick client**：Philips 可用 Oracle 补齐 Tracking 缺失的稳定字段；配置缺失、client/查询失败或未命中写入 `problems`，保留已有结果并形成 `partial_success`；依赖外部 `ORACLE_CLIENT_LIB_DIR`；Tecan 不消费 Oracle。

验证入口与按任务核对清单见 [`SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §7 与 [`TESTING.md`](backend/.planning/codebase/TESTING.md)。
