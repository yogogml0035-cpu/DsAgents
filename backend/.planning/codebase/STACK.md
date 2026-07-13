# STACK

> 技术栈快照。所有事实均基于当前代码（`pyproject.toml` + `uv.lock` + `backend/` 顶层源码）核对。
> 本轮刷新（2026-07-13）已逐文件核对当前工作树：依赖与锁定版本、`backend/` 顶层源码布局、run-first 架构、DeepAgents 装配边界、usage/pricing 层均与代码一致。
> 本文件优先记录代码与仓库配置可直接证实的事实；运行命令以仓库内 `scripts/start-backend.bat` 与测试默认值为准。

## 1. 运行时 / 语言

| 项 | 值 | 证据 |
|---|---|---|
| 语言 | Python | `pyproject.toml` |
| 版本约束 | `>=3.11,<4.0` | `requires-python` |
| 包管理器 | `uv`（非 pip） | `backend/uv.lock`、`scripts/start-backend.bat` 用 `uv run` |
| 构建 backend | `setuptools>=68`（`setuptools.build_meta`） | `[build-system]` |
| 包名 / 版本 | `dsagents` / `0.1.0` | `[project]` |

## 2. 构建方式（发行名 `dsagents`，源码顶层为 `api.py` + `runtime/` / `integrations/` / `skills/`）

`pyproject.toml` 配置：

```toml
[tool.setuptools]
package-dir = {"" = "."}

[tool.setuptools.packages.find]
where = ["."]
include = ["runtime*", "integrations*", "skills*"]

[tool.setuptools.package-data]
"skills.philipswgqimport" = ["SKILL.md", "references/*.md", "assets/*"]
"skills.tecanimport" = ["SKILL.md", "references/*.md", "assets/*"]
```

- `backend/` 作为安装根，发行名仍为 `dsagents`，但源码顶层为 `api.py` 与 `runtime/`、`integrations/`、`skills/`（以及两个内置 Skill 包 `philipswgqimport` / `tecanimport`）。
- 模块内部使用**绝对顶层导入**（如 `from runtime import AgentResources, create_harness`、`from integrations.artifacts import resolve_artifact_path`、`from skills.philipswgqimport.scripts.tools import generate_philips_wgq_import`），安装后同样可用。
- `package-dir = {"" = "."}` 把仓库 `backend/` 目录映射为导入根。
- `package-data` 确保两个内置 Skill 的 `SKILL.md` / `references/*.md` / `assets/*`（模板）随 wheel 一起打包。
- 当前显式保留 `py-modules = ["api"]` 打包顶层入口模块；旧的扁平多模块列表已删除，旧辅助模块（`harness`/`hands`/`resources`/`run_ledger`/`tools`/`subagents`/`workflow_artifacts`/`artifact_names`/`philips_wgq_import`/`tecan_import`）已全部删除。

### 模块清单（顶层源码）

| 模块 | 职责 |
|---|---|
| `api.py` | FastAPI run-first HTTP 层（`POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`）；`_usage_summary` 价格估算 |
| `runtime/__init__.py` | 对外稳定入口：`AgentResources` / `create_harness` / `RunLedger` |
| `runtime/agent.py` | `Brain`/`BrainFactory` Protocol、`DeepAgentsBrainFactory`、`workflow_subagents()`（4 个声明式 extractor，各自装 middleware）、`ToolTelemetry`（`wrap_tool_call`）、`NoProgressMiddleware`（`before_model`）、`NoProgressLoop` |
| `runtime/execution.py` | `HarnessRuntime.execute_run`（stream chunk → RunEvent）、`ARTIFACT_REFERENCE_HINT`、`create_harness`、`_update_events`、`_normalize_messages`；`RunControl` 协作 drain，`GraphDrained` → `cancelled` |
| `runtime/observability.py` | 纯内容/元数据提取器：`model_usage`、`thinking_delta`、`message_delta`、`assistant_message_payload`、`tool_call_payload`、`chunk_agent`、`is_subagent_message`、`MAIN_AGENT_NAME`、`MAIN_AGENT_MODEL` |
| `runtime/resources.py` | `AgentResources`（context manager）、`ResourceConfig`、`CompositeBackend`（`/memories/` `/artifacts/` `/large_tool_results/` `/skills/` 路由） |
| `runtime/runs.py` | `SqliteRunLedger`、`RunEvent`、`RunSnapshot`。fresh schema，UTC ISO-8601 毫秒时间戳，无迁移代码。`RUN_STATUSES = {queued, running, succeeded, failed, cancelled, cancelling}`。`aggregate_model_usage` |
| `runtime/tools.py` | `ToolCatalog` dataclass + `default_tool_catalog()`（静态注册 6 个工具） |
| `integrations/artifacts.py` | `artifacts_root`、`resolve_artifact_path`、`to_virtual_artifact_path`、`unique_download_path`、`write_json_artifact`、`read_json_artifact`、`clean_filename`、`make_unique_name`、`make_timestamped_name` |
| `integrations/mineru.py` | `parse_documents`、`extract_archives`、`MINERU_POLL_INTERVAL_SECONDS` |
| `skills/philipswgqimport/scripts/tools.py` | `save_philips_wgq_extraction` + `generate_philips_wgq_import`（2 个业务 Tool） |
| `skills/philipswgqimport/scripts/documents.py` | `generate_tracking`、`generate_invoice_packing`、`generate_bonded_checklist` + 共享 openpyxl helper（`header_columns`、`copy_sheet_row` 等） |
| `skills/tecanimport/scripts/tools.py` | `save_tecan_extraction` + `generate_tecan_import`（2 个业务 Tool） |
| `skills/tecanimport/scripts/documents.py` | `generate_invoice_packing` + `insert_rows` |

