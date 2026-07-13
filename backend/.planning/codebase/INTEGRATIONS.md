# INTEGRATIONS

> 外部集成与依赖边界。事实基于当前代码核对，区分「已确认」与「需确认」。
> 本轮刷新（2026-07-13）已逐文件核对当前工作树：`dsagents/api.py`、`dsagents/runtime/`、`dsagents/integrations/`、两个内置 Skill 包；HTTP/run ledger/MinerU/上传/artifacts 边界、DeepAgents Skills/SubAgents、Oracle 与 Excel 模板链均与代码一致。

## 1. HTTP 框架（FastAPI + uvicorn）

入口模块：`dsagents/api.py`。`create_app(*, resource_config: ResourceConfig | None = None, harness_factory: Callable[[AgentResources], HarnessRuntime] = create_harness)` 返回 `FastAPI(lifespan=lifespan)`，模块底部 `app = create_app()`，预期由 `uv run uvicorn dsagents.api:app` 拉起（uvicorn 作为依赖声明存在，但 `api.py` 未直接 import；测试用 `harness_factory` 注入 `FakeBrainFactory`）。

### 端点契约

| 方法 / 路径 | 入参 | 行为 | 返回 |
|---|---|---|---|
| `POST /runs` | `{"session_id": str\|null, "messages": [{"role": str, "content": [{"type":"text","text":str} \| {"type":"artifact","path":str}]}...]}`（`RunRequest`，`ConfigDict(extra="forbid")`） | `session_id` 为空生成 `uuid4().hex`；`run_id = uuid4().hex`；获取单飞锁 → 写 ledger → 起 daemon 线程执行 | `200 {"run_id","session_id","status":"queued"}`；校验失败 `422`；冲突 `409 {"error":"该会话正在运行","active_run_id"}` |
| `GET /runs/{run_id}` | query `after_event_id: int\|null` | 读 run 快照 + run events（支持增量游标）+ 当前 run 全局最新非 `status`/非 `model_usage` 事件 + 该 run 全部 `model_usage` 汇总出的 `usage` | `200 {"run":{...},"events":[...],"latest_content_event":{...}\|null,"usage":{...}\|null}`；未知 run `404 {"error":"Unknown run: ..."}` |
| `POST /runs/{run_id}/cancel` | path `run_id` | 见 §取消流 | `404`（未知）/ `409`（终态）/ `200`（已 cancelling/cancelled）/ `202`（活跃 drain） |
| `POST /upload` | multipart `files: list[UploadFile] = File(...)`（字段名固定 `files`，支持 1 个或多个） | 同一请求共用一个 `batch_timestamp`；落到 `<artifacts_dir>/uploads/<cleaned-stem>_<upload-ts>(_n).ext`；只有真实物理重名时才追加序号；`name` 返回清洗后的原始文件名 | `200 {"files":[{"file_path":"/artifacts/uploads/...","name":"<原名>","mime_type":"<mime-or-application/octet-stream>","size":123}]}` |

> 注：当前**无 SSE / `StreamingResponse` / `text/event-stream`**，事件获取靠轮询 `GET /runs/{run_id}?after_event_id=...`。
> 注：`after_event_id` **只影响 `events[]`**；`latest_content_event` 始终返回该 run 当前最新的非 `status`/非 `model_usage` 事件，没有则为 `null`；顶层 `usage` 也不受 `after_event_id` 影响。
> 注：当前**未注册 `CORSMiddleware`**，也没有 CORS 配置消费者。
> 注：run / event 响应里的时间字段统一为 UTC ISO-8601 毫秒（`2026-07-13T08:18:59.250Z`）。

### 取消流（`POST /runs/{run_id}/cancel`）

`dsagents/api.py` 的 `cancel_run`：

