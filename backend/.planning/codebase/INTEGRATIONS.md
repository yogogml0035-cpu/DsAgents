---
last_mapped_commit: 3dadbc4
analysis_date: 2026-07-20
focus: tech
---

# INTEGRATIONS — backend 外部集成事实

Analysis Date: **2026-07-20**
`last_mapped_commit`: **3dadbc4**

本文档描述 `backend/` 与外部系统、本地持久化及 HTTP 对外面的集成边界。标识符、路径、配置键、API 名保留原文；**不记录**真实密钥 / token / 连接串。

---

## 1. LLM / OpenAI-compatible（Anthropic 协议）Providers

### 1.1 生产配置方式

生产 Brain 由 `DeepAgentsBrainFactory`（`runtime/agent.py`）创建：

1. 启动时 `load_dotenv(backend/.env)`。
2. 若构造未注入 `model`，则：

```python
init_chat_model(
    f"anthropic:{os.getenv('MINIMAX_MODEL')}",
    api_key=os.getenv("MINIMAX_API_KEY"),
    base_url=os.getenv("MINIMAX_BASE_URL"),
    thinking={"type": "adaptive"},
)
```

3. `create_deep_agent(model=..., tools=..., backend=..., checkpointer=..., store=..., skills=["/skills/"], ...)`。

| 环境变量 | 作用 |
|---|---|
| `MINIMAX_API_KEY` | 提供商 API key |
| `MINIMAX_BASE_URL` | Anthropic-compatible base URL（模板：`https://api.minimaxi.com/anthropic`） |
| `MINIMAX_MODEL` | 模型名（模板：`MiniMax-M3`）；前缀 `anthropic:` 走 langchain-anthropic |

### 1.2 可注入性

- `Brain` / `BrainFactory` 为 `typing.Protocol`。
- 测试可注入 `FakeBrainFactory` / 自定义 `model`（`tests/test_support.py`、`create_app(harness_factory=...)`）。
- `create_harness(resources)` 默认装配 `DeepAgentsBrainFactory()` + `default_tool_catalog()`。

### 1.3 调用形态

`HarnessRuntime.execute_run` 统一：

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

- `thread_id = session_id`（LangGraph checkpointer 键）。
- 查询维度始终是 `run_id`（ledger）。
- `artifact` content block 在入 Brain 前转为 `ARTIFACT_REFERENCE_HINT` 文本路径提示。

### 1.4 Thinking / 结构化输出与 provider 交互

- 默认模型 `thinking={"type": "adaptive"}`（MiniMax / Anthropic 扩展）。
- `StructuredOutputCompatibility` 在 **ToolStrategy** 请求上通过 `request.override(model=...)` **关闭当次 thinking**，以兼容强制 tool choice；工厂原始 model 不变。
- Philips workflow：`ToolStrategy(PhilipsWgqRecognitionResult)`；Tecan SubAgent：`ToolStrategy(ExtractionReference)`。
- `StructuredOutputRecovery`：有界 `after_model` 恢复；空 data 壳与耗尽策略见 `Agents.md` / harness 测试（不在此重复实现细则）。

### 1.5 用量与费用估算（非外部计费 API）

- 流式 `messages` 通道提取 `model_usage` 事件写入 ledger。
- `GET /runs/{run_id}` 聚合 token，并对已知模型 `MiniMax-M3` 做 **CNY 趋势估算**（`api.py` 硬编码档位；非 MiniMax 官方账单 API）。未知 model → `estimated_cost_cny = null`。

### 1.6 降级

| 情况 | 行为 |
|---|---|
| 缺少 / 无效 `MINIMAX_*` | 首次真实模型调用失败；run 投影 `failed` |
| 注入 FakeBrain / 测试 model | 不触网 |
| provider 超时 / 5xx | 异常 → run `failed`（无自动重试队列） |

---

## 2. MinerU 文档解析 HTTP

### 2.1 位置与工具

