# INTEGRATIONS — backend 外部集成事实

> Analysis Date: 2026-07-22
> 范围：`backend/` 源码中的出站/入站边界。
> **不**记录密钥、`.env` 值或私有连接串；仅环境变量**名**与行为。

## 总览

| 集成 | 协议 | 代码入口 | 失败策略 |
|------|------|----------|----------|
| MiniMax（Anthropic 兼容 LLM） | HTTPS | `runtime.agent.DeepAgentsBrainFactory` | 模型/流异常 → run `failed` |
| MinerU 文档解析 | HTTPS + multipart | `integrations.mineru.parse_documents` | 工具异常 / 超时；可投影 custom 状态 |
| Oracle 主数据（可选） | Oracle Net | `lookup_philips_wgq_master_data` | 配置缺失/查询失败 → `problems` + null 字段 |
| SQLite ×3 | 本地文件 | `runtime.resources` / `runtime.runs` | 进程本地；ledger 为外部权威投影 |
| Artifacts 磁盘 | 本地文件系统 | `integrations.artifacts` + upload API | 路径校验；虚拟 `/artifacts/` |
| OMS JSONL | 本地 append | `runtime.oms_log` | best-effort；**不**阻塞已创建 run |
| Auth | — | **无** | 见下文 |
| Webhooks | — | **无** | 见下文 |

---

## HTTP 表面（入站）

| 方法 | 路径 | 入站 | 出站副作用 |
|------|------|------|------------|
| `POST` | `/upload` | multipart `files` | 写 `artifacts/uploads/`，返回虚拟路径 |
| `POST` | `/runs` | JSON：`messages`、可选 `workflow`（`WAG`\|`DK`）、可选 `session_id` | ledger + daemon 线程 + 可选 OMS 行 + 下游 LLM/MinerU/Oracle |
| `GET` | `/runs/{run_id}` | query `after_event_id?` | 只读：`run`、`workflow`、`result`、`events`、`latest_content_event`、`usage` |
| `POST` | `/runs/{run_id}/cancel` | 无 body | ledger `cancelling`/`cancelled` + `RunControl` drain |

**约束与响应要点**

- 模块级 `app = create_app()`，供 `uvicorn api:app` 使用。
- `workflow` 与客户端 `session_id` **互斥**（workflow 强制服务端新建 session）。
- `POST /runs` 成功：`{"run_id","session_id","status":"queued"}`。
- 同 `session_id` 并发：HTTP **409**（`该会话正在运行` + `active_run_id`）。
- 未知 run：HTTP **404**。
- 终态再 cancel：HTTP **409**；已 cancelling/cancelled：200 回显状态；活跃 cancel：HTTP **202** `{"status":"cancelling"}`。
- **无** SSE / WebSocket；客户端模式为 **上传 → 创建 run → 轮询 GET**。

---

## External APIs

### 1. LLM Provider — MiniMax（Anthropic 兼容）

| 项 | 事实 |
|----|------|
| 初始化 | `langchain.chat_models.init_chat_model(f"anthropic:{os.getenv('MINIMAX_MODEL')}", api_key=..., base_url=..., thinking={"type":"adaptive"})` |
| 文件 | `backend/runtime/agent.py`（`BACKEND_ENV_PATH = backend/.env`） |
| 依赖链 | `langchain-anthropic` → `anthropic` SDK；经 `httpx` 出站 |
| 注入点 | 可向 `DeepAgentsBrainFactory(model=...)` 注入假模型（测试） |
| 运行时用途 | 主 Agent 推理、工具调用、WAG `ToolStrategy` 结构化提交、DK finalizer 终态 |
| 用量标签 | `runtime.observability.MAIN_AGENT_MODEL = "MiniMax-M3"` 写入每条 `model_usage` |
| API 估价 | `api._usage_summary` 对可计价模型（`MiniMax-M3`）按档估算 CNY；未知模型则金额为 null |

**环境变量**

| 名 | 说明 |
|----|------|
| `MINIMAX_MODEL` | 模型 ID |
| `MINIMAX_API_KEY` | 密钥 |
| `MINIMAX_BASE_URL` | Anthropic 兼容 API 根 |

无第二生产 provider 硬编码；DeepAgents 传递依赖可含 `langchain-google-genai`，**本仓库未接线**。

**Harness 相关**

- `register_harness_profile("anthropic", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))`
- `thread_id = session_id`；WAG / DK workflow 禁止客户端复用 `session_id`（服务端新建）

### 2. MinerU — 文档解析服务