- 未知 run → `404 {"error":"Unknown run: <run_id>"}`。
- 终态（`succeeded`/`failed`）→ `409 {"error":"Run already terminal: <status>","status":<status>}`。
- 已 `cancelling`/`cancelled` → `200 {"status":<status>}`。
- 活跃 run（`queued`/`running`）→ 投影 `cancelling` 事件 → `harness.request_cancel(run_id)` 触发 LangGraph `RunControl` 协作 drain → `GraphDrained` 在 `execute_run` 内投影为 `cancelled`；若 run 尚未进入 `execute_run`（`queued` 或未注册 `RunControl`），直接置 `cancelled`，返回 `202 {"status":"cancelling"}`。

取消不回滚已生成文件，不实现多进程强杀。

### `usage` 出口结构（`dsagents/api.py _usage_summary`）

基于 `dsagents/runtime/runs.py aggregate_model_usage(run_id)` 的原始 token 总量，叠加 cache hit rate 与 tier-aware CNY 估算（`PRICING_AS_OF = "2026-07-12"`，MiniMax-M3 standard 定价）：

- `model_calls`、`input_tokens`、`output_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens`、`cache_hit_rate`（无输入为 `null`）。
- `estimated_cost_cny` / `estimated_savings_cny`：按**每个模型调用自身 input ≤/> 512k tokens** 分 tier（standard / long_context）后汇总；cache creation 按非 cache-read input 计价，savings 为 cache-read 相对 standard input 的折扣。任意一次调用的模型不在可计价集合（`_PRICEABLE_MODELS = {"MiniMax-M3"}`）内时，两个金额均为 `null`，token 计数仍完整。
- `estimated_cost_note` + `pricing_as_of`：标注仅为趋势估算，最终以 MiniMax 实际账单为准。
- `by_agent[]`：按 `(scope, agent_name)` 分组，每项含 `scope`、`agent_name`、`model_calls`、四类 token 量与自身 `cache_hit_rate`。

### artifact block 与上传能力

- `artifact` block 是**项目 API 语义**，不是直接发给 LangChain 的标准多模态 block。
- `HarnessRuntime.execute_run(...)`（`dsagents/runtime/execution.py`）把 `artifact` block 转成文本提示 `ARTIFACT_REFERENCE_HINT`（`Uploaded artifact: {path}. Use read_file ... or parse_documents ...`），再把归一化后的 `messages[]`（全部转成 `{"type":"text","text":...}`）发给 Brain。
- 常见办公文件和任意图片都可以通过 `POST /upload` 保存；能否被解析或理解取决于 DeepAgents `read_file`、`parse_documents`、MinerU 和模型多模态能力。

### lifespan

启动：装配 `AgentResources`、`fail_incomplete_runs("执行已中断，请重试")`（把遗留 `queued`/`running`/`cancelling` run 标 `failed`）、构建 harness、初始化锁注册表（`session_locks` / `active_runs` / `registry_lock`）。
停止：`resources.__exit__` 关闭 SQLite 连接上下文。

## 2. LLM Provider 集成边界

| 边界 | 实现 | 证据 |
|---|---|---|
| 生产 brain | `DeepAgentsBrainFactory`：`init_chat_model("anthropic:<MODEL>", ...)` → `ChatAnthropic`；`create_deep_agent(...)` 同时注入 `skills=["/skills/"]`、四个 SubAgents、`/skills/**` 写禁令、主 Agent middleware 与主 agent 名（`MAIN_AGENT_NAME = "dsagents-main"`） | `dsagents/runtime/agent.py` |
| 本地测试 brain | `FakeBrain` / `FakeBrainFactory`（模拟 v2 stream chunk，`updates`+`subgraphs`，不触达真实 provider） | `backend/tests/test_support.py` |
| 系统 prompt | `DEFAULT_SYSTEM_PROMPT` 引导文件工具，并明确只有用户清晰要求业务结果时才使用业务 Skill；普通 PDF 请求不触发业务流程 | `dsagents/runtime/agent.py` |
| prompt-cache 中间件 | **不新增自定义 cache middleware**。`create_deep_agent` 已在尾栈自动挂 `AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore")`（`deepagents/graph.py`），给 system 末块与末个 tool 打 `cache_control={"type":"ephemeral","ttl":"5m"}`；因为 MiniMax 走 `ChatAnthropic`，该中间件对 MiniMax-M3 生效。固定前缀 = `DEFAULT_SYSTEM_PROMPT` + `default_tool_catalog()` tool schema + SDK 默认 deep-agent prompt，**不要**向其注入时间/run_id 等动态内容 | `dsagents/runtime/agent.py` + `langchain_anthropic/middleware/prompt_caching.py`（库源） |
| usage 观测出口 | usage 不实现为 Agent middleware，而是复用 `execute_run` 的统一 `messages` 流出口：在 subagent 文本过滤之前从终态 chunk 的 `usage_metadata` 提取（`dsagents/runtime/observability.py model_usage`），每个模型调用仅在非空时写一次 `model_usage` 事件（含 subagent 调用）；不写入 AgentState/checkpointer/store，不新增表 | `dsagents/runtime/{execution,observability,runs}.py` + `api._usage_summary` |