- 实现：`integrations/mineru.py`
- 静态注册工具：`parse_documents`、`extract_archives`（`runtime/tools.py`）
- 输出目录：`data/artifacts/downloads/`（虚拟路径 `/artifacts/downloads/...`）

### 2.2 配置

| 变量 | 必填 | 说明 |
|---|---|---|
| `MINERU_BASE_URL` | 是 | 服务根 URL |
| `MINERU_BACKEND` | 是 | 提交表单 `backend`（如 `vlm-engine`） |
| `MINERU_EFFORT` | 否 | 表单 `effort`；默认空串 |
| `MINERU_TIMEOUT_SECONDS` | 是 | 连接/读写超时秒数 |

缺必填变量：`RuntimeError("Missing required environment variable: ...")`。

### 2.3 HTTP 交互

| 步骤 | 方法 | 路径 / URL | 说明 |
|---|---|---|---|
| 提交 | `POST` | `{MINERU_BASE_URL}/tasks` | multipart：`files` + `backend` / `effort` / 输出选项 bool 表单字段 |
| 轮询 | `GET` | 响应中的 `status_url`（相对 URL 经 `urljoin` 规范化） | 状态 `pending` / `processing` / `completed` / `failed` |
| 下载 | `GET` | `result_url` | JSON 模式写 `result_path`；ZIP 模式写 `archive_path` |

- 轮询间隔：`MINERU_POLL_INTERVAL_SECONDS = 30.0`
- 超时：整体截止 `timeout_seconds`；超时 `TimeoutError`
- 客户端库：`requests`（非 httpx）

### 2.4 输出模式

- **默认 JSON**：`return_content_list=True` 等 → 下载 JSON 到 `/artifacts/downloads/<stem>.json`（`result_path`）
- **ZIP 模式**：请求 md/images/original/`response_format_zip` 任一为真时，归一为完整 ZIP（`archive_path`）；随后应用 `extract_archives` 解压到 `/artifacts/downloads/<zip-stem>/`

### 2.5 可观测性

- 通过 LangGraph `get_stream_writer()` 发 custom payload（`name=parse_documents|extract_archives`）
- harness 映射为 `tool_progress` 事件（与 `ToolTelemetry` 的 `tool_execution` 区分）

### 2.6 降级 / 失败

| 情况 | 行为 |
|---|---|
| 输入路径无效 | 记入 `failed[]`；若无一有效文件，返回空成功结构且不调 MinerU |
| 任务 `failed` / 非预期 status | `RuntimeError`；custom status=`failed` |
| 下载失败 | 抛异常；工具失败可导致 agent 重试或 run 失败 |
| 阻塞轮询中 cancel | drain 可能延迟到当前 `requests` / sleep 返回后 |

`parse_documents` 程序内解析路径 `allow_local=True`（测试便利）；HTTP API 上传路径始终是 `/artifacts/...`。

---

## 3. Oracle 主数据查询

### 3.1 位置

- 工具：`lookup_philips_wgq_master_data`（`skills/philipswgqinboundrecognition/scripts/tools.py`）
- 驱动：`oracledb`（可选 thick mode）
- 业务：按 12NC / product_id 补齐飞利浦外高桥主数据字段；优先 Tracking Excel，Oracle 仅补 `ORACLE_FIELDS` 中仍为 null 的稳定字段

### 3.2 配置

| 变量 | 说明 |
|---|---|
| `ORACLE_DSN` | 连接串 |
| `ORACLE_USERNAME` | 用户 |
| `ORACLE_PASSWORD` | 密码 |
| `ORACLE_CLIENT_LIB_DIR` | Instant Client 目录；设置则 `oracledb.init_oracle_client(lib_dir=...)`（进程内一次） |
| `ORACLE_TIMEOUT_SECONDS` | TCP / call 超时（默认 30s） |

### 3.3 查询

