# INTEGRATIONS

> 外部集成与依赖边界。事实基于当前代码核对，区分「已确认」与「需确认」。

## 1. HTTP 框架（FastAPI + uvicorn）

入口模块：`api.py`。`create_app()` 返回 `FastAPI(lifespan=lifespan)`，模块底部 `app = create_app()`，预期由 `uvicorn api:app` 拉起（uvicorn 作为依赖声明存在，但 `api.py` 未直接 import）。

### 端点契约

| 方法 / 路径 | 入参 | 行为 | 返回 |
|---|---|---|---|
| `POST /runs` | `{"message": str, "session_id": str\|null}`（`RunRequest`） | `session_id` 为空生成 `uuid4().hex`；`run_id = uuid4().hex`；获取单飞锁 → 写 ledger → 起 daemon 线程执行 | `200 {"run_id","session_id","status":"queued"}`；冲突 `409 {"error":"该会话正在运行","active_run_id"}` |
| `GET /runs/{run_id}` | query `after_event_id: int\|null` | 读 run 快照 + run events（支持增量游标） | `200 {"run":{...},"events":[...]}`；未知 run `404 {"error":"Unknown run: ..."}` |
| `POST /files` | multipart `file: UploadFile` | 落到 `<artifacts_dir>/uploads/<uuid>_<cleaned_name>`；返回虚拟路径 | `200 {"file_path":"/artifacts/uploads/..."}` |

> 注：当前**无 SSE / `StreamingResponse` / `text/event-stream`**，事件获取靠轮询 `GET /runs/{run_id}?after_event_id=...`。
> 注：当前**未注册 `CORSMiddleware`**（代码无；`.env.example` 的 `CORS_ORIGINS` 无消费者 → 需确认）。

### lifespan

启动：装配 `AgentResources`、`fail_incomplete_runs("执行已中断，请重试")`、构建 harness、初始化锁注册表。
停止：`resources.__exit__` 关闭 SQLite 连接上下文。

## 2. LLM Provider 集成边界

| 边界 | 实现 | 证据 |
|---|---|---|
| 生产 brain | `DeepAgentsBrainFactory`：`init_chat_model("anthropic:<MODEL>", api_key=<KEY>, base_url=<URL>, thinking={"type":"adaptive"})` → `ChatAnthropic`；`create_deep_agent(model=..., tools=..., system_prompt=..., middleware=..., backend=..., checkpointer=..., store=...)` | `harness.py` |
| 自检 brain | `_FakeBrain` / `_FakeBrainFactory`（模拟 v2 stream chunk，不触达真实 provider） | `self_check.py` |
| 系统 prompt | `DEFAULT_SYSTEM_PROMPT`（文档处理 agent，引导调用 `parse_document`，写入 `/memories/`、`/artifacts/`） | `harness.py` |

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
| `resources.backend` | `CompositeBackend(default=StateBackend(), routes={...})`（`deepagents.backends`） | 路由到 store / 文件系统 | — |

LangGraph 调用约定（`harness.py`）：

```python
brain.stream(
    {"messages": [{"role": "user", "content": message}]},
    config={"configurable": {"thread_id": session_id}},
    stream_mode=["messages", "custom", "values"],
    version="v2",
)
```

- payload 只含当前 user message（多轮记忆依赖 checkpointer/store，不在 payload 重放）
- `thread_id = session_id`
- `messages` / `custom` / `values` 三 channel 全部消费
- raw 完整 v2 chunk 整体落库（`run_events.raw_*`），不只是 `chunk["data"]`

## 4. 文件上传 / artifacts 集成

| 边界 | 实现 | 证据 |
|---|---|---|
| multipart 解析 | `python-multipart`（依赖）+ FastAPI `UploadFile = File(...)` | `api.py` |
| 物理落点 | `<artifacts_dir>/uploads/<uuid>_<cleaned_name>`，`mkdir(parents=True, exist_ok=True)` | `api.py` |
| 虚拟路径 | `/artifacts/uploads/<name>`（`_virtual_upload_path`） | `api.py` |
| 文件名清洗 | `_clean_filename`：去路径、strip，空则 `"upload"` | `api.py` |
| artifacts 根 | `ResourceConfig.artifacts_dir = data_dir / "artifacts"` | `resources.py` |