## 3. 核心依赖及用途

锁定版本来自 `uv.lock`（仅记录事实，不代表升级时的兼容性承诺）：

| 依赖 | 约束 | lock 版本 | 用途 | 证据 |
|---|---|---|---|---|
| `deepagents` | `>=0.6.12` | `0.6.12` | Agent 主体；`create_deep_agent(...)` 装配 Skills、声明式 SubAgents、权限、middleware 与可流式 agent；`CompositeBackend`/`FilesystemBackend`/`StateBackend`/`StoreBackend`、`FilesystemPermission`、`SubAgent`、`HarnessProfile`/`GeneralPurposeSubagentProfile`/`register_harness_profile` | `runtime/agent.py`、`runtime/resources.py` |
| `fastapi` | `>=0.116.1` | `0.139.0` | HTTP 框架；`create_app()` → `FastAPI(lifespan=...)` | `api.py` |
| `langchain` | `>=1.3.11` | `1.3.11` | `init_chat_model`、`AgentMiddleware`、`ToolCallRequest` | `runtime/agent.py` |
| `langchain-anthropic` | `>=1.4.8` | `1.4.8` | LLM provider（Anthropic 兼容客户端，实际指向 MiniMax 端点）；`thinking={"type":"adaptive"}` | 经 `init_chat_model("anthropic:...")` 间接使用 |
| `langchain-core` | `>=1.4.8` | `1.4.8` | `BaseChatModel`、`AIMessage` / `AIMessageChunk`、`HumanMessage`；测试断言 `thinking`/`text` block 载荷 | `runtime/observability.py`、`backend/tests/test_support.py` |
| `langgraph` | `>=1.2.7` | `1.2.7` | Agent 编排 / 流式 API；`get_stream_writer`（`langgraph.config`）；`RunControl`（`langgraph.runtime`）；`GraphDrained`（`langgraph.errors`） | `runtime/{agent,execution}.py`、`integrations/mineru.py`；harness 调用 `brain.stream(..., stream_mode=["messages","custom","updates"], subgraphs=True, version="v2", control=RunControl())` |
| `langgraph-checkpoint-sqlite` | `>=3.1.0` | `3.1.0` | LangGraph checkpointer（`SqliteSaver`，`langgraph.checkpoint.sqlite`）；同时经 `langgraph.store.sqlite.SqliteStore` 提供 store | `runtime/resources.py` |
| `openpyxl` | `>=3.1,<4` | `3.1.5` | 读取 tracking/订单/信息表并基于固定模板生成 Philips/Tecan Excel | 两个 Skill 的 `scripts/documents.py` |
| `oracledb` | `>=3,<4` | `3.4.2` | Philips 申报/法定单位可选查询（thick mode）；运行时延迟 import，失败走人工校验 | `skills/philipswgqimport/scripts/tools.py` |
| `python-multipart` | `>=0.0.20` | `0.0.32` | `POST /upload` 多文件上传解析（`UploadFile = File(...)`） | `api.py` |
| `python-dotenv` | `>=1.2.2` | `1.2.2` | `.env` 加载 | `runtime/agent.py`、`integrations/mineru.py` 各自 `load_dotenv(...)` |
| `requests` | `>=2.34.2` | `2.34.2` | 外部 HTTP（MinerU 任务提交/轮询/取结果） | `integrations/mineru.py` |
| `uvicorn` | `>=0.35.0` | `0.49.0` | ASGI 服务器（运行 FastAPI app）；`api.py` 未直接 import，由外部 `uv run uvicorn api:app` 命令拉起 | 依赖声明；`scripts/start-backend.bat` |
| `httpx2` | `>=2.5.0` | `2.5.0` | **测试依赖**（通过 `[project.dependencies]` 装入但运行时代码不直接 import）：`fastapi.testclient.TestClient` 的 HTTP 传输层，用于本地 HTTP 断言且避免 `starlette.testclient` 对 `httpx` 的弃用警告 | `backend/tests/test_api.py`、`backend/tests/test_support.py`（经 `TestClient` 间接使用） |

