---
last_mapped_commit: 08413f4688e03e5a24fb8ac08270541d280aee5d
---

# External Integrations

**Analysis Date:** 2026-07-16

> 外部集成边界基于 `api.py`、`runtime/`、`integrations/`、`skills/` 与 `backend/.env.example` 核对。文档只记键名与用途，不记录真实密钥或本地 `.env` 值。`POST /upload` 与 `RunMessage` 的 `artifact` block 只暴露 `/artifacts/...` 虚拟路径；`parse_documents` 内部允许测试/程序内 `allow_local` 路径，业务 Tool 只接受显式 `/artifacts/...`。

## APIs & External Services

### 1. 本服务 HTTP API（FastAPI）

入口：`backend/api.py`。`create_app(*, resource_config=None, harness_factory=create_harness)`；模块级 `app = create_app()`。预期由 `uv run uvicorn api:app --host 0.0.0.0 --port 8500` 拉起。

| 方法 / 路径 | 入参 | 行为摘要 | 主要状态码 |
|---|---|---|---|
| `POST /runs` | `RunRequest`：`workflow?`、`session_id?`、`messages[]`（`text` / `artifact` block） | 分配 `run_id`；Philips workflow 强制新 session；写 ledger；daemon 执行 | `200` queued；`409` 通用 session 冲突；`422` 未知 workflow/非法复用 |
| `GET /runs/{run_id}` | query `after_event_id?` | run 快照 + 顶层 `workflow`/`result` + 增量 events + `latest_content_event` + `usage` | `200`；`404` 未知 run |
| `POST /runs/{run_id}/cancel` | path `run_id` | 协作 drain 或直接 cancelled | `202` cancelling；`200` 已取消中；`409` 终态；`404` 未知 |
| `POST /upload` | multipart 字段 `files`（可多文件） | 落到 `artifacts/uploads/`，返回虚拟路径 | `200` `files[]` |

补充：

- **无** SSE / `text/event-stream`；靠轮询 `after_event_id`。
- `after_event_id` 只过滤 `events[]`；`latest_content_event` 与 `usage` 始终全量当前值。
- **无** CORS 中间件。
- 时间字段：UTC ISO-8601 毫秒。
- 取消不回滚已生成文件，不跨进程强杀。
- 当前唯一固定 workflow 是 `philips_wgq_inbound_recognition`；省略 `workflow` 时保持通用/Tecan 的 `reply` 行为。
- Philips `result` 来自 `ToolStrategy(PhilipsWgqRecognitionResult)`；`reply` 不参与 JSON 解析。`input_problems` 仍对应 `run.status=succeeded`。

**取消流**：终态 → `409`；已 `cancelling`/`cancelled` → `200`；活跃 → 投影 `cancelling` → `harness.request_cancel`；未进入 `execute_run` 则直接 `cancelled`，HTTP `202`。

**`usage` 出口**（`api._usage_summary`）：基于 `aggregate_model_usage`；叠加 cache hit rate 与 MiniMax-M3 tier 定价（`PRICING_AS_OF = "2026-07-12"`，input 阈值 512k）。未知模型时金额为 `null`，token 仍完整。含 `by_agent[]`。

**artifact 语义**：`artifact` block 是项目 API 语义，非 LangChain 标准多模态 block。`HarnessRuntime` 转为文本提示 `ARTIFACT_REFERENCE_HINT` 后以纯 text messages 交给 Brain。

**lifespan**：装配 `AgentResources` → `fail_incomplete_runs("执行已中断，请重试")` → harness + 锁注册表；退出关闭 SQLite 上下文。

### 2. LLM Provider（MiniMax via Anthropic 兼容）

| 边界 | 实现 | 证据 |
|---|---|---|
| 生产 | 通用/Tecan 用 `thinking={"type":"adaptive"}`；`runtime_middlewares()` 为每个 agent graph 装配 `StructuredOutputCompatibility`，仅为 `ToolStrategy` 请求复制模型并关闭 thinking，以保留强制 tool choice 和结构化响应 | `runtime/middleware.py`、`runtime/agent.py` |
| 测试 | `FakeBrain` / `FakeBrainFactory`（v2 stream，不触达网络） | `tests/test_support.py` |
| prompt-cache | DeepAgents 尾栈 `AnthropicPromptCachingMiddleware`；固定前缀勿注入 run_id/时间等动态内容 | 库行为 + `DEFAULT_SYSTEM_PROMPT` / tool schema |
| usage 观测 | 从 stream `messages` chunk 的 `usage_metadata` 提取 → `model_usage` 事件；不入库专用表 | `runtime/observability.py`、`runtime/execution.py` |