- SQL 固定查询 `od.chda` 等（品名、规格、原产国、HS、计量单位与法定单位名）。
- 每个 product_id 单独 `cursor.execute` + `fetchone`。
- 连接：`oracledb.connect(user=..., password=..., dsn=..., tcp_connect_timeout=timeout)`，`connection.call_timeout = int(timeout * 1000)`。

### 3.4 降级（**不阻塞 run 创建**）

| 情况 | 返回 |
|---|---|
| DSN/用户/密码任一空 | `({}, [problem: "Oracle 配置缺失"])`；调用方可人工补齐 |
| 连接 / 查询异常 | `({}, [problem: "Oracle 查询失败：..."])` |
| 未命中行 | 对该 product_id 记 problem「Oracle 未命中 12NC」 |
| 无 product_ids | 空映射、无 problem |

Tracking `.xlsx` 读取失败同样记 problem，不抛穿服务。工具整体返回 `items` + `problems` 结构供 agent 写入业务 `outcome`。

---

## 4. SQLite Run Ledger

### 4.1 组件

- 类：`SqliteRunLedger`（`runtime/runs.py`）
- 库文件：`data/dsagents_runs.db`
- 大 payload 外溢目录：`data/internal/run-events/<uuid>.json`（默认 inline ≤ 262144 字节）

### 4.2 表

**`runs`（投影快照）**

| 列 | 含义 |
|---|---|
| `run_id` | PK |
| `session_id` | LangGraph thread / 会话单飞 |
| `input_messages_json` | 请求 messages |
| `workflow` | 可选；如 `philips_wgq_inbound_recognition` |
| `status` | `queued` / `running` / `succeeded` / `failed` / `cancelled` / `cancelling` |
| `created_at` / `updated_at` | UTC+8 本地文本时间 |
| `reply` | 成功时助手文本 |
| `error` | 失败 / 取消说明 |
| `result_json` | 成功时业务 JSON（Philips `run.result`） |

**`run_events`（append-only）**

| 列 | 含义 |
|---|---|
| `event_id` | 自增 PK |
| `run_id` | 外联逻辑 |
| `type` | 事件类型 |
| `created_at` | UTC+8 |
| `payload_json` / `payload_artifact_path` | 业务 payload 或外溢指针 |
| `raw_json` / `raw_artifact_path` | 原始 chunk 或外溢 |

### 4.3 事件类型（固定 7 类）

`status` · `tool_execution` · `tool_progress` · `thinking` · `text_delta` · `assistant_message` · `model_usage`

- `latest_content_event` 排除 `status` 与 `model_usage`。
- `model_usage` 可按 agent 聚合，供 API `usage` 字段。

### 4.4 生命周期钩子

- 启动 lifespan：`fail_incomplete_runs("执行已中断，请重试")` 将 `queued` / `running` / `cancelling` 标 `failed`。
- 状态机：`queued → running → succeeded|failed|cancelled`；取消：`queued → cancelled` 或 `running → cancelling → cancelled`。

### 4.5 与其它 SQLite 的关系

| 库 | 类 | 用途 |
|---|---|---|
| `dsagents_runs.db` | `SqliteRunLedger` | run 投影 + 事件 |
| `dsagents_checkpoints.db` | `SqliteSaver` | LangGraph checkpoint（`thread_id=session_id`） |
| `dsagents_store.db` | `SqliteStore` | 跨 run `/memories/`（namespace `("dsagents",)`） |

三库**无**跨库事务、**无**共享连接。

---

## 5. 本地 Artifacts 文件系统

### 5.1 路径约定

| 物理 | 虚拟前缀 | 写入方 |
|---|---|---|
| `data/artifacts/uploads/` | `/artifacts/uploads/` | `POST /upload` |
| `data/artifacts/downloads/` | `/artifacts/downloads/` | MinerU、解压、Tecan JSON/Excel |
| `backend/skills/` | `/skills/` | 只读 Skill 资源（主 Agent write deny `/skills/**`） |
| （StateBackend 默认） | 其它虚拟路径 | 会话态 |
| `SqliteStore` | `/memories/` | 跨 run 手册与记忆 |

