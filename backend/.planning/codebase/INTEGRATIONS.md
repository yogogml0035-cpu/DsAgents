# INTEGRATIONS

> 外部集成与依赖边界。事实基于当前代码核对，区分「已确认」与「需确认」。
> 本轮刷新（2026-07-10）已核对当前工作树：HTTP/run ledger 表面未变；新增 DeepAgents Skills/Subagents、Philips/Tecan artifact 工具链、Excel 模板与可选 Oracle 查询。

## 1. HTTP 框架（FastAPI + uvicorn）

入口模块：`api.py`。`create_app(*, resource_config: ResourceConfig | None = None, harness_factory: Callable[[AgentResources], HarnessRuntime] = create_harness)` 返回 `FastAPI(lifespan=lifespan)`，模块底部 `app = create_app()`，预期由 `uvicorn api:app` 拉起（uvicorn 作为依赖声明存在，但 `api.py` 未直接 import；测试用 `harness_factory` 注入 `FakeBrainFactory`）。

### 端点契约

| 方法 / 路径 | 入参 | 行为 | 返回 |
|---|---|---|---|
| `POST /runs` | `{"session_id": str\|null, "messages": [{"role": str, "content": [{"type":"text","text":str} \| {"type":"artifact","path":str}]}...]}`（`RunRequest`） | `session_id` 为空生成 `uuid4().hex`；`run_id = uuid4().hex`；获取单飞锁 → 写 ledger → 起 daemon 线程执行 | `200 {"run_id","session_id","status":"queued"}`；校验失败 `422`；冲突 `409 {"error":"该会话正在运行","active_run_id"}` |
| `GET /runs/{run_id}` | query `after_event_id: int\|null` | 读 run 快照 + run events（支持增量游标）+ 当前 run 全局最新非 `status`/非 `model_usage` 事件 + 该 run 全部 `model_usage` 汇总出的 `usage` | `200 {"run":{...},"events":[...],"latest_content_event":{...}\|null,"usage":{...}\|null}`；未知 run `404 {"error":"Unknown run: ..."}`。`usage` 含 `model_calls`、四类 token 总量、`cache_hit_rate`（无输入为 `null`）、`estimated_cost_cny` / `estimated_savings_cny`（按调用 input ≤/>512k 分 tier 后汇总；MiniMax-M3 standard 定价，`pricing_as_of` 标注日期）、估算说明、`by_agent` 分项；模型不可计价时金额为 `null`，token 仍完整 |
| `POST /upload` | multipart `files: UploadFile[]`（字段名固定 `files`，支持 1 个或多个） | 落到 `<artifacts_dir>/uploads/<cleaned-stem>_<upload-ts>(_n).ext`；同一请求共用一个上传时间戳，只有真实物理重名时才追加 `_2`、`_3`；`name` 继续返回清洗后的原始文件名 | `200 {"files":[{"file_path":"/artifacts/uploads/...","name":"<原名>","mime_type":"<mime-or-application/octet-stream>","size":123}]}` |

> 注：当前**无 SSE / `StreamingResponse` / `text/event-stream`**，事件获取靠轮询 `GET /runs/{run_id}?after_event_id=...`。
> 注：`after_event_id` **只影响 `events[]`**；`latest_content_event` 始终返回该 run 当前最新的非 `status` 事件，没有则为 `null`。
> 注：`POST /runs` **不再支持**旧 `{"message":"..."}` 请求体。
> 注：当前**未注册 `CORSMiddleware`**，也没有 CORS 配置消费者。
> 注：run / event 响应里的时间字段当前统一为本机时区秒级文本 `YYYY-MM-DD HH:mm:ss`；旧 UTC 时间在首次迁移时会被平移到本机时区。

### artifact block 与上传能力

- `artifact` block 是**项目 API 语义**，不是直接发给 LangChain 的标准多模态 block。
- `HarnessRuntime.execute_run(...)` 会把 `artifact` block 转成文本提示：`Uploaded artifact: /artifacts/uploads/...`，再把归一化后的 `messages[]` 发给 Brain。
- 常见办公文件和任意图片都可以通过 `POST /upload` 保存；能否被解析或理解取决于 DeepAgents `read_file`、`parse_documents`、MinerU 和模型多模态能力。

### lifespan

启动：装配 `AgentResources`、`fail_incomplete_runs("执行已中断，请重试")`、构建 harness、初始化锁注册表。
停止：`resources.__exit__` 关闭 SQLite 连接上下文。

## 2. LLM Provider 集成边界