相关 env：`MINIMAX_MODEL`、`MINIMAX_API_KEY`、`MINIMAX_BASE_URL`（`.env.example` 默认模型名 `MiniMax-M3`，base 示例指向 MiniMax Anthropic 兼容端点）。

### 3. MinerU 文档解析（HTTP，`requests`）

唯一生产出站 HTTP 客户端模块：`integrations/mineru.py`。

| 步骤 | 方法 | 说明 |
|---|---|---|
| 提交 | `POST {MINERU_BASE_URL}/tasks` | multipart 文件 + form：`backend`/`effort`/`return_*`/`response_format_zip` |
| 轮询 | `GET {status_url}` | 间隔 `MINERU_POLL_INTERVAL_SECONDS=30`；总超时 `MINERU_TIMEOUT_SECONDS`；状态 `pending`/`processing`/`completed`/`failed` |
| 结果 JSON | `GET {result_url}` | 默认模式 → `/artifacts/downloads/<stem>.json`，返回 `result_path` |
| 结果 ZIP | `GET {result_url}` stream | 全量输出模式 → `.zip`，返回 `archive_path` |

工具契约：

- `parse_documents(file_paths, return_md=False, return_content_list=True, ...)`：任选 md/images/original/zip 则五参数全 true。
- `extract_archives(zip_paths)`：标准库 `zipfile` 解压到 `/artifacts/downloads/<zip-stem>/`。
- 二者在 LangGraph 内通过 `get_stream_writer()` 发 `tool_progress`；`ToolTelemetry` 另发 `tool_execution`。

### 4. Oracle（可选，仅 Philips Skill）

- 消费者：`skills/philipswgqinboundrecognition/scripts/tools.py` 的 `_oracle_data` / `_init_oracle_client`。
- 驱动：`oracledb.connect(...)`；配置 `ORACLE_CLIENT_LIB_DIR` 时先启用 thick mode。
- 查询：参数化 `:product_id`，只读稳定主数据并映射为英文键 `chinese_name` / `specification` / `origin_country` / `customs_code` / `unit` / `legal_unit_1` / `legal_unit_2`；仅查询 Tracking 尚缺字段的 12NC。
- 行为：配置缺失、client/查询失败或未命中写入 `problems`，不覆盖 Tracking，不丢弃 PDF 结果。
- Tecan Skill **不**调用 Oracle。

### 5. 业务工具（进程内，非外部 HTTP）

| 工具 | 模块 | 要点 |
|---|---|---|
| `lookup_philips_wgq_master_data` | `skills/philipswgqinboundrecognition/scripts/tools.py` | 严格 Tracking 选行 + Oracle 缺失字段补齐；返回英文字段（`product_id` 等）；不返回交易字段 |
| `save_tecan_extraction` | `skills/tecanimport/scripts/tools.py` | 写 extraction JSON |
| `generate_tecan_import` | 同上 | 订单+信息表匹配 + 发票箱单 Excel |

Philips 最终业务合同由结构化响应承担；Tecan Tool 仍以 `status=generated` / `code=input_problems` 返回。两者都无多阶段状态机恢复。

Excel：Philips 仅用 `openpyxl` 只读 Tracking 的 `进口` / `申报要素` sheet；Tecan 使用模板 `Tecan_进口_发票箱单_空运.xlsx` 生成工作簿。旧 Philips Excel 模板与写入模块已删除。

## Data Storage

### SQLite 三库

| 组件 | 路径 | 库/模块 |
|---|---|---|
| Run ledger | `backend/data/dsagents_runs.db` | `SqliteRunLedger`（标准库 `sqlite3`） |
| LangGraph store | `backend/data/dsagents_store.db` | `SqliteStore`（`langgraph.store.sqlite`） |
| LangGraph checkpointer | `backend/data/dsagents_checkpoints.db` | `SqliteSaver`（`langgraph.checkpoint.sqlite`） |

**runs 投影表**：`run_id` PK、`session_id`、`input_messages_json`、可选 `workflow`、`status`、时间戳、`reply`、`error`、可选 `result_json`。

**run_events 表**：自增 `event_id`、`run_id`、`type`、`created_at`、payload/raw（可外溢文件）。索引：`idx_run_events_run_order`、`idx_runs_session_created`。

大 payload（> 262_144 字节）写入 `data/internal/run-events/<uuid>.json`，行内占位，读取透明回填。

### 文件系统 artifacts