### Brain / BrainFactory 边界（模块归属）

- `Brain` / `BrainFactory` 是 `dsagents/runtime/agent.py` 内定义的 `Protocol`（`stream(payload, config, **kwargs)` / `create(*, resources, middleware, tools)`）；`DeepAgentsBrainFactory` 是其生产实现，`HarnessRuntime`（`dsagents/runtime/execution.py`）持有并驱动它。
- `create_harness`（`dsagents/runtime/execution.py`）装配：`tools=default_tool_catalog()`、`brain_factory=DeepAgentsBrainFactory()`；`execute_run` 把 `runtime_middlewares()` 与 `tools.as_list()` 一起传给 `brain_factory.create(...)`。
- 旧的 `Hands` Protocol / `ToolStatusHands` / `ToolStatusMiddleware` 已删除；工具遥测改由 `ToolTelemetry`（`wrap_tool_call`）实现。

### Skills / Subagents 边界

- `/skills/` 映射到 `dsagents/skills/`（两个内置 Skill 包：`philipswgqimport`、`tecanimport`，各含 `SKILL.md` + `references/` + `assets/` 模板 + `scripts/`）；字段/规则只下沉一层 `references/`，模板位于各 Skill 的 `assets/`。
- `philips-wgq-extractor-a/b` 与 `tecan-extractor-a/b` 是创建 DeepAgent 时一次性注册的声明式 SubAgent（`workflow_subagents()`，`dsagents/runtime/agent.py`）；每个只获得对应 extraction 保存工具，内置文件写入被 `_READ_ONLY_FILES`（`FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")`）拒绝；每个 SubAgent 通过 `_extractor(...)` **显式注入** `runtime_middlewares()`（声明式 SubAgent 不继承主 Agent middleware）。
- A/B 并行、C 回查和裁决由 Skill 指令驱动；业务模块不扫描 session、上传历史或最近文件，所有 `generate_*_import` 只消费显式 artifact 路径。
- `execute_run` 按 stream metadata 的 `lc_agent_name` 丢弃 subagent thinking/text token（`observability.is_subagent_message` / `chunk_agent`），只对外暴露主 agent 模型 token；usage 提取在过滤之前完成，故 subagent 模型成本仍计入。
- 锁定的 `deepagents==0.6.12` 没有官方新文档中的 `harness_profile` 构造参数；代码用该版本公开的 profile 注册 API（`register_harness_profile("anthropic", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))`）禁用默认 general-purpose subagent。

环境变量（**仅键名 / 用途，不含值**）：

| 键 | 用途 | 消费者 |
|---|---|---|
| `MINIMAX_MODEL` | 传给 `init_chat_model` 的模型名（`anthropic:` 前缀）；`.env.example` 默认 `MiniMax-M3` | `dsagents/runtime/agent.py` |
| `MINIMAX_API_KEY` | Anthropic 兼容客户端 API key | `dsagents/runtime/agent.py` |
| `MINIMAX_BASE_URL` | Anthropic 兼容端点 base URL（实际可指向 MiniMax） | `dsagents/runtime/agent.py` |

## 3. LangGraph checkpointer / store 持久化边界

`AgentResources.__enter__` 装配（`dsagents/runtime/resources.py`）：