| 边界 | 实现 | 证据 |
|---|---|---|
| 生产 brain | `DeepAgentsBrainFactory`：`init_chat_model("anthropic:<MODEL>", ...)` → `ChatAnthropic`；`create_deep_agent(...)` 同时注入 `skills=["/skills/"]`、四个 subagents、`/skills/**` 写禁令与主 agent 名 | `harness.py` |
| 本地测试 brain | `FakeBrain` / `FakeBrainFactory`（模拟 v2 stream chunk，不触达真实 provider） | `backend/tests/test_support.py` |
| 系统 prompt | `DEFAULT_SYSTEM_PROMPT` 引导文件工具，并明确只有用户清晰要求业务结果时才使用业务 Skill；普通 PDF 请求不触发业务流程 | `harness.py` |
| prompt-cache 中间件 | **不新增自定义 cache middleware**。`create_deep_agent` 已在尾栈自动挂 `AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore")`（`deepagents/graph.py`），给 system 末块与末个 tool 打 `cache_control={"type":"ephemeral","ttl":"5m"}`；因为 MiniMax 走 `ChatAnthropic`，该中间件对 MiniMax-M3 生效。固定前缀 = `DEFAULT_SYSTEM_PROMPT` + `default_tool_catalog()` tool schema + SDK 默认 deep-agent prompt，**不要**向其注入时间/run_id 等动态内容 | `harness.py` + `langchain_anthropic/middleware/prompt_caching.py`（库源） |
| usage 观测出口 | usage 不实现 Agent middleware，而是复用 `harness.execute_run` 的统一 `messages` 流出口：在 subagent 文本过滤之前从终态 `AIMessageChunk.usage_metadata` 提取，每个模型调用仅在非空时写一次 `model_usage` 事件（含 subagent 调用）；不写入 AgentState/checkpointer/store，不新增表 | `harness.py` `_model_usage`/`_chunk_agent` + `run_ledger.py` `aggregate_model_usage` + `api.py` `_usage_summary` |

### Skills / Subagents 边界

- `/skills/` 映射到 `backend/skills/`；两个 `SKILL.md` 均少于 100 行，字段/规则只下沉一层 `references/`，模板位于各 Skill 的 `assets/`。
- `philips-wgq-extractor-a/b` 与 `tecan-extractor-a/b` 是创建 DeepAgent 时一次性注册的临时 subagent；每个只获得对应 extraction 保存工具，内置文件写入被拒绝，并用 `ToolStrategy(ExtractionReference)` 返回 `{extractor, artifact_path}`。
- A/B 并行、C 回查和裁决由 Skill 指令驱动；业务模块不扫描 session、上传历史或最近文件，所有 builder/generator 只消费显式 artifact 路径。
- `HarnessRuntime` 仍保留 task 工具调用/结果事件，但按 stream metadata 的 `lc_agent_name` 丢弃 subagent thinking/text token，只对外暴露主 agent 模型 token。
- 当前锁定 `deepagents==0.6.12` 没有官方新文档中的 `harness_profile` 参数；代码使用该版本公开的 provider profile 注册 API 禁用默认 general-purpose subagent。

环境变量（**仅键名 / 用途，不含值**）：

| 键 | 用途 | 消费者 |
|---|---|---|
| `MINIMAX_MODEL` | 传给 `init_chat_model` 的模型名（`anthropic:` 前缀） | `harness.py` |
| `MINIMAX_API_KEY` | Anthropic 兼容客户端 API key | `harness.py` |
| `MINIMAX_BASE_URL` | Anthropic 兼容端点 base URL（实际可指向 MiniMax） | `harness.py` |

## 3. LangGraph checkpointer / store 持久化边界

`AgentResources.__enter__` 装配（`resources.py`）：

| 组件 | 来源 | DB 路径 | setup |
|---|---|---|---|
| `resources.checkpointer` | `SqliteSaver.from_conn_string(...)`（`langgraph.checkpoint.sqlite`） | `data/dsagents_checkpoints.db` | `.setup()` |
| `resources.store` | `SqliteStore.from_conn_string(...)`（`langgraph.store.sqlite`） | `data/dsagents_store.db` | `.setup()` |
| `resources.runs` | `SqliteRunLedger`（标准库 `sqlite3`） | `data/dsagents_runs.db` | `_setup()` 建表 |
| `resources.backend` | `CompositeBackend(default=StateBackend(), routes={...})`（`deepagents.backends`） | 路由到 store / 文件系统 / state | — |