| 物理目录 | 虚拟前缀 | 写入者 |
|---|---|---|
| `data/artifacts/uploads/` | `/artifacts/uploads/` | `POST /upload` |
| `data/artifacts/downloads/` | `/artifacts/downloads/` | MinerU、解压、Tecan JSON/Excel |

路径工具：`integrations/artifacts.py` — `resolve_artifact_path`（防 `..`）、`to_virtual_artifact_path`、`clean_filename`、`make_timestamped_name`、`unique_download_path`、`write_json_artifact` / `read_json_artifact`。

### LangGraph 持久化约定

```text
brain.stream(
  {"messages": normalized_messages},
  config={"configurable": {"thread_id": session_id}},
  stream_mode=["messages", "custom", "updates"],
  subgraphs=True,
  version="v2",
  control=RunControl(),
)
```

- 查询维度是 `run_id`；`session_id` 仅作 `thread_id` 与进程内单飞锁。
- payload 只含当前请求 messages，不重放本地 session 历史。
- `BrainFactory.create(..., workflow)` 明确接收 workflow；Philips 从 `updates` 捕获并再次校验 `structured_response`。

**无** Redis、Postgres、对象存储 SDK、云 blob 客户端。

## Authentication & Identity

| 项 | 状态 |
|---|---|
| 用户登录 / OAuth / JWT | **未实现** |
| API key 鉴权中间件 | **未实现**（HTTP 端点开放，依赖部署侧网络隔离） |
| 服务间 mTLS | **未实现** |
| LLM 凭证 | 环境变量 `MINIMAX_API_KEY` 等，进程内传给 provider 客户端 |
| Oracle 凭证 | 环境变量 `ORACLE_USERNAME` / `ORACLE_PASSWORD` 等，可选 |
| CORS / 浏览器跨域 | **未配置** |

结论：backend 当前是**无内置身份层**的 agent 运行时；安全边界在部署网络与密钥管理，不在应用鉴权中间件。

## Monitoring & Observability

| 能力 | 实现 | 说明 |
|---|---|---|
| Run 事件流 | `SqliteRunLedger` + `GET /runs/{run_id}` | `status` / `thinking` / `text_delta` / `tool_execution` / `tool_progress` / `assistant_message` / `model_usage` 等 |
| 模型用量 | `model_usage` 事件 + `usage` 汇总 | token + cache + 可选 CNY 估算 |
| 工具遥测 | `ToolTelemetry.wrap_tool_call` | custom stream → `tool_execution`（started/completed/error + duration） |
| 解析进度 | `parse_documents` / `extract_archives` 自发 custom | → `tool_progress` |
| 无进展熔断 | `NoProgressMiddleware` | 从当前 HumanMessage 之后的消息状态计算连续相同 tool 调用 `NO_PROGRESS_WINDOW` 次 → `NoProgressLoop`；不保存实例级状态 |
| 结构化输出兼容 | `StructuredOutputCompatibility.wrap_model_call` | `ToolStrategy` 且模型启用 thinking 时，`request.override(model=...)` 生成一次性 `thinking=None` 模型；不写 graph state、不改变 `structured_response` 合同 |
| 结构化日志 / OpenTelemetry / Prometheus | **未接入** | 无 APM SDK |
| 错误上报 SaaS | **未接入** | 失败写入 run `error` 字段 |

可观测性以 **run 为中心的事件投影** 为主，而非独立 metrics 后端。

## CI/CD & Deployment

| 项 | 状态 / 约定 |
|---|---|
| backend 内 CI 配置 | **无** 专属 `.github/workflows` 等（本子项目目录内） |
| 本地启动 | `scripts/start-backend.ps1` → 独立 PowerShell 窗口 → `uv run uvicorn api:app --host 0.0.0.0 --port 8500` |
| 依赖安装 | `cd backend && uv sync` |
| 打包 | setuptools wheel；Skill 资源经 `package-data` 打入 |
| 配置 | 部署机放置 `backend/.env`（参考 `.env.example` 键名） |
| 数据卷 | 持久化 `backend/data/`（三 SQLite + artifacts） |
| Oracle 部署前提 | thick mode 需本机 Instant Client 路径 `ORACLE_CLIENT_LIB_DIR` |
| 健康检查端点 | **无** 专用 `/health` |

程序内入口（非 HTTP）：`AgentResources` + `create_harness(...).execute_run(..., workflow=None)`。

## External File/Document Services

### MinerU（外部文档解析服务）

见上文「APIs & External Services」§3。产物只落本地 `downloads/`，不把完整 content_list/base64 图片塞进 tool result。

### 本地文档与模板