| 组件 | 来源 | DB 路径 | setup |
|---|---|---|---|
| `resources.runs` | `SqliteRunLedger`（标准库 `sqlite3`） | `data/dsagents_runs.db` | `_setup()` 建表（fresh schema，无迁移） |
| `resources.store` | `SqliteStore.from_conn_string(...)`（`langgraph.store.sqlite`） | `data/dsagents_store.db` | `.setup()` |
| `resources.checkpointer` | `SqliteSaver.from_conn_string(...)`（`langgraph.checkpoint.sqlite`） | `data/dsagents_checkpoints.db` | `.setup()` |
| `resources.backend` | `CompositeBackend(default=StateBackend(), routes={...})`（`deepagents.backends`） | 路由到 store / 文件系统 / state | — |
| `RunControl` | `langgraph.runtime.RunControl`（per-run 实例，存于 `HarnessRuntime.run_controls`） | 内存 | 协作 drain 入口 |

`CompositeBackend` 路由策略：

| 虚拟前缀 | backend | 说明 |
|---|---|---|
| `/memories/` | `StoreBackend(store, namespace=lambda _rt: ("dsagents",))` | 显式长期记忆，跨会话持久（SQLite store） |
| `/artifacts/`、`/large_tool_results/` | `FilesystemBackend(root_dir=artifacts_dir.resolve(), virtual_mode=True)`（同一实例） | 落磁盘 |
| `/skills/` | `FilesystemBackend(root_dir=skills_dir.resolve(), virtual_mode=True)` | Skill/参考文档/模板读取；agent 权限禁止写入 |
| 其它（含 `/conversation_history/`、`/logs/`） | `StateBackend()` | 同 `thread_id` 图状态；不进入跨 session store |

LangGraph 调用约定（`dsagents/runtime/execution.py execute_run`）：

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

- payload 只含当前请求里的 `messages[]`，不重放本地 session 历史。
- `thread_id = session_id`。
- `text` block 原样保留；`artifact` block 转成文本路径提示。
- `messages` / `custom` / `updates` 三 channel 全部消费；`updates` 派生 `assistant_message` / `tool_execution`（`_update_events`）。
- `messages` channel 先在 subagent 过滤之前提取 `model_usage`（覆盖主 agent 与 subagent 调用），再只把主 agent 的模型 token 规范化为 `thinking` / `text_delta`；subagent 文本 token 由 `lc_agent_name` 过滤。
- `control=RunControl()`：取消时 `request_cancel(run_id)` 触发 drain，LangGraph 在自身检查点抛 `GraphDrained`，`execute_run` 投影为 `cancelled`。
- run event 查询维度始终是 `run_id`；`thread_id=session_id` 只用于 checkpointer 上下文，不参与 `run_events` 查询。

## 4. 文件上传 / artifacts 集成

| 边界 | 实现 | 证据 |
|---|---|---|
| multipart 解析 | `python-multipart`（依赖）+ FastAPI `UploadFile = File(...)` | `dsagents/api.py` |
| 物理落点 | `<artifacts_dir>/uploads/<cleaned-stem>_<upload-ts>(_n).ext`，`target.parent.mkdir(parents=True, exist_ok=True)` | `dsagents/api.py _store_upload` |
| 文件名清洗 | `clean_filename`：只取 basename、把所有空白归一成普通空格、strip，空则 `"upload"` | `dsagents/integrations/artifacts.py`、`dsagents/api.py` |
| 命名冲突 | `make_timestamped_name(dir, name, batch_timestamp, reserved_names)`：同请求共用一个 batch 时间戳；只在物理路径已存在时追加 `_2`/`_3` | `dsagents/integrations/artifacts.py`、`dsagents/api.py` |
| artifacts 根 | `ResourceConfig.artifacts_dir = data_dir / "artifacts"` | `dsagents/runtime/resources.py` |
| 虚拟路径解析 | `resolve_artifact_path` 把 `/artifacts/...` 解析回物理路径，并拒绝 `..` 越权（`Invalid /artifacts path`）；`to_virtual_artifact_path` 反向生成虚拟路径 | `dsagents/integrations/artifacts.py` |
| 唯一下载名 | `unique_download_path(stem, suffix)` = `downloads/<stem>_<timestamp>(_n).suffix`，`make_unique_name` 保证 exclusive create | `dsagents/integrations/artifacts.py` |
| immutable JSON | `write_json_artifact(stem, payload)` / `read_json_artifact(raw_path)`：写入用 `unique_download_path`，永不覆盖 | `dsagents/integrations/artifacts.py` |