> `httpx2` 是 `fastapi`/`starlette.testclient` 的传递依赖被显式声明为直接依赖，目的是让 TestClient 的 HTTP 后端可被锁定。运行时业务代码不 `import httpx` / `httpx2`。

## 4. 本地持久化（存储栈）

数据目录固定为 `backend/data/`（路径由 `ResourceConfig` 决定，与 CWD 无关）。

| 组件 | 类型 | 落点 | 证据 |
|---|---|---|---|
| `SqliteRunLedger` | 标准库 `sqlite3` | `data/dsagents_runs.db` | `runtime/{resources,runs}.py` |
| `SqliteStore` | LangGraph store（`langgraph.store.sqlite`） | `data/dsagents_store.db` | `runtime/resources.py` |
| `SqliteSaver` | LangGraph checkpointer（`langgraph.checkpoint.sqlite`） | `data/dsagents_checkpoints.db` | `runtime/resources.py` |
| `CompositeBackend` | `deepagents.backends` | `/memories/` → store；`/artifacts/`、`/large_tool_results/` → artifact 文件系统；`/skills/` → 仓库 Skill 目录；其余 → state | `runtime/resources.py` |
| 大 run event 外溢 | 文件系统 | `data/internal/run-events/*.json` | `runtime/runs.py`（`max_inline_bytes=262_144`，lazy mkdir） |
| 上传文件（上传源） | 文件系统 | `data/artifacts/uploads/` | `api.py`（`_store_upload` lazy `mkdir(parents=True, exist_ok=True)`） |
| 工具/业务产物 | 文件系统 | `data/artifacts/downloads/` | MinerU JSON/ZIP、解压目录、immutable 业务 JSON 与 Excel；`integrations/mineru.py` 与 `integrations/artifacts.py unique_download_path` 在落盘前 lazy mkdir |

`dsagents_runs.db`、`artifacts/uploads/`、`artifacts/downloads/`、`internal/run-events/` 均在首次运行对应流程时按需创建。`AgentResources.__enter__` 启动时只确保 `data_dir` 与 `artifacts_dir` 存在。

### run-first 架构落地（`runtime/runs.py`）

- run 是唯一执行/查询单位；`run_events` append-only，`runs` 是投影快照。
- `runs` 表（投影）：`run_id`(PK) / `session_id` / `input_messages_json` / `status` / `created_at` / `updated_at` / `reply` / `error`；`status ∈ {queued, running, succeeded, failed, cancelled, cancelling}`。
- `run_events` 表（事件流）：`event_id`(自增 PK) / `run_id` / `type` / `created_at` / `payload_json`(+`payload_artifact_path`) / `raw_json`(+`raw_artifact_path`)；索引 `idx_run_events_run_order(run_id, event_id)`、`idx_runs_session_created(session_id, created_at desc)`。
- 时间字段统一写 UTC ISO-8601 毫秒（`_now_text()` = `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{microsecond//1000:03d}Z"`，如 `2026-07-13T08:18:59.250Z`）。
- **fresh schema，无迁移代码**：`_setup()` 仅 `create table if not exists` + 建索引；无 `pragma user_version`、无 `_migrate`。部署切换 = 停服务 + 整体清空 `backend/data/`，重启后由 `_setup` 与 LangGraph `.setup()` 重建。
- 大 payload（payload 或 raw 序列化后 > `262_144` 字节）外溢为 `data/internal/run-events/<uuid>.json`，行内只存 `{"artifact_path","bytes"}` 占位；读取时透明回填。
- `aggregate_model_usage(run_id)`：汇总该 run 所有 `model_usage` 事件为 token 总量 + per-agent 桶 + per-call 记录（per-call 保留模型与各 token 项，供 tier-aware 定价；价格估算在 `api._usage_summary` 完成，不入库）。
- `fail_incomplete_runs(error)`：启动时把遗留 `queued/running/cancelling` run 标 `failed`。

## 5. LLM Provider