LangGraph 调用约定（`harness.py`）：

```python
brain.stream(
    {"messages": normalized_messages},
    config={"configurable": {"thread_id": session_id}},
    stream_mode=["messages", "custom", "values"],
    version="v2",
)
```

- payload 只含当前请求里的 `messages[]`，不重放本地 session 历史
- `thread_id = session_id`
- `text` block 原样保留；`artifact` block 转成文本路径提示
- `messages` / `custom` / `values` 三 channel 全部消费，其中 `values` 只保留 raw snapshot，并派生 `tool_call` / `tool_result` / `assistant_message`；最终 AIMessage 同时含 `thinking` 与 `text` block 时，`assistant_message.payload` 会带上最后一个 `thinking` 文本和最终 `text`
- `messages` channel 先在 subagent 过滤之前提取 `model_usage`（覆盖主 agent 与 subagent 调用），再只把主 agent 的模型 token 规范化为 `thinking` / `text_delta`；subagent 文本 token 由 `lc_agent_name` 过滤
- `values` 不是公开 run event type；外部调用方应消费八类规范化事件（七类业务事件 + `model_usage` 观测事件），完整 snapshot 仅保留在事件 `raw`
- raw 完整 v2 chunk 整体落库（`run_events.raw_*`），不只是 `chunk["data"]`
- run event 查询维度始终是 `run_id`；`thread_id=session_id` 只用于 checkpointer 上下文，不参与 `run_events` 查询

## 4. 文件上传 / artifacts 集成

| 边界 | 实现 | 证据 |
|---|---|---|
| multipart 解析 | `python-multipart`（依赖）+ FastAPI `UploadFile = File(...)` | `api.py` |
| 物理落点 | `<artifacts_dir>/uploads/<cleaned-stem>_<upload-ts>(_n).ext`，`mkdir(parents=True, exist_ok=True)` | `api.py` |
| 虚拟路径 | `/artifacts/uploads/<name>`（`_virtual_upload_path`） | `api.py` |
| 返回元数据 | `name` / `mime_type` / `size` / `file_path` | `api.py` |
| 文件名清洗 | `artifact_names.clean_filename`：只取 basename、把所有空白归一成普通空格、strip，空则 `"upload"` | `artifact_names.py`、`api.py` |
| artifacts 根 | `ResourceConfig.artifacts_dir = data_dir / "artifacts"` | `resources.py` |

`/artifacts/` 虚拟路径在工具层也支持：`tools._resolve_document_path` 把 `/artifacts/...` 解析回物理路径，并拒绝 `..` 越权（`Invalid /artifacts path`）。

`CompositeBackend` 路由策略：

| 虚拟前缀 | backend | 说明 |
|---|---|---|
| `/memories/` | `StoreBackend(store, namespace=("dsagents",))` | 显式长期记忆，跨会话持久（SQLite store） |
| `/artifacts/`、`/large_tool_results/` | `FilesystemBackend(root_dir=artifacts_dir, virtual_mode=True)` | 落磁盘 |
| `/skills/` | `FilesystemBackend(root_dir=backend/skills, virtual_mode=True)` | Skill/参考文档/模板读取；agent 权限禁止写入 |
| 其它（含 `/conversation_history/`、`/logs/`） | `StateBackend()` | 同 `thread_id` 图状态；不进入跨 session store |

### artifact 目录拆分规则（上传源 vs 解析产物）

`data/artifacts/` 下按写入来源拆成两路子目录，共用 `/artifacts/` 虚拟前缀，由 `_resolve_document_path` 统一回解析：

| 物理子目录 | 虚拟前缀 | 写入者 | 命名规则 | 证据 |
|---|---|---|---|---|
| `uploads/` | `/artifacts/uploads/` | HTTP `POST /upload`（`api.py`） | `<cleaned-stem>_<upload-ts>(_n).ext`，`make_timestamped_name` + 同请求共用时间戳；`clean_filename` 清洗 | `api.py`、`artifact_names.py` |
| `downloads/` | `/artifacts/downloads/` | MinerU/解压产物；Philips/Tecan extraction、adjudication、canonical JSON 与 Excel | MinerU 沿用源 stem；业务 JSON/Excel 使用时间戳 + `make_unique_name`，以 exclusive create/新工作簿保存，不覆盖旧文件 | `tools.py`、`workflow_artifacts.py`、两个业务模块 |