### artifact 目录拆分规则（上传源 vs 解析产物）

`data/artifacts/` 下按写入来源拆成两路子目录，共用 `/artifacts/` 虚拟前缀，由 `resolve_artifact_path` 统一回解析：

| 物理子目录 | 虚拟前缀 | 写入者 | 命名规则 | 证据 |
|---|---|---|---|---|
| `uploads/` | `/artifacts/uploads/` | HTTP `POST /upload`（`dsagents/api.py`） | `<cleaned-stem>_<upload-ts>(_n).ext`，`make_timestamped_name` + 同请求共用时间戳；`clean_filename` 清洗 | `dsagents/api.py`、`dsagents/integrations/artifacts.py` |
| `downloads/` | `/artifacts/downloads/` | MinerU/解压产物；Philips/Tecan extraction、canonical JSON 与 Excel | MinerU 沿用源 stem；业务 JSON/Excel 使用时间戳 + `make_unique_name` / `unique_download_path`，以 exclusive create / 新工作簿保存，不覆盖旧文件 | `dsagents/integrations/mineru.py`、两个 Skill 的 `scripts/tools.py` |

- 上传源只进 `uploads/`，工具产物只进 `downloads/`；两路命名互不污染、互不重名（`make_timestamped_name` vs `make_unique_name`）。
- `resolve_artifact_path` 对 `/artifacts/...` 与绝对路径（`allow_local=True` 时）一视同仁，工具层不关心产物来自上传还是解析。
- `downloads/` 由 `dsagents/integrations/mineru.py` 与 `unique_download_path` 在落盘/解压前 lazy `mkdir(parents=True, exist_ok=True)`；`uploads/` 由 `dsagents/api.py` 同样 lazy mkdir。

## 5. 环境变量集成（python-dotenv）

加载点（导入时 `load_dotenv(...)`）：

- `dsagents/runtime/agent.py`
- `dsagents/integrations/mineru.py`

文档只记录**键名、用途与代码消费者**，不重复本地 `.env` 中的真实值或任何敏感示例。`.env.example` 提供的是示例占位，不应被当成运行时事实来源。

`.env.example` 键清单：

| 键 | backend 代码消费者 | 状态 |
|---|---|---|
| `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` / `MINIMAX_MODEL` | `dsagents/runtime/agent.py` | 已确认 |
| `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` | `dsagents/integrations/mineru.py`（缺失即 `RuntimeError`） | 已确认 |
| `MINERU_EFFORT` | `dsagents/integrations/mineru.py`（`os.getenv(...) or ""`，可省略或留空） | 已确认 |
| `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` | `dsagents/skills/philipswgqimport/scripts/tools.py` | 可选；三者齐备 + client 初始化成功才查询 Philips 单位 |
| `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS` | `dsagents/skills/philipswgqimport/scripts/tools.py` | 可选 thick client 目录与连接/调用超时；默认 30 秒 |

> 交叉引用：`ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` 三者齐备且 `ORACLE_CLIENT_LIB_DIR` 指向有效 instant client 才会发起查询，否则**优雅降级**（跳过法定单位查询，单位字段填「需确认」并返回人工校验项）；`ORACLE_CLIENT_LIB_DIR` 是 thick mode 部署依赖（详见 `CONCERNS.md` §8）。Tecan Skill 不消费任何 Oracle 键。

## 6. 外部 HTTP 调用（requests）

仅 `dsagents/integrations/mineru.py`，对接 MinerU 任务式文档解析接口（`_submit_mineru_task` / `_wait_for_mineru_completion` / `_download_mineru_json` / `_download_mineru_zip`）。`MINERU_BASE_URL` 来自 `.env`，路径用 `urljoin` 在 base 末尾补 `/` 后拼接 `status_url` / `result_url`。