`CompositeBackend` 路由（`runtime/resources.py`）：

- `/memories/` → `StoreBackend`
- `/artifacts/`、`/large_tool_results/` → `FilesystemBackend(artifacts_dir)`
- `/skills/` → `FilesystemBackend(skills_dir)`
- default → `StateBackend`

### 5.2 路径安全

- `integrations/artifacts.py`：`resolve_artifact_path` 拒绝 `..`；默认要求显式 `/artifacts/...`。
- `to_virtual_artifact_path` / `write_json_artifact` / `read_json_artifact` 供 Skill 与工具使用。
- 上传：`clean_filename` + 同批 `batch_timestamp` + `make_timestamped_name` 防冲突。

### 5.3 降级

- 取消 / 失败 **不回滚** 已写入 downloads。
- 磁盘满 / 权限错误 → 上传或工具失败；run 视调用栈标 `failed`。

---

## 6. OMS 旁路 JSONL 索引（`oms_log`）

### 6.1 定位

- 实现：`runtime/oms_log.py`
- 默认文件：`backend/log/oms_log.log`（锚定 `backend/`，**不在** `data/`）
- **不是** `run_events` 第 8 类；**无** HTTP 查询 API；供运维按时间 / 文件名 stem grep

### 6.2 写入时机

- **仅** HTTP `POST /runs` 在 `resources.runs.create_run(...)` **成功之后** best-effort 调用 `append_run_created_log(...)`。
- 写失败：`except Exception: pass`，**不**影响已创建 run 的 `200 queued` 与后台线程。
- **不写**场景：`422` / `409`、`POST /upload`、cancel、终态更新、**程序内** `create_run` + `execute_run` 直调路径。

### 6.3 记录形状

每行一条 JSON：

| 字段 | 含义 |
|---|---|
| `event` | 固定 `"run_created"` |
| `created_at` | UTC+8 `YYYY-MM-DD HH:MM:SS` |
| `run_id` | |
| `session_id` | |
| `workflow` | 可 null |
| `files` | 从 messages 中 `type=artifact` 收集的 `[{name, path}, ...]`（顺序保留，不去重） |

**不含**：prompt 全文、thinking、工具 raw、业务 `result`。

---

## 7. HTTP API 对外端点

入口：`api.py` → `create_app()` → `app`；默认 `uv run uvicorn api:app --host 0.0.0.0 --port 8500`。

| 方法 / 路径 | 入参 | 成功响应 | 主要错误 |
|---|---|---|---|
| `POST /upload` | multipart 字段 `files`（可多文件） | `200 {"files":[{"file_path","name","mime_type","size"}]}`；`file_path` 形如 `/artifacts/uploads/...` | 框架层校验错误 |
| `POST /runs` | JSON：`workflow?`（仅 `philips_wgq_inbound_recognition`）、`session_id?`、`messages[]`（`text`/`artifact` blocks，`extra=forbid`） | `200 {"run_id","session_id","status":"queued"}` | `422` 校验 / workflow 复用 session；`409` 同 session 已有活跃 run |
| `GET /runs/{run_id}` | query `after_event_id?` | `200 {"run","workflow","result","events","latest_content_event","usage"}` | `404` 未知 run |
| `POST /runs/{run_id}/cancel` | path `run_id` | `202 {"status":"cancelling"}` 或 `200` 幂等已取消中 | `404`；`409` 已终态 `succeeded`/`failed` |

补充：

- **无 SSE** / WebSocket；客户端轮询 GET。
- Philips workflow **禁止**客户端复用 `session_id`（须服务端新 session）。
- 业务 JSON 读 GET 顶层 `result`（或 `run.result`），**不**解析 `reply` 为业务契约。
- Philips `outcome=input_problems` 时 run 仍可 `succeeded`（业务问题 ≠ 执行失败）。
- `after_event_id` **只裁剪** `events[]`，不影响 `latest_content_event` 与 `usage`。
- 会话单飞：进程内 `threading.Lock` + `active_runs`；冲突中文错误「该会话正在运行」。

