---
last_mapped_commit: 08413f4688e03e5a24fb8ac08270541d280aee5d
---

# Technology Stack

**Analysis Date:** 2026-07-16

> 技术栈事实基于 `backend/pyproject.toml`、`backend/uv.lock` 与 `backend/` 顶层源码（`api.py`、`runtime/`、`integrations/`、`skills/`）核对。不读取真实密钥文件；运行命令以仓库 `scripts/start-backend.ps1` 与测试默认值为准。

## Languages

| 项 | 值 | 证据 |
|---|---|---|
| 语言 | Python | `backend/pyproject.toml` |
| 版本约束 | `>=3.11,<4.0` | `requires-python` |
| 包名 / 版本 | `dsagents` / `0.1.0` | `[project]` |
| 描述 | Agent runtime for DeepAgents with pluggable document parsing. | `[project].description` |

发行名保持 `dsagents`；源码顶层为 `api.py` 与三个包 `runtime/`、`integrations/`、`skills/`（含内置 Skill 包 `philipswgqinboundrecognition`、`tecanimport`）。模块使用绝对顶层导入（如 `from runtime.resources import AgentResources`）。

### 构建与打包

| 项 | 值 | 证据 |
|---|---|---|
| 包管理器 | `uv`（非 `pip install -e .`） | `backend/uv.lock`、`scripts/start-backend.ps1` 用 `uv run` |
| 构建 backend | `setuptools>=68`（`setuptools.build_meta`） | `[build-system]` |
| 安装根 | `backend/`，`package-dir = {"" = "."}` | `[tool.setuptools]` |
| 顶层模块 | `py-modules = ["api"]` | `[tool.setuptools]` |
| 包发现 | `include = ["runtime*", "integrations*", "skills*"]` | `[tool.setuptools.packages.find]` |
| 包数据 | Philips 打包 `SKILL.md`；Tecan 打包 `SKILL.md`、`references/*.md`、`assets/*` | `[tool.setuptools.package-data]` |

## Runtime

### 进程与并发

| 项 | 值 | 证据 |
|---|---|---|
| HTTP 入口 | `api:app`（`create_app()` 返回的 `FastAPI`） | `backend/api.py` |
| ASGI 服务器 | `uvicorn`（依赖声明；`api.py` 不直接 import） | `pyproject.toml`、`scripts/start-backend.ps1` |
| 启动命令 | `cd backend` 后 `uv run uvicorn api:app --host 0.0.0.0 --port 8500` | `scripts/start-backend.ps1` |
| 默认端口 | `8500` | 启动脚本；`tests/test_real_image_run.py` 的 `DEFAULT_BASE_URL` |
| HTTP handler | 同步 `def`（FastAPI 同步路由跑在线程池） | `api.py` |
| 后台执行 | `threading.Thread(daemon=True)`，per-run（`_run_background`） | `api.py` |
| 单飞锁 | per-`session_id` 的 `threading.Lock`；`registry_lock` 保护 `session_locks` / `active_runs` | `api.py` |
| 取消 | `RunControl` 协作 drain → `GraphDrained` → `cancelled` | `runtime/execution.py`、`langgraph.runtime` |
| 中断恢复 | 启动时 `fail_incomplete_runs` 把遗留 `queued/running/cancelling` 标 `failed`；无 worker 恢复器 | `api.py` lifespan、`runtime/runs.py` |
| 无 | Redis / 分布式锁 / 消息队列 / 外部任务调度 | 代码全库检索 |

### 数据目录（与 CWD 无关）

`ResourceConfig`（`runtime/resources.py`）将数据根固定为 `backend/data/`：

| 路径属性 | 落点 |
|---|---|
| `run_db` | `data/dsagents_runs.db` |
| `store_db` | `data/dsagents_store.db` |
| `checkpoint_db` | `data/dsagents_checkpoints.db` |
| `artifacts_dir` | `data/artifacts` |
| `run_events_dir` | `data/internal/run-events` |
| `skills_dir` | `backend/skills`（非 data 下） |