| 调用 | 方法 / URL | 入参 | 说明 |
|---|---|---|---|
| 提交任务 | `POST {MINERU_BASE_URL}/tasks`（multipart `files=[(name,handle,mime)...]`，form `backend/effort/return_md/return_content_list/return_images/return_original_file/response_format_zip`，timeout=`MINERU_TIMEOUT_SECONDS`） | 源文件 + 工具参数 | 默认提交 `return_content_list=true`，其余输出与 `response_format_zip=false`；当 `return_md`、`return_images`、`return_original_file` 或 `response_format_zip` 任一为 true 时，工具把五个输出参数全部规范为 true，返回 ZIP；只接受当前官方响应字段 `task_id/status_url/result_url`（均为字符串）；`effort` 允许空字符串 |
| 轮询状态 | `GET {status_url}`（timeout=`MINERU_TIMEOUT_SECONDS`，默认每 `MINERU_POLL_INTERVAL_SECONDS=30.0` 秒轮询一次；总超时 = `MINERU_TIMEOUT_SECONDS`） | task 级状态 | 只认 `pending/processing/completed/failed`（大小写不敏感）；没有页级进度；`pending/processing` 继续轮询，`failed` 取 `error/message/detail`，未知状态直接报错；超时抛 `TimeoutError` |
| 取结果（JSON） | `GET {result_url}`（timeout=`MINERU_TIMEOUT_SECONDS`） | `response_format_zip=false` | 保存 MinerU 返回的 task 级 JSON 到 `/artifacts/downloads/<stem>.json`，工具返回 `result_path`，不把完整 `content_list` 或 base64 images 放进 tool result |
| 取结果（ZIP） | `GET {result_url}`（timeout=`MINERU_TIMEOUT_SECONDS`，`stream=True` 流式落盘，64KB 块） | `response_format_zip=true` | 保存 task 级二进制 ZIP 到 `/artifacts/downloads/<stem>.zip`，工具返回 `archive_path`；ZIP 内由 MinerU 分离 markdown、content_list、images 与原始文件 |

工具 `parse_documents`：AI 侧看到 `parse_documents(file_paths, return_md=False, return_content_list=True, return_images=False, return_original_file=False, response_format_zip=False)`。默认只要 content_list，保存 task 级 JSON 到 `/artifacts/downloads/<stem>.json` 并返回 `result_path`、`archive_path=None`；用户明确要 Markdown、图片、原始文件或完整下载包时应传五个输出参数全 true，工具保存 ZIP 并返回 `archive_path`、`result_path=None`。单文件命名复用源文件 stem；多文件命名为 `<first-stem>_etc_<batch-ts>.json/.zip`，重名继续用 `make_unique_name`。成功返回结构化 JSON（`task_id/status_url/result_url/archive_path/result_path/result_format/output_options/succeeded[]/failed[]`）；`succeeded[]` 只记录成功提交的源文件 `file_path`；无有效输入时两种路径均为 `None`。
`parse_documents` 的 LangChain 工具 schema 由短 docstring 加 `Annotated` 参数说明组成；调用策略写在参数说明和系统 prompt，不把长操作手册塞进函数 docstring。

工具 `extract_archives(zip_paths: list[str])`：最小本地解压工具，用标准库 `zipfile` 把每个 ZIP 解压到 `/artifacts/downloads/<zip-stem>/`，返回结构与 `parse_documents` 详略对齐的 JSON：

```json
{
  "succeeded": [
    {"archive_path": "/artifacts/downloads/<zip>.zip", "output_dir": "/artifacts/downloads/<zip-stem>/", "files": ["<解压出的相对文件路径>..."]}
  ],
  "failed": [
    {"zip_path": "/artifacts/downloads/<zip>.zip", "error": "<逐 ZIP 错误信息>"}
  ]
}
```