- 上传源只进 `uploads/`，工具产物只进 `downloads/`；两路命名互不污染、互不重名（`make_timestamped_name` vs `make_unique_name`）。
- `_resolve_document_path` 对 `/artifacts/...` 与绝对路径一视同仁，工具层不关心产物来自上传还是解析。
- `downloads/` 由 `tools.py` 在落盘/解压前 lazy `mkdir(parents=True, exist_ok=True)`；`uploads/` 由 `api.py` 同样 lazy mkdir。

## 5. 环境变量集成（python-dotenv）

加载点（导入时 `load_dotenv(Path(__file__).with_name(".env"))`）：

- `harness.py`
- `tools.py`

文档只记录**键名、用途与代码消费者**，不重复本地 `.env` 中的真实值或任何敏感示例。`.env.example` 提供的是示例占位，不应被当成运行时事实来源。

`.env.example` 键清单：

| 键 | backend 代码消费者 | 状态 |
|---|---|---|
| `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` / `MINIMAX_MODEL` | `harness.py` | 已确认 |
| `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_EFFORT` / `MINERU_TIMEOUT_SECONDS` | `tools.py` | 已确认（其中 `MINERU_EFFORT` 可省略或留空） |
| `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` | `philips_wgq_import.py` | 可选；三者齐备才查询 Philips 单位 |
| `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS` | `philips_wgq_import.py` | 可选 thick client 目录与连接/调用超时；默认 30 秒 |

> 交叉引用：`ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` 三者齐备才会发起查询，否则**优雅降级**（跳过法定单位查询，单位字段填“需确认”并返回人工校验项）；`ORACLE_CLIENT_LIB_DIR` 指向 Oracle instant client，是 thick mode 部署依赖（详见 `CONCERNS.md`）。Tecan 不消费任何 Oracle 键。

## 6. 外部 HTTP 调用（requests）

仅 `tools.py`，对接 MinerU 3.4.0 任务式文档解析接口（`_submit_mineru_task` / `_wait_for_mineru_completion` / `_download_mineru_json` / `_download_mineru_zip`）：

| 调用 | 方法 / URL | 入参 | 说明 |
|---|---|---|---|
| 提交任务 | `POST {MINERU_BASE_URL}/tasks`（multipart `files=[...]`，form `backend/effort/return_md/return_content_list/return_images/return_original_file/response_format_zip`，timeout=`MINERU_TIMEOUT_SECONDS`） | 源文件 + 工具参数 | 默认提交 `return_content_list=true`，其余输出与 `response_format_zip=false`；当 `return_md`、`return_images`、`return_original_file` 或 `response_format_zip` 任一为 true 时，工具会把五个输出参数全部规范为 true，返回 ZIP；只接受当前官方响应字段 `task_id/status_url/result_url`；`effort` 允许空字符串 |
| 轮询状态 | `GET {status_url}`（timeout=`MINERU_TIMEOUT_SECONDS`，默认每 30 秒轮询一次） | task 级状态 | 只认 `pending/processing/completed/failed`；没有页级进度；`pending/processing` 继续轮询，未知状态直接报错 |
| 取结果（JSON） | `GET {result_url}`（timeout=`MINERU_TIMEOUT_SECONDS`） | `response_format_zip=false` | 保存 MinerU 返回的 task 级 JSON 到 `/artifacts/downloads/<stem>.json`，工具返回 `result_path`，不把完整 `content_list` 或 base64 images 放进 tool result |
| 取结果（ZIP） | `GET {result_url}`（timeout=`MINERU_TIMEOUT_SECONDS`，`stream=True` 流式落盘） | `response_format_zip=true` | 保存 task 级二进制 ZIP 到 `/artifacts/downloads/<stem>.zip`，工具返回 `archive_path`；ZIP 内由 MinerU 分离 markdown、content_list、images 与原始文件 |

工具 `parse_documents`：AI 侧看到 `parse_documents(file_paths, return_md=False, return_content_list=True, return_images=False, return_original_file=False, response_format_zip=False)`。默认只要 content_list，保存 task 级 JSON 到 `/artifacts/downloads/<stem>.json` 并返回 `result_path`、`archive_path=None`；用户明确要 Markdown、图片、原始文件或完整下载包时应传五个输出参数全 true，工具保存 ZIP 并返回 `archive_path`、`result_path=None`。单文件命名复用源文件 stem（如 `report_20260708000000.pdf` → `report_20260708000000.json/.zip`），多文件命名为 `<first-stem>_etc_<batch-ts>.json/.zip`，重名继续用 `make_unique_name`。成功返回结构化 JSON（`task_id/status_url/result_url/archive_path/result_path/result_format/output_options/succeeded[]/failed[]`）；`succeeded[]` 只记录成功提交的源文件 `file_path`，不再伪造每文件输出路径；无有效输入时两种路径均为 `None`。
`parse_documents` 的 LangChain 工具 schema 由短 docstring 加 `Annotated` 参数说明组成；调用策略写在参数说明和系统 prompt，不把长操作手册塞进函数 docstring。