`AgentResources.__enter__` 确保 `data_dir` 与 `artifacts_dir` 存在；uploads/downloads/run-events 在首次使用时 lazy mkdir。

### 顶层源码模块

| 模块 | 职责 |
|---|---|
| `api.py` | FastAPI run-first HTTP 层；`_usage_summary` 价格估算 |
| `runtime/__init__.py` | 对外稳定入口：`AgentResources` / `create_harness` / 相关类型 |
| `runtime/agent.py` | `Brain`/`BrainFactory` Protocol、`DeepAgentsBrainFactory`、`workflow_subagents()`、middleware 装配入口 |
| `runtime/middleware.py` | `ToolTelemetry`、`NoProgressMiddleware`、`StructuredOutputCompatibility`、主 Agent `MemoryMiddleware` 装配与 `runtime_middlewares()` |
| `runtime/execution.py` | `HarnessRuntime.execute_run`、`create_harness`、消息归一化、stream → RunEvent |
| `runtime/observability.py` | `model_usage`、thinking/text delta、subagent 过滤、`MAIN_AGENT_NAME` |
| `runtime/resources.py` | `AgentResources`、`ResourceConfig`、`CompositeBackend` 路由 |
| `runtime/runs.py` | `SqliteRunLedger`、`RunEvent`、`RunSnapshot`、`aggregate_model_usage` |
| `runtime/tools.py` | `ToolCatalog` + `default_tool_catalog()`（静态 5 工具） |
| `integrations/artifacts.py` | 虚拟路径解析、上传命名、immutable JSON 落盘 |
| `integrations/mineru.py` | `parse_documents`、`extract_archives`、MinerU HTTP 客户端 |
| `skills/philipswgqinboundrecognition/` | Philips WGQ 结构化识别（`SKILL.md` + Pydantic schema + 单一主数据工具） |
| `skills/tecanimport/` | Tecan 进口 Skill（`SKILL.md` + tools/documents + 模板） |

## Frameworks

### HTTP

- **FastAPI**（`>=0.116.1`，lock `0.139.0`）：`create_app(*, resource_config, harness_factory)` → `FastAPI(lifespan=...)`。
- 端点：`POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`。
- 请求/响应模型：Pydantic v2（HTTP `RunRequest` + Philips `PhilipsWgqRecognitionResult`，均 `extra="forbid"`）。
- 无 SSE / `StreamingResponse`；客户端靠轮询 `GET /runs/{run_id}?after_event_id=...`。
- 未注册 `CORSMiddleware`。

### Agent / 编排

- **DeepAgents**（`>=0.6.12`，lock `0.6.12`）：`create_deep_agent(...)` 装配 model、tools、skills、subagents、permissions、checkpointer、store、backend、middleware、name。
- **LangGraph**（`>=1.2.7`，lock `1.2.7`）：`brain.stream(..., stream_mode=["messages","custom","updates"], subgraphs=True, version="v2", control=RunControl())`；`thread_id = session_id`。
- **LangChain**（`>=1.3.11`，lock `1.3.11`）：`init_chat_model`、`AgentMiddleware`、`ToolCallRequest`、`ToolStrategy`。
- **langchain-core**（`>=1.4.8`，lock `1.4.8`）：`BaseChatModel`、消息类型。
- **langchain-anthropic**（`>=1.4.8`，lock `1.4.8`）：经 `init_chat_model("anthropic:...")` 得到 `ChatAnthropic`；实际端点可指向 MiniMax Anthropic 兼容 API。

### DeepAgents 装配要点（`runtime/agent.py` + `runtime/middleware.py`）