`succeeded[].files[]` 列出该 ZIP 解压出的相对路径；`failed[]` 逐 ZIP 记录错误（键名为 `zip_path`）。不新增通用命令/代码执行工具、不引入依赖、不做历史兼容。

`parse_documents` / `extract_archives` 在 LangGraph 上下文内会通过 `get_stream_writer()` 发 custom `tool_progress` payload（`parse_documents`：`submitted/pending/processing/completed/failed`，附批量 `file_paths`、必要 `archive_path` 或 `result_path` 与 `succeeded_count/failed_count`；`extract_archives`：`completed` + `zip_paths` + 计数），脱离 LangGraph 独立调用时静默跳过这些进度事件。`ToolTelemetry`（`dsagents/runtime/agent.py`）的 `wrap_tool_call` 另发每个工具调用的 `tool_execution`（`started/completed/error` + 计时 + scope），与上述 parse_documents/extract_archives 自发的进度事件是两套独立 custom payload。

`default_tool_catalog()` 当前静态注册 6 个工具：`parse_documents`、`extract_archives`，以及 Philips/Tecan 各 2 个（extraction 保存 + 一站式生成）。

### 业务工具契约（4 个业务工具）

| 工具名 | 所属模块 | 关键入参 | 返回 |
|---|---|---|---|
| `save_philips_wgq_extraction` | `dsagents/skills/philipswgqimport/scripts/tools.py` | `extractor`, `source_artifact`, `logistics`, `items` | `{extractor, artifact_path}` |
| `generate_philips_wgq_import` | `dsagents/skills/philipswgqimport/scripts/tools.py` | `extraction_artifacts`, `tracking_artifact`, `international_forwarder?`, `customs_mode?`, `decisions` | 成功 `{"status":"generated","canonical_artifact","artifacts","manual_checks"}`；问题 `{"code":"input_problems","problems":[{source,location,issue,action}]}` |
| `save_tecan_extraction` | `dsagents/skills/tecanimport/scripts/tools.py` | `extractor`, `source_artifact`, `logistics`, `items` | `{extractor, artifact_path}` |
| `generate_tecan_import` | `dsagents/skills/tecanimport/scripts/tools.py` | `extraction_artifacts`, `decisions`（join 订单 + 信息工作簿） | 成功 `{"status":"generated",...}`；问题 `{"code":"input_problems",...}` |

> `?` 表示可选参数。两个 `generate_*_import` 都是一次性 canonical 构建 + 匹配 + 计算 + 模板写入 + 输出复核；业务问题统一返回 `input_problems` 并结束 run，不再有 `build_*_canonical` / `save_*_adjudication` / `generate_*_documents` / `needs_input` / `needs_c` / `needs_adjudication` 状态机，也不再有 `info_source_preference` / `pn_info_source_overrides`（Tecan 来源冲突一律作为 `input_problems`）。

## 7. Oracle 与业务工作簿边界

- `openpyxl` 读取 Philips tracking、Tecan 订单/信息表，并从 `dsagents/skills/<skill>/assets/` 下固定模板生成最终工作簿（Philips：`invoice,packing进境.xlsx`、`核注清单导入模板.xlsx`；Tecan：`Tecan_进口_发票箱单_空运.xlsx`）；上传原件和模板均不被编辑。
- 共享 openpyxl helper 在各 Skill 的 `scripts/documents.py`（Philips：`generate_tracking` / `generate_invoice_packing` / `generate_bonded_checklist` + `header_columns` / `copy_sheet_row` 等；Tecan：`generate_invoice_packing` + `insert_rows`）。
- Philips 单位查询通过 `oracledb.connect(...)`（thick mode，运行时 `import oracledb`）建立，SQL 只按明确料号候选读取三个单位字段。配置缺失、client 未初始化或查询异常时继续生成，结果使用「需确认」值并返回人工校验项。
- Tecan 不调用 Oracle；订单和信息表按表头/内容识别，信息来源冲突直接返回 `input_problems`，后续 run 必须重新显式传原路径。
- 两个 `generate_*_import` 都只在瞬时返回值中报告 `input_problems`，不增加数据库、恢复状态或 artifact registry。