| 项 | 事实 |
|----|------|
| 文件 | `backend/integrations/mineru.py` |
| 工具名 | `parse_documents`、`extract_archives`（后者为**本地** ZIP，不调 MinerU） |
| HTTP 客户端 | `requests` |
| 提交 | `POST {MINERU_BASE_URL}/tasks`，multipart `files` + form：`backend`、`effort`、`return_md`、`return_content_list`、`return_images`、`return_original_file`、`response_format_zip` |
| 响应字段 | 期望 JSON：`task_id`、`status_url`、`result_url`（相对路径会相对 `MINERU_BASE_URL` 拼接） |
| 轮询 | `GET status_url`；状态 `pending`/`processing` 继续；`completed` 结束；`failed` 抛错 |
| 轮询间隔 | `MINERU_POLL_INTERVAL_SECONDS = 30.0` |
| 结果 | 默认 JSON → `/artifacts/downloads/*.json` 的 `result_path`；ZIP 模式 → `archive_path` |
| 流式状态 | `langgraph.config.get_stream_writer` 发 custom 事件（submitted/pending/processing/completed/failed）→ 投影为 `tool_progress` |

**环境变量**

| 名 | 必需 | 说明 |
|----|------|------|
| `MINERU_BASE_URL` | 是 | API 根 |
| `MINERU_BACKEND` | 是 | 引擎名（测试示例：`vlm-engine`） |
| `MINERU_TIMEOUT_SECONDS` | 是 | 超时秒数 |
| `MINERU_EFFORT` | 否 | 缺省空串 |

路径解析：`resolve_artifact_path(..., allow_local=True)`，接受 `/artifacts/...` 或本地绝对路径。

**ZIP 消费约定**（`/memories/AGENTS.md` + 工具表）：`parse_documents` 返回 `archive_path` 时先 `extract_archives`，再 `read_file` 文本/Markdown。

### 3. 无其他出站业务 HTTP

- 无 OMS 远程推送、无渠道 ERP HTTP、无下载反向代理。
- `httpx2` 在 `pyproject.toml` 声明，**源码无 import**。

---

## Databases

### A. Run ledger SQLite（权威外部投影）

| 项 | 事实 |
|----|------|
| 类 | `runtime.runs.SqliteRunLedger` |
| 文件 | `ResourceConfig.run_db` → `data/dsagents_runs.db` |
| 驱动 | stdlib `sqlite3` |
| 大事件 | 超过 `max_inline_bytes`（默认 262_144）可落到 `data/internal/run-events/` |
| 状态机 | `queued` → `running` → `succeeded` \| `failed` \| `cancelled`；取消途中 `cancelling` |
| 内容 | `runs` 快照（含 `result_json`、`workflow`、`reply`、`error`）+ append-only `run_events` |
| 时间 | UTC+8 本地字符串 `YYYY-MM-DD HH:MM:SS` |

事件类型固定 **7** 类（实现见 `runtime/execution.py` + `runtime/runs.py`）：

| type | 来源概要 |
|------|----------|
| `status` | 状态投影（含终态 reply/error/result） |
| `tool_execution` | 工具调用/遥测（middleware 或 updates） |
| `tool_progress` | `parse_documents` / `extract_archives` custom 流 |
| `thinking` | 模型 thinking delta |
| `text_delta` | 主 Agent 文本 delta（子 Agent 文本过滤） |
| `assistant_message` | 终态助手消息 |
| `model_usage` | 每次模型调用 token / cache 元数据 |

### B. LangGraph checkpoints SQLite

| 项 | 事实 |
|----|------|
| API | `SqliteSaver.from_conn_string` + `setup()` |
| 文件 | `data/dsagents_checkpoints.db` |
| 用途 | 图状态 / `thread_id=session_id` 续跑 |
| 依赖包 | `langgraph-checkpoint-sqlite`（锁定 3.1.0） |

### C. LangGraph store SQLite

| 项 | 事实 |
|----|------|
| API | `SqliteStore.from_conn_string` + `setup()` |
| 文件 | `data/dsagents_store.db` |
| 用途 | `/memories/` 持久手册与跨 run 记忆 |
| 绑定 | `StoreBackend(store=..., namespace=lambda _rt: ("dsagents",))` |

三库物理分离、连接不共享。进程启动 `fail_incomplete_runs("执行已中断，请重试")` 清理崩溃中 run。

### D. Oracle（可选业务主数据）