- 生产 brain：`DeepAgentsBrainFactory` → `init_chat_model(f"anthropic:{MINIMAX_MODEL}", api_key=..., base_url=..., thinking={"type":"adaptive"})` → `create_deep_agent(...)`。
- `skills=["/skills/"]`；主 agent 名 `MAIN_AGENT_NAME = "dsagents-main"`。
- `runtime_middlewares(*, memory_backend=None)` 每个 agent graph 返回新建的 `ToolTelemetry`、`NoProgressMiddleware`、`StructuredOutputCompatibility`；主 Agent（`execution.py`）传 `memory_backend=resources.backend` 时追加内置 `MemoryMiddleware(sources=["/memories/AGENTS.md"], system_prompt=RUNTIME_MEMORY_SYSTEM_PROMPT)`。两个声明式 Tecan SubAgent（`tecan-extractor-a/b`）各自注入无 memory 的 `runtime_middlewares()` 并使用只读文件系统权限；Philips workflow 不装 SubAgent。
- Philips workflow 使用 `ToolStrategy(PhilipsWgqRecognitionResult)`；`StructuredOutputCompatibility.wrap_model_call` 在本次 `ToolStrategy` 请求绑定前用 `request.override(model=...)` 复制模型并关闭 `ChatAnthropic.thinking`，规避 Anthropic-compatible endpoint 在强制 tool choice + thinking 下的兼容性/性能问题；工厂持有的 adaptive thinking 模型不被修改，同时只暴露 `parse_documents` 和 `lookup_philips_wgq_master_data`。直接调用工厂且传入空 middleware 时，工厂会补齐该兼容 middleware。
- `register_harness_profile("anthropic", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))` 禁用默认 general-purpose subagent（锁版本 0.6.12 无 `harness_profile=` 构造参数）。
- `/skills/**` 写权限 deny；SubAgent 对 `/**` write deny。
- `AnthropicPromptCachingMiddleware` 由 DeepAgents 尾栈自动挂载（非本仓库自定义）；因 MiniMax 走 `ChatAnthropic`，对 MiniMax-M3 生效。

### Backend 虚拟文件系统（`runtime/resources.py`）

`CompositeBackend` 路由：

| 虚拟前缀 | 实现 | 说明 |
|---|---|---|
| `/memories/` | `StoreBackend` → SQLite store，namespace `("dsagents",)` | 共享运行时操作手册 `/memories/AGENTS.md`（缺失时 seed，不覆盖） |
| `/artifacts/` | `FilesystemBackend(artifacts_dir, virtual_mode=True)` | 上传与产物 |
| `/large_tool_results/` | 同上 disk backend | 大工具结果 |
| `/skills/` | `FilesystemBackend(skills_dir, virtual_mode=True)` | Skill 只读源 |
| 其它 | `StateBackend()` | 同 `thread_id` 图状态 |

## Key Dependencies

锁定版本来自 `backend/uv.lock`（仅记录当前事实，不代表升级兼容性承诺）：