工具 `extract_archives(zip_paths: list[str])`：最小本地解压工具，用标准库 `zipfile` 把每个 ZIP 解压到 `/artifacts/downloads/<zip-stem>/`，返回结构与 `parse_documents` 详略对齐的 JSON：

```json
{
  "succeeded": [
    {"archive_path": "/artifacts/downloads/<zip>.zip", "output_dir": "/artifacts/downloads/<zip-stem>/", "files": ["<解压出的相对文件路径>..."]}
  ],
  "failed": [
    {"archive_path": "/artifacts/downloads/<zip>.zip", "error": "<逐 ZIP 错误信息>"}
  ]
}
```

`succeeded[].files[]` 列出该 ZIP 解压出的相对路径；`failed[]` 逐 ZIP 记录错误。不新增通用命令/代码执行工具、不引入依赖、不做历史兼容。

`parse_documents` 在 LangGraph 上下文内会通过 `get_stream_writer()` 发 custom `tool_status` payload：`submitted/pending/processing/completed/failed`，附批量 `file_paths`、必要 `archive_path` 或 `result_path` 与 `succeeded_count/failed_count`；脱离 LangGraph 独立调用时静默跳过这些进度事件。

`default_tool_catalog()` 当前注册十个工具：`parse_documents`、`extract_archives`，以及 Philips/Tecan 各四个 `save extraction` / `build canonical` / `save adjudication` / `generate documents` 工具。生成器唯一业务参数均为 canonical artifact 路径。

### 业务工具契约（8 个业务工具）

| 工具名 | 所属模块 | 关键入参 | 返回 |
|---|---|---|---|
| `save_philips_wgq_extraction` | `philips_wgq_import.py` | `extractor`, `source_artifact`, `logistics`, `items` | `{extractor, artifact_path}` |
| `save_philips_wgq_adjudication` | `philips_wgq_import.py` | `source_artifacts`, `decisions` | `{artifact_path}` |
| `build_philips_wgq_canonical` | `philips_wgq_import.py` | `extraction_artifacts`, `tracking_artifact`, `international_forwarder?`, `customs_mode?`, `adjudication_artifact?` | `{status, ...}` 状态机：`canonical` / `needs_c` / `needs_adjudication` / `needs_input` |
| `generate_philips_wgq_documents` | `philips_wgq_import.py` | `canonical_artifact` | 生成的 Excel artifact 路径 |
| `save_tecan_extraction` | `tecan_import.py` | `extractor`, `source_artifact`, `logistics`, `items`（强制 `[]`） | `{extractor, artifact_path}` |
| `save_tecan_adjudication` | `tecan_import.py` | `source_artifacts`, `decisions` | `{artifact_path}` |
| `build_tecan_canonical` | `tecan_import.py` | `extraction_artifacts`, `order_artifact`, `information_artifacts`, `info_source_preference?`, `pn_info_source_overrides?`, `adjudication_artifact?` | `{status, ...}` 同上状态机 |
| `generate_tecan_documents` | `tecan_import.py` | `canonical_artifact` | 生成的 Excel artifact 路径 |

> `?` 表示可选参数。两个 canonical builder 的 `status` 共用同一套状态机；canonical artifact 只在 `status=="canonical"` 时用于 generator。

## 7. Oracle 与业务工作簿边界

- `openpyxl` 读取 Philips tracking、Tecan 订单/信息表，并从三个已校验模板生成最终工作簿；上传原件和模板均不被编辑。
- Philips 单位查询通过 `oracledb.connect(...)` 延迟建立，SQL 只按明确料号候选读取三个单位字段。配置缺失、未命中或查询异常时继续生成，结果使用“需确认”值并返回人工校验项。
- Tecan 不调用 Oracle；订单和信息表按表头/内容识别，信息来源冲突返回正常 `needs_input` 结果，后续 run 必须重新显式传原路径与选择。
- 两个 canonical builder 都只在瞬时返回值中报告 `needs_c` / `needs_adjudication` / `needs_input`，不增加数据库、恢复状态或 artifact registry。