| 项 | 事实 |
|----|------|
| 工具 | `lookup_philips_wgq_master_data` |
| 文件 | `backend/skills/philips_wgq_inbound_recognition/scripts/tools.py` |
| 驱动 | `import oracledb`（延迟导入） |
| 连接 | `oracledb.connect(user=..., password=..., dsn=..., tcp_connect_timeout=...)` |
| Thick | `ORACLE_CLIENT_LIB_DIR` 存在时 `init_oracle_client(lib_dir=...)` 一次 |
| 查询 | 按 `product_id`（12NC）查 `od.chda` 等，补齐中文品名/规格/原产国/HS/单位等 **稳定字段** |
| 优先级 | Tracking XLSX 合格行优先；Oracle **只填仍为 null 的 ORACLE_FIELDS** |
| 不覆盖 | 数量、价格、金额、运单号等本票事实 |

**环境变量**：`ORACLE_DSN`、`ORACLE_USERNAME`、`ORACLE_PASSWORD`、`ORACLE_CLIENT_LIB_DIR`、`ORACLE_TIMEOUT_SECONDS`（默认 30）。

**降级**：任一配置缺失 → 不连库，返回 problem「Oracle 配置缺失」；异常 → problem「Oracle 查询失败」；未命中 → 字段级 problem。**不**阻塞 Philips 已确认结果的提交路径。

---

## Artifacts storage

| 项 | 事实 |
|----|------|
| 根 | `ResourceConfig.artifacts_dir` → `backend/data/artifacts/` |
| 上传 | `POST /upload` → `artifacts/uploads/`，文件名 `make_timestamped_name` |
| 虚拟路径 | `/artifacts/uploads/...`、`/artifacts/downloads/...` |
| 解析 | `integrations.artifacts.resolve_artifact_path`：禁止 `..`；默认只接受 `/artifacts/...`（MinerU 解析允许 local） |
| 写 JSON | `write_json_artifact` → downloads 下唯一路径 |
| Agent 视图 | `FilesystemBackend(root_dir=artifacts_dir, virtual_mode=True)` 挂到 `/artifacts/` 与 `/large_tool_results/` |
| 权限 | `FilesystemPermission` deny write `/skills/**` |

上传元数据返回：`file_path`、`name`、`mime_type`、`size`。运行时生成物不覆盖上传源。

**业务工具与 artifacts**

| 工具 | 读 | 写 |
|------|----|----|
| `parse_documents` | 上传/本地文档 | downloads JSON 或 ZIP |
| `extract_archives` | ZIP artifact | `downloads/<zip-stem>/` 展开 |
| `inspect_supply_chain_workbooks` | `.xlsx` | `tecan_workbook_*.json` |
| `lookup_philips_wgq_master_data` | Tracking `.xlsx` | 不写 artifact（返回 dict） |
| `finalize_tecan_overseas_recognition` | 无 | 返回 JSON 字符串（由 harness 投影 `run.result`） |

---

## OMS log（best-effort）

| 项 | 事实 |
|----|------|
| 模块 | `backend/runtime/oms_log.py` |
| 默认路径 | `backend/log/oms_log.log` |
| 触发 | `api.post_run` 在 `create_run` **成功之后**、`append_run_created_log(...)` |
| 语义 | best-effort；`except Exception: pass`，**永不**因 OMS 失败而回滚 run |
| 格式 | 一行一个 JSON：`event=run_created`、`created_at`、`run_id`、`session_id`、`workflow`、`files[{name,path}]` |
| `files` | 仅从请求 messages 的 `type=artifact` 块抽取 |
| 非目标 | **不是** `run_events`；**无**查询 API；**不**写 prompt/thinking/tool raw/`run.result` |
| 时区 | 与 ledger 相同 UTC+8 `YYYY-MM-DD HH:MM:SS` |

用途：运维按时间/文件名 stem grep 索引。

---

## Auth

**当前无 HTTP 认证/授权集成。**

- FastAPI 应用未挂载 OAuth2、API Key 校验、Basic Auth、JWT 中间件或 CORS 业务策略。
- 端点默认对网络可达方开放；部署层（反向代理、内网、网关）由运维负责，**不在** `api.py` 实现。
- LLM / MinerU / Oracle 凭证仅出现在**服务端环境变量**与 `.env`，不经客户端 API 传递。
- 测试可注入假 `BrainFactory`，不涉及真实鉴权。

若后续加 Auth，应落在网关或显式 FastAPI 依赖，并同步根级 `INTERFACES.md`；当前规划以无 Auth 为事实。

---

## Webhooks

**当前无 Webhook 出站或入站。**