| 依赖 | 约束 | lock 版本 | 用途 | 主要消费者 |
|---|---|---|---|---|
| `deepagents` | `>=0.6.12` | `0.6.12` | Agent 主体、backends、permissions、SubAgent、HarnessProfile | `runtime/agent.py`、`runtime/resources.py` |
| `fastapi` | `>=0.116.1` | `0.139.0` | HTTP 框架 | `api.py` |
| `uvicorn` | `>=0.35.0` | `0.49.0` | ASGI 服务器 | 外部 `uv run uvicorn` |
| `langchain` | `>=1.3.11` | `1.3.11` | chat model 初始化、middleware、structured output | `runtime/agent.py`、`runtime/middleware.py` |
| `langchain-anthropic` | `>=1.4.8` | `1.4.8` | Anthropic 兼容 LLM 客户端 | 经 `init_chat_model` |
| `langchain-core` | `>=1.4.8` | `1.4.8` | 消息与模型基类 | `runtime/agent.py`、`runtime/middleware.py`、`runtime/observability.py` |
| `langgraph` | `>=1.2.7` | `1.2.7` | 编排 / stream / `RunControl` / `GraphDrained` / `get_stream_writer` | `runtime/execution.py`、`runtime/middleware.py`、`integrations/mineru.py` |
| `langgraph-checkpoint-sqlite` | `>=3.1.0` | `3.1.0` | `SqliteSaver` + 经 `langgraph.store.sqlite` 的 `SqliteStore` | `runtime/resources.py` |
| `openpyxl` | `>=3.1,<4` | `3.1.5` | Philips 读 Tracking；Tecan 读订单/信息表并写 Excel | 两个 Skill 的 `scripts/` |
| `oracledb` | `>=3,<4` | `3.4.2` | Philips 稳定主数据可选补齐（延迟 import，可选 thick mode） | `skills/philipswgqinboundrecognition/scripts/tools.py` |
| `python-multipart` | `>=0.0.20` | `0.0.32` | multipart 上传 | `api.py` `UploadFile` |
| `python-dotenv` | `>=1.2.2` | `1.2.2` | 加载 `backend/.env` | `runtime/agent.py`、`integrations/mineru.py` |
| `requests` | `>=2.34.2` | `2.34.2` | MinerU HTTP；部分真实集成测试 | `integrations/mineru.py`、真实 run 测试 |
| `httpx2` | `>=2.5.0` | `2.5.0` | **测试用** HTTP 传输（`TestClient`）；运行时业务代码不直接 import | 经 `fastapi.testclient` |

> `httpx2` 显式声明为直接依赖，锁定 TestClient 后端并避免 starlette 对旧 `httpx` 的弃用警告。业务路径不 `import httpx` / `httpx2`。

### 标准库（关键）

- `sqlite3`：`SqliteRunLedger`（`runtime/runs.py`）
- `threading`：session 单飞与后台 run 线程（`api.py`）
- `zipfile`：`extract_archives`（`integrations/mineru.py`）
- `mimetypes`、`shutil`、`uuid`、`json`、`pathlib`：上传与路径处理

### 本地持久化栈

| 组件 | 类型 | 落点 | 证据 |
|---|---|---|---|
| `SqliteRunLedger` | 标准库 `sqlite3` | `data/dsagents_runs.db` | `runtime/runs.py` |
| `SqliteStore` | LangGraph store | `data/dsagents_store.db` | `runtime/resources.py` |
| `SqliteSaver` | LangGraph checkpointer | `data/dsagents_checkpoints.db` | `runtime/resources.py` |
| 大 event 外溢 | 文件系统 | `data/internal/run-events/*.json` | `max_inline_bytes=262_144` |
| 上传源 | 文件系统 | `data/artifacts/uploads/` | `api.py` |
| 工具/业务产物 | 文件系统 | `data/artifacts/downloads/` | MinerU、Skill 生成 |

**run-first 要点**（`runtime/runs.py`）：

- run 是唯一执行/查询单位；`run_events` append-only，`runs` 为投影快照。
- 状态集：`queued` / `running` / `succeeded` / `failed` / `cancelled` / `cancelling`。
- 时间戳：UTC ISO-8601 毫秒（如 `2026-07-13T08:18:59.250Z`）。
- fresh schema，无迁移；切换部署可清空 `backend/data/` 后由 `_setup()` / `.setup()` 重建。
- `runs` 同时保存可选 `workflow` 与 `result_json`；Philips 结构化结果经 Pydantic 校验后写入，通用/Tecan 为 `null`。
- `aggregate_model_usage(run_id)` 汇总 token；CNY 估算在 `api._usage_summary`（仅 `MiniMax-M3` 可计价）。

### 工具注册（`runtime/tools.py`）

`default_tool_catalog()` 静态 5 个：

1. `parse_documents` — MinerU 批解析
2. `extract_archives` — ZIP 解压到 downloads
3. `lookup_philips_wgq_master_data` — Philips Tracking/Oracle 稳定字段补齐
4. `save_tecan_extraction` / `generate_tecan_import`

新增 Skill 时在此静态 import + 注册，不自动扫描。