| Provider | 集成方式 | 证据 |
|---|---|---|
| Anthropic 兼容（生产） | `init_chat_model(f"anthropic:{os.getenv('MINIMAX_MODEL')}", api_key=..., base_url=..., thinking={"type":"adaptive"})` → `ChatAnthropic`；由 `DeepAgentsBrainFactory` 注入 `create_deep_agent(model=...)` | `runtime/agent.py` |
| `FakeBrain`（本地测试） | `FakeBrain` / `FakeBrainFactory`，模拟 v2 `stream(...)` 产出 `messages`/`custom`/`updates` chunk（`subgraphs=True`），覆盖 `updates` → `assistant_message` / `tool_execution` 派生，并产出含 `usage_metadata` 的主/subagent chunk | `backend/tests/test_support.py` |

provider profile 边界（`runtime/agent.py`）：
- `register_harness_profile("anthropic", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))` —— 禁用 DeepAgents 自动添加的第五个 general-purpose subagent，只保留 `workflow_subagents()` 的四个显式 extractor。
- 锁定的 `deepagents==0.6.12` 支持 `skills`、`subagents`、`permissions`、`response_format`、`name`、`middleware`，但**不支持**官方新文档出现的 `create_deep_agent(..., harness_profile=...)` 参数；故用 profile 注册 API 而非构造参数。升级该依赖时需重新核对这两个入口。

> **`thinking` 参数版本敏感性**：`thinking={"type":"adaptive"}` 对 `langchain-anthropic` 版本敏感。当前锁版本为 `langchain-anthropic==1.4.8` / `deepagents==0.6.12`；升级时需验证 `init_chat_model("anthropic:...", thinking=...)` 仍被接受。

## 6. 配置加载

`.env` 由以下模块在导入时加载（`load_dotenv(...)`）：

- `runtime/agent.py`
- `integrations/mineru.py`

`api.py`、`runtime/{execution,observability,resources,runs,tools}.py`、`integrations/artifacts.py`、两个 Skill 包的 `scripts/` 均不直接 `load_dotenv`；它们消费的 `MINIMAX_*` / `MINERU_*` 等键由上述两个模块先把值注入 `os.environ` 后间接生效（Skill 的 `generate_philips_wgq_import` 另从 `os.environ` 直读 `ORACLE_*` 键）。

配置键清单与用途见 `INTEGRATIONS.md` §5（不在此重复，避免漂移）。本文件不记录本地 `.env` 真实值。

## 7. 运行时并发模型

| 项 | 值 |
|---|---|
| HTTP handler | 同步 `def`（FastAPI 同步路由跑在线程池） |
| 后台执行 | `threading.Thread(daemon=True)`，per-run（`api._run_background`） |
| 单飞锁 | per-`session_id` 的 `threading.Lock`，由 `registry_lock`（另一把 `threading.Lock`）保护 `session_locks` / `active_runs` 注册表；`POST /runs` 同 session 冲突返回 `409` |
| LangGraph 流 | classic `brain.stream(..., stream_mode=["messages","custom","updates"], subgraphs=True, version="v2", control=RunControl())`；`thread_id = session_id` |
| 取消 | `POST /runs/{run_id}/cancel` → `RunControl` 协作 drain → `GraphDrained` → `cancelled`；queued / 未进入 execute_run 的 run 直接置 `cancelled` |
| SubAgent | 四个声明式 extractor（`workflow_subagents()`）；每个自装 `runtime_middlewares()`；主 agent 可在同一模型回合发出并行 `task` 调用，subagent messages/todos/structured response 与父状态隔离 |
| 中断恢复 | 仅启动时 `fail_incomplete_runs` 把遗留 `queued/running/cancelling` 标 `failed`；无 worker 恢复器 |
| 无 | Redis / DB 锁 / 消息队列 / 外部任务调度 |

## 8. 运行命令说明（代码外约定）

| 项 | 说明 |
|---|---|
| `uvicorn` | 仅作为依赖声明存在，`api.py` 不直接 `import uvicorn`；由外部命令拉起 `api:app` |
| 启动命令 | `scripts/start-backend.bat`：`cd backend` 后 `uv run uvicorn api:app --host 0.0.0.0 --port 8500` |
| 默认端口 | `8500`（脚本提示与 `backend/tests/test_real_image_run.py` 的 `DEFAULT_BASE_URL = "http://127.0.0.1:8500"` 一致） |

## 9. 测试工具栈

| 项 | 值 | 证据 |
|---|---|---|
| HTTP 测试客户端 | `fastapi.testclient.TestClient`（底层走 `httpx2`） | `backend/tests/test_support.py`、`backend/tests/test_api.py` |
| Brain 替身 | `FakeBrain` / `FakeBrainFactory`，经 `create_app(harness_factory=...)` 注入，不触达真实 provider | `backend/tests/test_support.py` |
| 测试文件 | `tests/` 下 `test_*.py`（见 TESTING.md 完整清单） | `backend/tests/` |
