---
last_mapped_commit: 28534a9
---

# Integrations

**Analysis Date:** 2026-07-16

> 外部集成边界基于 `api.py`、`runtime/`、`integrations/`、`skills/` 与 `backend/.env.example` 核对。只记键名与用途，不记录真实密钥或本地 `.env` 值。`POST /upload` 与 `RunMessage` 的 `artifact` block 只暴露 `/artifacts/...` 虚拟路径；`parse_documents` 内部 `allow_local=True` 可供测试/程序内路径，业务 Skill 工具只接受显式 artifact 路径。

## APIs & External Services

### 1. 本服务 HTTP API（FastAPI）

入口：`backend/api.py`。`create_app(*, resource_config=None, harness_factory=create_harness)`；模块级 `app = create_app()`。预期由：

```text
uv run uvicorn api:app --host 0.0.0.0 --port 8500
```

或仓库 `scripts/start-backend.ps1` 拉起（`-Port` 默认 8500；无 `-Server` 时新开窗口）。

| 方法 / 路径 | 入参 | 行为摘要 | 主要状态码 |
|---|---|---|---|
| `POST /runs` | `RunRequest`：`workflow?`、`session_id?`、`messages[]`（`text` / `artifact` block） | 分配 `run_id`；Philips workflow 强制新 `session_id`；写 ledger；daemon 线程执行 | `200` queued；`409` session 冲突；校验失败 `422` |
| `GET /runs/{run_id}` | query `after_event_id?` | run 快照 + 顶层 `workflow`/`result` + 增量 events + `latest_content_event` + `usage` | `200`；`404` 未知 run |
| `POST /runs/{run_id}/cancel` | path `run_id` | 投影 `cancelling` 并 `harness.request_cancel`；未进入执行则直接 `cancelled` | `202` cancelling；`200` 已取消中；`409` 终态；`404` 未知 |
| `POST /upload` | multipart 字段 `files`（可多文件） | 落到 `artifacts/uploads/`，返回虚拟路径 | `200` `files[]` |

补充：

- **无** SSE / `text/event-stream`；客户端靠轮询 `after_event_id`。
- `after_event_id` 只过滤 `events[]`；`latest_content_event` 与 `usage` 始终为当前全量值。
- **无** CORS 中间件。
- 时间字段：UTC ISO-8601 毫秒（ledger）。
- 取消不回滚已生成文件，不跨进程强杀 worker。
- 启动 lifespan：`fail_incomplete_runs("执行已中断，请重试")` 清理上次进程遗留非终态 run。

#### `RunRequest` 约束

- `workflow` 当前仅允许字面量 `"philips_wgq_inbound_recognition"` 或省略（通用 / Skill 驱动，如 Tecan）。
- `workflow is not None` 且同时带 `session_id` → 校验错误（workflow 必须新 session）。
- `messages[].content`：`type: "text"` + `text`，或 `type: "artifact"` + `path`。
- `extra="forbid"`：未知字段拒绝。

#### 事件类型（查询侧可见，固定 7 类）

执行层写入、`GET /runs/{run_id}` 返回的事件类型（`runtime/execution.py` + `runtime/runs.py`）：

| `type` | 来源概要 |
|---|---|
| `status` | 投影状态变更（queued/running/…）；终态可带 `reply` / `error` / 可选 `result` |
| `model_usage` | 主/子代理模型用量（token / cache）；**不计入** `latest_content_event` |
| `thinking` | 主代理 thinking 增量 |
| `text_delta` | 主代理文本增量 |
| `tool_execution` | `ToolTelemetry` 与 updates 派生的工具调用/结果摘要 |
| `tool_progress` | MinerU `parse_documents` / `extract_archives` 经 custom stream 的进度 |
| `assistant_message` | updates 中的完整助手消息摘要 |

`usage` 汇总：`api._usage_summary` 在 token 事实上叠加 cache hit rate 与 MiniMax-M3 分档 CNY **趋势**估价（未知模型则金额为 `null`）。

#### 程序内入口（非 HTTP）

`AgentResources` + `create_harness(...).execute_run(messages, session_id, run_id, workflow=...)`（`runtime/execution.py`）。测试与嵌入式调用走此路径。Stream：`stream_mode=["messages", "custom", "updates"]`、`subgraphs=True`、`version="v2"`、`control=RunControl`；`config.configurable.thread_id = session_id`。