### 7.1 Cancel 集成

1. 活跃 run 投影 `cancelling`。
2. `harness.request_cancel(run_id)` → `RunControl.request_drain`。
3. 执行循环遇 drain → `GraphDrained` → `cancelled`。
4. 尚无 control（queued 未进入 execute）：直接 `cancelled`。
5. **不**跨进程强杀；工具阻塞时 drain 延迟。

### 7.2 程序内接口（非 HTTP）

```python
with AgentResources(ResourceConfig()) as resources:
    harness = create_harness(resources)
    resources.runs.create_run(run_id, session_id, messages_json, workflow=...)
    for _ in harness.execute_run(messages, session_id, run_id, workflow=...):
        pass
```

程序内路径 **不写 OMS**。

---

## 8. Auth

| 项 | 现状 |
|---|---|
| HTTP 鉴权 | **无**（无 API key 头校验、无 OAuth、无依赖注入 security scheme） |
| CORS | **未**配置专用 CORS 中间件 |
| 上传 / run 访问控制 | 依赖部署网络边界 |
| 密钥存放 | 仅服务端 `backend/.env`；注释要求不暴露给 frontend |
| 模型 key | 仅出站 MiniMax；不回传客户端 |

映射时源码 grep 无 `Authorization` / Bearer 中间件；测试中的 `api_key=` 仅为 Fake / 模型构造参数。

---

## 9. 外部服务降级行为汇总

| 依赖 | 缺失 / 失败时 | 是否阻塞 run 创建 |
|---|---|---|
| MiniMax LLM | 执行期 `failed` | 否（先 queued，后台失败） |
| MinerU | 工具错误；agent 可重试或 run 失败 | 否 |
| Oracle 凭证缺失 | tool 返回 problems，人工补齐 | 否 |
| Oracle 查询失败 | tool 返回 problems | 否 |
| Instant Client / thick | 未设 `ORACLE_CLIENT_LIB_DIR` 则不 init thick；连接失败走 problem | 否 |
| OMS 日志写失败 | 吞异常 | **否**（明确 best-effort） |
| SQLite / 磁盘 | 创建 run 或 emit 可能抛错；上传失败 | 创建阶段可能 500 |
| Checkpoint / Store | 启动 lifespan 装配失败则服务起不来 | N/A（进程级） |
| 进程重启 | 未完成 run 标 `failed`（「执行已中断，请重试」） | N/A |

---

## 10. 业务 Skill 与外部系统触点

| Skill | 外部 / 本地触点 |
|---|---|
| Philips WGQ（`philips_wgq_inbound_recognition`） | MinerU（共享工具）、Tracking Excel（openpyxl）、Oracle 主数据、结构化 `run.result` |
| Tecan import | MinerU（共享）、本地 Excel 模板 `/skills/tecan-import/assets/`、JSON artifacts；**无** Oracle |

工具注册中心：`runtime/tools.default_tool_catalog()`（静态 5 工具，不自动扫描）。

Philips workflow **denylist** 去掉 Tecan 业务工具，**保留** `parse_documents` / `extract_archives` 与 `lookup_philips_wgq_master_data`。

---

## 11. 映射边界

- 范围：`backend/` 源码与配置；交叉核对根级 `INTERFACES.md` / `Agents.md`，以本轮源码为准。
- 不读取 `.env` 真实密钥；模板以 `.env.example` 为准。
- 不把 `data/artifacts` 样本内容或 `log/oms_log.log` 业务行写入本文。
- `httpx2` 为依赖声明项，当前业务路径 MinerU 使用 `requests`。

---

*End of INTEGRATIONS.md*