- 无 run 完成回调 URL、无签名推送、无重试队列。
- 客户端集成模式：**上传 → 创建 run → 轮询** `GET /runs/{run_id}`（可选 `after_event_id`）。
- 取消为客户端主动 `POST .../cancel`，非外部 webhook。

---

## Observability

| 层 | 事实 |
|----|------|
| 权威投影 | `SqliteRunLedger`：`runs` 快照 + append-only `run_events`（7 类事件） |
| 提取器 | `runtime/observability.py`：纯函数，从 stream chunk 抽取 usage / thinking / text / tool_calls（无 I/O） |
| 工具遥测 | `ToolTelemetry` middleware 经 `get_stream_writer` 发 started/completed/error + `duration_ms` |
| 进度 | MinerU / extract_archives custom payload → `tool_progress` |
| 查询 | `GET /runs/{run_id}` 返回 events、`latest_content_event`（排除 status/model_usage）、`usage` 汇总 |
| 用量汇总 | `aggregate_model_usage` + API 层 cache hit rate / 分档 CNY 估价（仅 `MiniMax-M3` 可计价） |
| 主 Agent 名 | `MAIN_AGENT_NAME = "dsagents-main"`；子 Agent 文本默认不投影，但 usage 仍记录 |
| OMS | 旁路 JSONL 索引，**独立于** run_events |
| LangSmith | 仅作为依赖链存在；本仓库**未**显式配置/接线 LangSmith tracing |
| 日志 | 无结构化应用日志框架要求；失败进入 run `error` / event payload |

**降级与边界**

- 工具失败：多数进入 tool 事件 / 业务 `problems`；MinerU 硬失败可致工具异常。
- 模型失败 / `NoProgressLoop`：run `failed`。
- Oracle/MinerU 配置不全：Oracle 软降级；MinerU 缺 env 在调用时 `RuntimeError: Missing required environment variable: ...`。
- Cancel：协作 drain，**不能**强杀外部 HTTP 或 Oracle 调用。
- 多 worker：session 锁与 `run_controls` 仅进程内，跨进程 cancel/互斥**未**集成。

---

## 渠道业务集成（非远程 API，但属集成边界）

### Philips WGQ

| 项 | 事实 |
|----|------|
| HTTP 触发 | `POST /runs` body `workflow: "WAG"` |
| Skill 资源 | `/skills/philips_wgq_inbound_recognition/SKILL.md` |
| 货代版式 | `references/freight-forwarders.md`（DHL、DSV、FedEx、UPS、康捷空） |
| 终态 schema | `PhilipsWgqRecognitionResult`（`skills/philips_wgq_inbound_recognition`） |
| 投影 | ToolStrategy `structured_response` → `run.result` |
| 主数据 | Tracking XLSX + 可选 Oracle |
| 工具 denylist | 去掉 `finalize_tecan_overseas_recognition` |
| 恢复 | `StructuredOutputRecovery` / `StructuredOutputCompatibility`（WAG 专用；`can_jump_to` 须含 `"end"`） |

### Tecan 境外

| 项 | 事实 |
|----|------|
| HTTP 触发 | `POST /runs` body `workflow: "DK"`；通用 run 的明确 Skill 请求仍可使用 finalizer |
| Skill 资源 | `/skills/tecan_import/SKILL.md` + `references/` |
| 终态 | `finalize_tecan_overseas_recognition` → `TecanOverseasRecognitionResult` → harness 捕获 ToolMessage → `run.result` |
| 工具 denylist | 去掉 `lookup_philips_wgq_master_data`，保留共享工具与 finalizer |
| XLSX | `inspect_supply_chain_workbooks` 只读转 JSON；**不**写 Excel 模板 |

两渠道 `items[]` 共用完整 24 字段合同；未知 `null`；无 `shipment`/Excel 噪声进最终 JSON。`input_problems` 仍为 run `succeeded`（业务 outcome，非传输失败）。

---

## 相关源码路径速查

```
backend/api.py
backend/runtime/agent.py
backend/runtime/execution.py
backend/runtime/resources.py
backend/runtime/runs.py
backend/runtime/tools.py
backend/runtime/oms_log.py
backend/runtime/observability.py
backend/runtime/middleware.py
backend/integrations/mineru.py
backend/integrations/artifacts.py
backend/skills/channel_contract.py
backend/skills/philips_wgq_inbound_recognition/scripts/tools.py
backend/skills/tecan_import/scripts/tools.py
backend/pyproject.toml
backend/uv.lock
```