---

### 2. LLM Provider：MiniMax（Anthropic 兼容）

| 项 | 事实 |
|---|---|
| 协议 | Anthropic Messages 兼容 HTTP |
| 接入 | `langchain.chat_models.init_chat_model("anthropic:{MINIMAX_MODEL}", ...)` → `ChatAnthropic` |
| 代码 | `runtime/agent.py` `DeepAgentsBrainFactory` |
| 能力 | `thinking={"type": "adaptive"}`；`StructuredOutputCompatibility` 在 ToolStrategy 请求上可关闭 thinking |
| 配置键 | `MINIMAX_API_KEY`、`MINIMAX_BASE_URL`、`MINIMAX_MODEL` |
| 示例默认（`.env.example`） | base `https://api.minimaxi.com/anthropic`；model `MiniMax-M3` |
| 定价元数据 | 仅服务侧 usage 展示；`PRICING_AS_OF=2026-07-12`；以 MiniMax 账单为准 |

`deepagents` profile `"anthropic"`：禁用自动 general-purpose subagent。

真实推理前需三键可用；工厂在构造时 `init_chat_model`，**无** lifespan 启动期强校验。

---

### 3. MinerU 文档解析服务

| 项 | 事实 |
|---|---|
| 客户端 | `integrations/mineru.py`，HTTP 库 `requests` |
| 配置键 | `MINERU_BASE_URL`（必填）、`MINERU_BACKEND`（必填）、`MINERU_EFFORT`（可空）、`MINERU_TIMEOUT_SECONDS`（必填） |
| 示例（`.env.example`） | base `http://10.11.0.110:6006`；backend `vlm-engine`；timeout `7200` |
| 轮询间隔 | `MINERU_POLL_INTERVAL_SECONDS = 30.0` |

**工具边界：**

| 工具 | 作用 |
|---|---|
| `parse_documents` | 批量提交本地/artifact 文件 → 轮询 → 下载 JSON 或 ZIP 到 `/artifacts/downloads/` |
| `extract_archives` | 将 ZIP artifact 解到 `/artifacts/downloads/<zip-stem>/` 并列出文件 |

**MinerU HTTP 约定（代码假设）：**

1. `POST {MINERU_BASE_URL}/tasks` — multipart `files` + form：`backend`、`effort`、`return_md`、`return_content_list`、`return_images`、`return_original_file`、`response_format_zip`（布尔以 form 字符串 `true`/`false` 传递）。
2. 响应 JSON 必须含字符串 `task_id`、`status_url`、`result_url`（相对 URL 会相对 base 拼接）。
3. `GET status_url` — `status` 为 `pending` / `processing` / `completed` / `failed`（大小写不敏感）。
4. `GET result_url` — JSON 对象或 ZIP 字节流。

**输出模式：**

- 默认：JSON → `result_path`（虚拟路径）。
- 任一 `return_md` / `return_images` / `return_original_file` / `response_format_zip` 为真 → 归一为 full ZIP 模式，写 `archive_path`。
- 进度通过 `langgraph.config.get_stream_writer` 发 custom 事件（`name: parse_documents` / `extract_archives`）。

缺失 `MINERU_*` 必填项时 `parse_documents` **快速失败**（`RuntimeError: Missing required environment variable: ...`）。

---

### 4. Oracle（Philips 主数据，可选）

| 项 | 事实 |
|---|---|
| 驱动 | `oracledb`（声明 `>=3,<4`，锁定 `3.4.2`） |
| 调用点 | `skills/philipswgqinboundrecognition/scripts/tools.py` → `lookup_philips_wgq_master_data` → `_oracle_data` |
| 配置键 | `ORACLE_DSN`、`ORACLE_USERNAME`、`ORACLE_PASSWORD`、`ORACLE_CLIENT_LIB_DIR`、`ORACLE_TIMEOUT_SECONDS` |
| Thick mode | 若 `ORACLE_CLIENT_LIB_DIR` 非空：进程内一次 `oracledb.init_oracle_client(lib_dir=...)`；`.env.example` 提示 Instant Client 可放在 gitignore 的 `backend/.oracle/...` |
| 连接 | `oracledb.connect(..., tcp_connect_timeout=timeout)`；`connection.call_timeout = int(timeout * 1000)` |
| 降级 | 三凭证任一缺失 → **不抛**，返回 `problems`（提示配置 Oracle 或人工补齐）；连接/查询异常同样落入 `problems` |
| SQL | 按 `product_id`（12NC）查 `od.chda` 并 join `dongsong.good` / `dongsong.custom_unit`，填充 `ORACLE_FIELDS`：`chinese_name`、`specification`、`origin_country`、`customs_code`、`unit`、`legal_unit_1`、`legal_unit_2` |