`/artifacts/` 虚拟路径在工具层也支持：`tools._resolve_document_path` 把 `/artifacts/...` 解析回物理路径，并拒绝 `..` 越权（`Invalid /artifacts path`）。

`CompositeBackend` 路由策略：

| 虚拟前缀 | backend | 说明 |
|---|---|---|
| `/memories/`、`/conversation_history/`、`/logs/` | `StoreBackend(store, namespace=("dsagents",))` | 跨会话持久（SQLite store） |
| `/artifacts/`、`/large_tool_results/` | `FilesystemBackend(root_dir=artifacts_dir, virtual_mode=True)` | 落磁盘 |
| 默认 | `StateBackend()` | 进程内 state |

## 5. 环境变量集成（python-dotenv）

加载点（导入时 `load_dotenv(Path(__file__).with_name(".env"))`）：

- `harness.py`
- `tools.py`

`.env.example` 键清单（**仅键名 / 默认占位**）：

| 键 | 默认占位（来自 `.env.example`） | backend 代码消费者 | 状态 |
|---|---|---|---|
| `MINIMAX_API_KEY` | （空） | `harness.py` | 已确认 |
| `MINIMAX_BASE_URL` | `https://api.minimaxi.com/anthropic` | `harness.py` | 已确认 |
| `MINIMAX_MODEL` | `MiniMax-M3` | `harness.py` | 已确认 |
| `MINERU_BASE_URL` | `http://10.11.0.110:6006` | `tools.py` | 已确认 |
| `MINERU_BACKEND` | `hybrid-engine` | `tools.py` | 已确认 |
| `MINERU_EFFORT` | `high` | `tools.py` | 已确认 |
| `MINERU_TIMEOUT_SECONDS` | `900` | `tools.py` | 已确认 |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | — | 无 | 需确认（代码无引用） |
| `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS` | — | 无 | 需确认（代码无引用，见 §7） |
| `LANGSMITH_TRACING` / `LANGSMITH_ENDPOINT` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | — | 无 | 需确认（代码无引用） |
| `CORS_ORIGINS` | `http://localhost:8500,...` | 无 | 需确认（未注册 CORS middleware） |

## 6. 外部 HTTP 调用（requests）

仅 `tools.py`，对接 MinerU 文档解析服务（`_submit_mineru_task` / `_wait_for_mineru_result`）：

| 调用 | 方法 / URL | 入参 | 说明 |
|---|---|---|---|
| 提交任务 | `POST {MINERU_BASE_URL}/tasks`（multipart `files=[...]`，form `backend/effort/return_md/response_format_zip`，timeout=60） | 源文件 + 配置 | 从响应递归找 `task_id/taskId/id` |
| 轮询状态 | `GET {MINERU_BASE_URL}/tasks/{task_id}`（timeout=30，默认 2s 轮询） | task_id | 命中 `FAILURE_STATES` 抛错；命中 `SUCCESS_STATES` 取结果 |
| 取结果 | `GET {MINERU_BASE_URL}/tasks/{task_id}/result`（timeout=120） | task_id | 从响应找 `md/markdown/md_content/markdown_content` |

工具 `parse_document`：解析本地文件 → 写 markdown 到 `data/document_outputs/<stem>.md`（或指定 `output_path`）→ 返回 JSON（`task_id/source/output_path/markdown_bytes`）。

`default_tool_catalog()` 当前只注册一个工具：`parse_document`。

## 7. instantclient/ 定位（需确认）

`backend/instantclient/` 内含 Oracle Instant Client 19.31 for Windows 二进制（`instantclient_19_31/`，含 `oci.dll`、`ojdbc8.jar`、`oraocci19.dll`、`orasql19.dll` 等，及 `BASIC_LITE_LICENSE`）。

| 事实 | 状态 |
|---|---|
| backend Python 代码**无** `oracledb` / `cx_Oracle` / `oracle` import | 已确认 |
| backend Python 代码**不读取** `ORACLE_*` / `ORACLE_CLIENT_LIB_DIR` | 已确认 |
| `.env.example` 含 `ORACLE_*` 键 | 已确认 |

结论：当前 instantclient 与 backend 运行时**无代码层集成**，疑似遗留资产或计划中能力 → **需确认**用途与是否应保留。