### 测试工具栈

| 项 | 值 | 证据 |
|---|---|---|
| HTTP 客户端 | `fastapi.testclient.TestClient`（底层 `httpx2`） | `tests/test_support.py`、`tests/test_api.py` |
| Brain 替身 | `FakeBrain` / `FakeBrainFactory` | `tests/test_support.py` |
| 风格 | 可执行 assert 脚本（`tests/test_*.py`） | `backend/tests/` |
| 真实集成 | 需显式 env 开关（含 `DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1`） | `test_real_*.py` |

## Configuration

### `.env` 加载

导入时 `load_dotenv(backend/.env)`：

- `runtime/agent.py`（`MINIMAX_*`）
- `integrations/mineru.py`（`MINERU_*`）

`api.py`、其余 `runtime/*`、`integrations/artifacts.py`、Skill 脚本不直接 `load_dotenv`；Philips Skill 运行时从 `os.environ` 读取 `ORACLE_*`。模板键见 `backend/.env.example`（占位，非运行时事实）。完整键名清单见 `INTEGRATIONS.md`「Environment Variables」。

### 代码内配置常量（非 env）

| 常量 | 位置 | 含义 |
|---|---|---|
| `PRICING_AS_OF` / `_PRICING_TIERS` / `_PRICEABLE_MODELS` | `api.py` | MiniMax-M3 token → CNY 趋势估算 |
| `MINERU_POLL_INTERVAL_SECONDS = 30.0` | `integrations/mineru.py` | 状态轮询间隔 |
| `NO_PROGRESS_WINDOW = 3` | `runtime/middleware.py` | 无进展循环检测窗口 |
| `max_inline_bytes = 262_144` | `runtime/runs.py` | run event 行内外溢阈值 |
| `DEFAULT_SYSTEM_PROMPT` | `runtime/agent.py` | 主 agent 系统提示 |

### ResourceConfig 可注入

测试与程序内入口可通过 `ResourceConfig(data_dir=...)` 与 `create_app(resource_config=..., harness_factory=...)` 注入隔离数据目录与 FakeBrain。

## Platform Requirements

| 项 | 要求 |
|---|---|
| OS | 开发/部署以 Windows 为主（`scripts/start-backend.ps1`）；Python 代码跨平台，路径统一用 `pathlib` |
| Python | 3.11 或 3.12（仓库内见 `cpython-312` 字节码；约束 `<4.0`） |
| 包同步 | `cd backend && uv sync`（遵守 `uv.lock`） |
| Oracle（可选） | 凭证不全、client/查询失败或未命中时写入 `problems`，不丢弃 PDF/Tracking 结果；启用 thick mode 时需有效 `ORACLE_CLIENT_LIB_DIR` |
| MinerU | 需可达的 `MINERU_BASE_URL` 服务；缺失必填 env 时 `parse_documents` 抛 `RuntimeError` |
| MiniMax / Anthropic 兼容 | 需 `MINIMAX_API_KEY`、`MINIMAX_BASE_URL`、`MINIMAX_MODEL`；生产路径触达真实模型 |
| 磁盘 | 可写 `backend/data/`（SQLite + artifacts + 大 event 外溢） |
| 网络 | 出站访问 LLM 端点与 MinerU；Philips 可选出站 Oracle |

### 版本敏感点

- 通用/Tecan 的 `thinking={"type":"adaptive"}` 依赖当前 `langchain-anthropic==1.4.8`；所有 agent graph 的 `runtime_middlewares()` 都带 `StructuredOutputCompatibility`，仅在 `ToolStrategy` 请求通过 `model_copy(update={"thinking": None})` 复制一次性模型，避免在工厂装配时替换共享模型。
- `deepagents==0.6.12` 的 harness profile 用注册 API，非 `create_deep_agent(..., harness_profile=...)`。
- 升级上述依赖时需重测 brain 装配与 prompt-cache 行为。

---
*Stack analysis: 2026-07-16*