**与 Tracking 的优先级（工具内）：** 先读可选 Tracking `.xlsx`（`进口` / `申报要素` sheet，`openpyxl`），再用 Oracle **仅补齐**仍为 `null` 的 `ORACLE_FIELDS`；不得用 Oracle 覆盖 Tracking 已有值。申报要素、BU 等不在 Oracle 字段集。

**Tecan 不消费 Oracle。**

---

### 5. Artifacts 存储（本地文件系统）

| 项 | 事实 |
|---|---|
| 物理根 | `ResourceConfig.artifacts_dir` → `backend/data/artifacts/` |
| 虚拟前缀 | `/artifacts/...`（`PurePosixPath`，禁止 `..`） |
| 上传 | `POST /upload` → `artifacts/uploads/`，时间戳化唯一名，返回 `file_path` 如 `/artifacts/uploads/<name>` |
| 下载/产出 | MinerU / Tecan / 通用 `write_json_artifact` → 主要在 `artifacts/downloads/` |
| 路径工具 | `integrations/artifacts.py`：`resolve_artifact_path`、`to_virtual_artifact_path`、`write_json_artifact`、`read_json_artifact`、`make_timestamped_name` 等 |
| deepagents 路由 | `/artifacts/` 与 `/large_tool_results/` → 同一 `FilesystemBackend` |
| 权限 | 主代理 deny write `/skills/**`；Tecan extractor SubAgent deny write `/**`（只读文件，写结果靠工具落盘） |

**客户端契约：** 消息里的 artifact 路径必须是上传或先前工具返回的虚拟路径；业务工具不扫描“最近文件”。

---

### 6. SQLite 持久化（进程本地，非外部 SaaS）

| 库文件 | 角色 | 集成方 |
|---|---|---|
| `dsagents_runs.db` | run 投影 + 事件索引 | `SqliteRunLedger` |
| `dsagents_store.db` | 跨 run store（`/memories/`） | `SqliteStore` |
| `dsagents_checkpoints.db` | LangGraph checkpoint（`thread_id`=`session_id`） | `SqliteSaver` |
| `data/internal/run-events/` | 超阈值事件 JSON 外置 | ledger `max_inline_bytes` |

`session_id` **不是**独立 REST 资源：仅用于 checkpoint `thread_id` 与进程内单飞锁。

---

## Skill 与外部系统边界

工具静态注册于 `runtime/tools.py` `default_tool_catalog()`；新增 Skill 需显式 import + 注册，**不**自动扫描。

### Philips WGQ 进境识别

| 项 | 事实 |
|---|---|
| 包路径 | `skills/philipswgqinboundrecognition/` |
| API workflow | `workflow="philips_wgq_inbound_recognition"`（`WORKFLOW` 常量） |
| 外部依赖 | MinerU（PDF）+ 可选 Tracking Excel（openpyxl）+ 可选 Oracle |
| 工具 | `parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`（workflow 排除帝肯工具，保留共享 MinerU 工具） |
| 输出 | `ToolStrategy(PhilipsWgqRecognitionResult)` → `run.result`；业务失败形态 `input_problems` 时 run 仍 `succeeded` |
| SubAgent | **禁用**（workflow 时 `subagents=[]`） |
| 文档 | `SKILL.md` 约束业务输入形态；手册与工具表需同时可见 `extract_archives` |

### Tecan 帝肯进口

| 项 | 事实 |
|---|---|
| 包路径 | `skills/tecanimport/` |
| 触发 | 非 API workflow 字段；由 Skill 描述 + 用户意图驱动（通用 agent + skills 挂载） |
| 外部依赖 | MinerU（空运 PDF）；订单/信息表 Excel（openpyxl）；模板 `assets/Tecan_进口_发票箱单_空运.xlsx` |
| 工具 | `save_tecan_extraction`、`generate_tecan_import`（另用通用 `parse_documents`） |
| SubAgent | `tecan-extractor-a` / `tecan-extractor-b`（只读 FS + `save_tecan_extraction` + `ExtractionReference` 结构化输出） |
| 输出 | `generate_tecan_import` → `status: generated` + Excel/canonical artifacts，或 `code: input_problems` |
| 参考 | `references/fields.md`、`references/rules.md` |
| 内部常量 | 工具模块内 `WORKFLOW = "tecan-import"`（抽取 payload 标记，**不是** HTTP `RunRequest.workflow` 字面量） |