| 来源 | 路径模式 | 用途 |
|---|---|---|
| 用户上传 | `/artifacts/uploads/...` | 源 PDF/图片/办公文件 |
| 解析/业务产物 | `/artifacts/downloads/...` | MinerU JSON/ZIP、Tecan JSON/Excel、解压树 |
| Tecan 模板 | `/skills/tecanimport/assets/*.xlsx` | 生成时复制/填充，不修改仓库模板 |
| Skill 说明 | `/skills/<skill>/SKILL.md`；Tecan 另有 `references/*.md` | Agent 读取的指令与字段规则 |

DeepAgents 内置文件工具（如 `read_file`）经 `CompositeBackend` 访问上述虚拟路径；图片/媒体可走 `read_file`，结构化文档解析走 `parse_documents`。

### 无外部对象存储

上传与产物均为**本地磁盘**；无 S3/OSS/Azure Blob 集成。

## Environment Variables (names only, no values)

加载点：`runtime/agent.py`、`integrations/mineru.py` 在 import 时 `load_dotenv(backend/.env)`。下列键名来自代码与 `backend/.env.example`；**不记录任何真实值**。

### 生产 / 运行时（backend 消费）

| 键 | 用途 | 消费者 | 必填性 |
|---|---|---|---|
| `MINIMAX_API_KEY` | Anthropic 兼容 API key | `runtime/agent.py` | 生产 brain 需要 |
| `MINIMAX_BASE_URL` | Anthropic 兼容 base URL | `runtime/agent.py` | 生产 brain 需要 |
| `MINIMAX_MODEL` | 模型名（`anthropic:` 前缀拼接） | `runtime/agent.py` | 生产 brain 需要 |
| `MINERU_BASE_URL` | MinerU 服务根 URL | `integrations/mineru.py` | `parse_documents` 必填 |
| `MINERU_BACKEND` | 提交 form 的 backend 字段 | `integrations/mineru.py` | `parse_documents` 必填 |
| `MINERU_TIMEOUT_SECONDS` | HTTP 与任务总超时（秒） | `integrations/mineru.py` | `parse_documents` 必填 |
| `MINERU_EFFORT` | 提交 form 的 effort（可空） | `integrations/mineru.py` | 可选 |
| `ORACLE_DSN` | Oracle 连接 DSN | Philips `tools.py` | 可选（三者齐备才查） |
| `ORACLE_USERNAME` | Oracle 用户 | Philips `tools.py` | 可选 |
| `ORACLE_PASSWORD` | Oracle 密码 | Philips `tools.py` | 可选 |
| `ORACLE_CLIENT_LIB_DIR` | Instant Client 目录（thick） | Philips `tools.py` | 可选 |
| `ORACLE_TIMEOUT_SECONDS` | 连接/调用超时（默认 30） | Philips `tools.py` | 可选 |

### 测试 / 手工真实 run（测试脚本消费，非服务核心路径）

| 键 | 用途 | 消费者 |
|---|---|---|
| `DSAGENTS_BASE_URL` / `DSAGENTS_API_BASE_URL` | 指向已启动 HTTP 服务 | 真实集成测试 |
| `DSAGENTS_RUN_REAL_IMAGE_TEST` | 开关真实图片 run 测试 | `test_real_image_run.py` |
| `DSAGENTS_RUN_REAL_MULTI_PDF_TEST` | 开关真实多 PDF 测试 | `test_real_multi_pdf_run.py` |
| `DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST` | 开关 DHL/DSV/FedEx/UPS/康捷空真实识别验收 | `test_real_philips_wgq_inbound_recognition.py` |
| `DSAGENTS_PHILIPS_WGQ_SAMPLE_ROOT` | 覆盖外高桥进境样例根目录 | 同上 |
| `DSAGENTS_IMAGE_PATH` / `DSAGENTS_IMAGE_QUESTION` | 图片路径与提问 | `test_real_image_run.py` |
| `DSAGENTS_PDF_DIR` / `DSAGENTS_MULTI_PDF_REQUEST` | PDF 目录与请求文案 | `test_real_multi_pdf_run.py` |
| `DSAGENTS_REAL_*_TIMEOUT_SECONDS` / `*_POLL_SECONDS` / `*_UPLOAD_TIMEOUT_SECONDS` | 超时与轮询间隔 | 对应真实测试 |

> 交叉引用：Oracle 配置不全或不可用时 Philips 保留 PDF/Tracking 数据并把问题纳入 `partial_success`；Tecan 不读 `ORACLE_*`。MinerU 必填键缺失时工具快速失败，不静默跳过。

---
*Integrations analysis: 2026-07-16*