### 通用运行手册

`/memories/AGENTS.md`（StoreBackend）种子内容指导：`parse_documents` 优先读 `result_path`；ZIP 须先 `extract_archives` 再 `read_file`。仅首次缺失时写入，不覆盖后续追加。`MemoryMiddleware` 在工具失败后可指导模型向该手册追加误用笔记（禁止写入业务数据与密钥）。

---

## Environment Variables（汇总，仅键名）

### 运行时 / 生产相关（`backend/.env.example`）

| 键 | 类别 | 必填性 |
|---|---|---|
| `MINIMAX_API_KEY` | LLM | 真实推理需要 |
| `MINIMAX_BASE_URL` | LLM | 真实推理需要 |
| `MINIMAX_MODEL` | LLM | 真实推理需要 |
| `MINERU_BASE_URL` | MinerU | `parse_documents` 必填 |
| `MINERU_BACKEND` | MinerU | 必填 |
| `MINERU_EFFORT` | MinerU | 可选 |
| `MINERU_TIMEOUT_SECONDS` | MinerU | 必填 |
| `ORACLE_DSN` | Oracle | 可选；缺则 Philips 主数据降级 |
| `ORACLE_USERNAME` | Oracle | 可选 |
| `ORACLE_PASSWORD` | Oracle | 可选 |
| `ORACLE_CLIENT_LIB_DIR` | Oracle thick | 可选 |
| `ORACLE_TIMEOUT_SECONDS` | Oracle | 可选（代码默认 30） |

加载路径：`backend/.env`（`BACKEND_ENV_PATH`）。

### 真实集成测试用（仅 `tests/`，非 `.env.example`）

包括但不限于：`DSAGENTS_API_BASE_URL` / `DSAGENTS_BASE_URL`、`DSAGENTS_RUN_REAL_*` 开关、样本路径与超时/轮询秒数等。这些只影响 opt-in 真实回归，不参与默认 `create_app()` 配置。

---

## 数据流（集成视角）

```text
Client
  │  POST /upload  ──►  disk: data/artifacts/uploads  ──►  /artifacts/uploads/...
  │  POST /runs    ──►  SqliteRunLedger (queued)
  │                     daemon thread → HarnessRuntime.execute_run
  │                         │
  │                         ├─ DeepAgentsBrain + MiniMax (Anthropic-compatible)
  │                         ├─ tools:
  │                         │     parse_documents ──HTTP──► MinerU ──► /artifacts/downloads
  │                         │     extract_archives ──local zip──► /artifacts/downloads/<stem>/
  │                         │     lookup_philips... ──xlsx + optional Oracle──► master fields
  │                         │     save_tecan_extraction / generate_tecan_import ──xlsx/json──► downloads
  │                         ├─ checkpoint/store SQLite (session_id = thread_id)
  │                         └─ emit events → ledger
  │  GET /runs/{id}  ◄──  snapshot + events + usage
  └  POST /runs/{id}/cancel ──► RunControl.drain / cancelled
```

---

## 非集成 / 明确不做

- 无 session CRUD REST、无 SSE 推送、无内置对象存储（S3 等）。
- 无独立“文件下载”HTTP 路由：产物靠虚拟路径 + 共享磁盘/后续封装。
- Oracle 与 MinerU **不**在 lifespan 做健康检查；失败发生在工具调用时。
- 密钥不进 ledger 事件正文设计目标；文档与代码映射不记录真实密钥。
- 无 webhook 入站/出站、无 Redis/Postgres 等外部消息或关系库。

## Analysis 边界

- 仅 **backend** 对外/对外部系统边界；前端如何轮询不在此展开。
- HTTP 契约细节与跨边界任务阅读仍以仓库 `INTERFACES.md`、`coding_maps/SYSTEM_MAP.md` 为准；本文聚焦集成点与配置键事实。
