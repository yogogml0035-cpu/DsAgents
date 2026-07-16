---
last_mapped_commit: 28534a9
---

# Technology Stack

**Analysis Date:** 2026-07-16

> 技术栈事实基于 `backend/pyproject.toml`、`backend/uv.lock` 与 `backend/` 顶层源码（`api.py`、`runtime/`、`integrations/`、`skills/`）核对。不读取真实密钥文件；配置键来自 `backend/.env.example` 与代码默认值。运行命令以仓库 `scripts/start-backend.ps1` 与测试默认值为准。

## Languages

| 项 | 值 | 证据 |
|---|---|---|
| 语言 | Python | `backend/pyproject.toml` |
| 版本约束 | `>=3.11,<4.0` | `requires-python`；`uv.lock` 同步为 `>=3.11, <4.0` |
| 包名 / 版本 | `dsagents` / `0.1.0` | `[project]` |
| 描述 | Agent runtime for DeepAgents with pluggable document parsing. | `[project].description` |

发行名保持 `dsagents`；源码顶层为 `api.py` 与三个包 `runtime/`、`integrations/`、`skills/`（内置 Skill：`philipswgqinboundrecognition`、`tecanimport`）。模块使用绝对顶层导入（如 `from runtime.resources import AgentResources`）。

### 构建与打包

| 项 | 值 | 证据 |
|---|---|---|
| 包管理器 | `uv`（勿用 `pip install -e .` 绕过锁） | `backend/uv.lock`；`scripts/start-backend.ps1` 使用 `uv run` |
| 构建 backend | `setuptools>=68`（`setuptools.build_meta`） | `[build-system]` |
| 安装根 | `backend/`，`package-dir = {"" = "."}` | `[tool.setuptools]` |
| 顶层模块 | `py-modules = ["api"]` | `[tool.setuptools]` |
| 包发现 | `include = ["runtime*", "integrations*", "skills*"]` | `[tool.setuptools.packages.find]` |
| 打包数据 | Philips：`SKILL.md`；Tecan：`SKILL.md`、`references/*.md`、`assets/*` | `[tool.setuptools.package-data]` |

## Runtime

| 项 | 值 | 证据 |
|---|---|---|
| HTTP ASGI | FastAPI `0.139.0`（声明 `>=0.116.1`） | `pyproject.toml` / `uv.lock` |
| ASGI 服务器 | uvicorn `0.49.0`（声明 `>=0.35.0`） | 同上 |
| 进程模型 | 单进程；`POST /runs` 在 daemon `threading.Thread` 中执行 | `backend/api.py` `_run_background` |
| Session 单飞 | 进程内 `threading.Lock` + `active_runs`；同 `session_id` 并发返回 `409` | `backend/api.py` `_acquire_session_run` |
| 入口 ASGI 对象 | `api:app`（`app = create_app()`） | `backend/api.py`；`scripts/start-backend.ps1` |
| 默认监听 | `0.0.0.0:8500` | `scripts/start-backend.ps1`；测试默认 `http://127.0.0.1:8500` |
| 环境加载 | `python-dotenv` 读 `backend/.env` | `runtime/agent.py`、`integrations/mineru.py` 中 `BACKEND_ENV_PATH` + `load_dotenv` |
| 协作取消 | LangGraph `RunControl` + `GraphDrained` | `runtime/execution.py` |

数据目录固定为 `backend/data/`（相对 `backend/` 源码根，与 CWD 无关），由 `ResourceConfig` 定义：

| 路径属性 | 默认路径 | 用途 |
|---|---|---|
| `data_dir` | `backend/data/` | 总根 |
| `run_db` | `dsagents_runs.db` | run 投影 + 事件索引（`SqliteRunLedger`） |
| `store_db` | `dsagents_store.db` | LangGraph `SqliteStore`（`/memories/`） |
| `checkpoint_db` | `dsagents_checkpoints.db` | LangGraph `SqliteSaver`（`thread_id` = `session_id`） |
| `artifacts_dir` | `data/artifacts/` | 上传与下载文件落盘 |
| `run_events_dir` | `data/internal/run-events/` | 超阈值事件外置 |
| `skills_dir` | `backend/skills/` | Skill 只读挂载源 |

## Frameworks

### HTTP / 校验

| 库 | 锁定版本 | 用途 |
|---|---|---|
| `fastapi` | `0.139.0` | 路由、`UploadFile`、`JSONResponse`、lifespan |
| `starlette` | `1.3.1` | FastAPI 传递依赖 |
| `pydantic` | `2.13.4` | `RunRequest` / content blocks；Skill 结构化输出 schema |
| `python-multipart` | `0.0.32` | `POST /upload` multipart |
| `uvicorn` | `0.49.0` | ASGI 服务器 |

### Agent / LLM 编排

| 库 | 锁定版本 | 用途 |
|---|---|---|
| `deepagents` | `0.6.12` | `create_deep_agent`、`CompositeBackend` 路由、`FilesystemPermission`、`HarnessProfile` / `register_harness_profile`、SubAgent 声明 |
| `langchain` | `1.3.11` | `init_chat_model`、`AgentMiddleware`、`ToolStrategy` |
| `langchain-core` | `1.4.8` | `BaseChatModel`、消息块 |
| `langchain-anthropic` | `1.4.8` | Anthropic 兼容 Chat 模型（MiniMax 经此路径） |
| `anthropic` | `0.115.1` | Anthropic SDK 传递依赖 |
| `langgraph` | `1.2.7` | 图执行、`RunControl`、`GraphDrained`、stream v2 |
| `langgraph-checkpoint-sqlite` | `3.1.0` | `SqliteSaver` / `SqliteStore` 入口 |
| `langgraph-checkpoint` | `4.1.1` | checkpoint 核心 |
| `langgraph-prebuilt` | `1.1.0` | prebuilt 组件（传递） |
| `langgraph-sdk` | `0.4.2` | SDK 传递 |
| `langsmith` | `0.9.5` | 观测传递依赖（非本服务对外 API） |

`DeepAgentsBrainFactory`（`runtime/agent.py`）默认：

```text
init_chat_model(
  f"anthropic:{MINIMAX_MODEL}",
  api_key=MINIMAX_API_KEY,
  base_url=MINIMAX_BASE_URL,
  thinking={"type": "adaptive"},
)
```

并对 provider profile `"anthropic"` 注册 `HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False))`，禁用 deepagents 自动通用子代理；Tecan 仅保留显式 `tecan-extractor-a` / `tecan-extractor-b`。

可注入边界：`Brain` / `BrainFactory` 为 `typing.Protocol`；默认实现 `DeepAgentsBrainFactory`；`create_harness(...)` 组装 `HarnessRuntime`。

### 存储与虚拟文件系统

| 组件 | 实现 | 说明 |
|---|---|---|
| Run ledger | `sqlite3` + `SqliteRunLedger` | `runs` 投影 + `run_events` append-only；大 payload 可外置 `run-events/` |
| Checkpoint | `SqliteSaver` | LangGraph 按 `thread_id`（= `session_id`） |
| Store | `SqliteStore` | 跨 run 共享；namespace `("dsagents",)` |
| 默认虚拟 FS | `StateBackend` | 图内临时态 |
| `/artifacts/`、`/large_tool_results/` | `FilesystemBackend` → `data/artifacts/` | `virtual_mode=True` |
| `/skills/` | `FilesystemBackend` → `backend/skills/` | 只读意图；`FilesystemPermission` deny write `/skills/**` |
| `/memories/` | `StoreBackend` | 首次写入 `RUNTIME_AGENTS_BASELINE` 到 `/memories/AGENTS.md` |

### 文档 / 外部数据访问

| 库 | 锁定版本 | 用途 |
|---|---|---|
| `requests` | `2.34.2` | MinerU HTTP：提交任务、轮询、下载 JSON/ZIP |
| `oracledb` | `3.4.2` | Philips 主数据查询（可选 thick mode） |
| `openpyxl` | `3.1.5` | Tracking / 订单 / 信息表 / 发票箱单 Excel |
| `et-xmlfile` | `2.0.0` | openpyxl 传递依赖 |
| `python-dotenv` | `1.2.2` | 加载 `backend/.env` |

### 其它声明依赖

| 库 | 锁定版本 | 说明 |
|---|---|---|
| `httpx2` | `2.5.0` | 写在 `pyproject.toml`；当前 `backend/**/*.py` **无**直接 `import`（预留/传递） |
| `httpx` | `0.28.1` | 传递依赖（常见于 langchain/http 栈） |
| `sqlite-vec` | `0.1.9` | 随 langgraph sqlite 栈出现 |
| `aiosqlite` | `0.22.1` | 异步 sqlite 传递 |
| `cryptography` | `49.0.0` | 传递（oracledb 等） |
| `pyyaml` | `6.0.3` | 传递 |
| `tenacity` | `9.1.4` | 传递 |
| `orjson` | `3.11.9` | 传递 |

**未使用：** 无 `pytest` / `SQLAlchemy` / `pandas` / `numpy` / `langchain-openai` 作为项目依赖；测试为可执行 assert 脚本（`python -m tests.<name>`）。

## Key Dependencies（直接依赖对照）

`pyproject.toml` 声明与 `uv.lock` 解析版本：

| 声明 | 约束 | 锁定版本 |
|---|---|---|
| deepagents | `>=0.6.12` | `0.6.12` |
| fastapi | `>=0.116.1` | `0.139.0` |
| langchain | `>=1.3.11` | `1.3.11` |
| langchain-anthropic | `>=1.4.8` | `1.4.8` |
| langchain-core | `>=1.4.8` | `1.4.8` |
| langgraph | `>=1.2.7` | `1.2.7` |
| langgraph-checkpoint-sqlite | `>=3.1.0` | `3.1.0` |
| openpyxl | `>=3.1,<4` | `3.1.5` |
| oracledb | `>=3,<4` | `3.4.2` |
| python-multipart | `>=0.0.20` | `0.0.32` |
| python-dotenv | `>=1.2.2` | `1.2.2` |
| requests | `>=2.34.2` | `2.34.2` |
| uvicorn | `>=0.35.0` | `0.49.0` |
| httpx2 | `>=2.5.0` | `2.5.0` |

## Configuration

### 环境变量（键名；值见 `.env.example` 占位，不写真实密钥）

| 键 | 用途 | 消费位置 |
|---|---|---|
| `MINIMAX_API_KEY` | LLM API 密钥 | `runtime/agent.py` |
| `MINIMAX_BASE_URL` | Anthropic 兼容 base URL（示例 `https://api.minimaxi.com/anthropic`） | 同上 |
| `MINIMAX_MODEL` | 模型名（示例 `MiniMax-M3`） | 同上；`api.py` 对 `MiniMax-M3` 做 usage 估价 |
| `MINERU_BASE_URL` | MinerU 服务根 | `integrations/mineru.py`（必填） |
| `MINERU_BACKEND` | 解析 backend 名（示例 `vlm-engine`） | 同上（必填） |
| `MINERU_EFFORT` | 可选 effort 字符串 | 同上（可空） |
| `MINERU_TIMEOUT_SECONDS` | 提交/轮询/下载超时（示例 `7200`） | 同上（必填） |
| `ORACLE_DSN` | Oracle 连接 DSN | `skills/philipswgqinboundrecognition/scripts/tools.py` |
| `ORACLE_USERNAME` | Oracle 用户 | 同上 |
| `ORACLE_PASSWORD` | Oracle 密码 | 同上 |
| `ORACLE_CLIENT_LIB_DIR` | Instant Client 目录；非空则 `init_oracle_client` thick mode | 同上；`.env.example` 提示 `backend/.oracle/instantclient/...` |
| `ORACLE_TIMEOUT_SECONDS` | 连接/调用超时秒（默认代码侧 `"30"`） | 同上 |

环境文件路径：`backend/.env`（由代码 `load_dotenv`）；模板：`backend/.env.example`。

### 代码内常量（非 env）

| 常量 | 值 / 含义 | 位置 |
|---|---|---|
| `MINERU_POLL_INTERVAL_SECONDS` | `30.0` | `integrations/mineru.py` |
| `PRICING_AS_OF` | `"2026-07-12"` | `api.py` MiniMax 趋势估价元数据 |
| `_PRICEABLE_MODELS` | `{"MiniMax-M3"}` | `api.py` |
| 标准/长上下文阈值 | `512 * 1024` input tokens | `api.py` |
| `INTERRUPTED_RUN_ERROR` | `"执行已中断，请重试"` | `api.py` lifespan 启动时 `fail_incomplete_runs` |
| `SKILLS_SOURCE` | `"/skills/"` | `runtime/agent.py` |
| `RUNTIME_AGENTS_PATH` | `"/memories/AGENTS.md"` | `runtime/resources.py` |
| `max_inline_bytes` | `262_144`（ledger 事件内联阈值） | `runtime/runs.py` |
| `RUN_STATUSES` | queued/running/succeeded/failed/cancelled/cancelling | `runtime/runs.py` |
| `DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES` | `2` | `runtime/middleware.py` |
| `NO_PROGRESS_WINDOW` | `3` | `runtime/middleware.py` / `runtime/agent.py` |

### 工具静态注册

`runtime/tools.py` → `default_tool_catalog()` 固定五元组（不扫描目录）：

1. `parse_documents`（MinerU）
2. `extract_archives`（本地 ZIP 解包）
3. `lookup_philips_wgq_master_data`（Tracking + Oracle）
4. `save_tecan_extraction`
5. `generate_tecan_import`

Philips workflow 下 Brain 排除帝肯工具（`save_tecan_extraction` / `generate_tecan_import`），保留共享 MinerU 工具与 `lookup_philips_wgq_master_data`，并启用 `ToolStrategy(PhilipsWgqRecognitionResult)` + 结构化输出中间件。

### Middleware 栈（运行时）

主 Agent 经 `runtime_middlewares(memory_backend=...)`（`runtime/middleware.py`）组装，含 `MemoryMiddleware`（`/memories/AGENTS.md`）与约 4 个运行时中间件（`ToolTelemetry`、`NoProgressMiddleware` 等）；Philips workflow 额外注入 `StructuredOutputRecovery` + `StructuredOutputCompatibility`。Tecan SubAgent 各自安装不含 memory 的 runtime middleware（声明式 SubAgent 不继承主 Agent middleware）。

`StructuredOutputRecovery`：`after_model` + `jump_to: "model"` 有界重试；`can_jump_to` 含 `"end"`，达 `max_retries` 或无法产出 `structured_response` 时显式 `jump_to: "end"`。

## Platform Requirements

| 项 | 要求 |
|---|---|
| OS | 开发/文档以 Windows 为主（`scripts/start-backend.ps1`）；Python 代码跨平台，路径统一用 `pathlib` |
| Python | `>=3.11,<4.0`（本机常见 3.12.x） |
| 包安装 | 在 `backend/` 执行 `uv sync`，以 `uv.lock` 为准 |
| 启动 | `cd backend && uv run uvicorn api:app --host 0.0.0.0 --port 8500`，或仓库根 `scripts/start-backend.ps1`（可新开窗口；`-Port` 默认 8500） |
| 磁盘 | 可写 `backend/data/`（SQLite 三库 + artifacts + 可选 run-events） |
| 网络 | 出站访问 MiniMax（`MINIMAX_BASE_URL`）与 MinerU（`MINERU_BASE_URL`）；Oracle 可选 |
| Oracle thick | 若设 `ORACLE_CLIENT_LIB_DIR`，需本机 Instant Client 动态库；缺失/失败时 Philips 工具以 `problems` 降级，不崩溃进程 |
| 前端 | 本子项目无内置 UI；客户端轮询 HTTP（无 SSE） |
| 测试 | `cd backend && python -m tests.<module>`；真实模型/MinerU/Oracle/外部 HTTP 与本地假 Brain 回归分离 |

## 源码布局（技术相关）

```text
backend/
  api.py                 # FastAPI 入口与 usage 估价
  pyproject.toml / uv.lock
  .env.example
  runtime/               # agent、execution、middleware、observability、resources、runs、tools
  integrations/          # artifacts 路径约定、mineru 客户端
  skills/
    philipswgqinboundrecognition/  # workflow + schema + Oracle/Tracking tool
    tecanimport/                   # Excel 生成 + 抽取/一站式 tool + assets/references
  tests/                 # assert 脚本，非 pytest
  data/                  # 运行时生成（通常不入库）
```

## Analysis 边界

- 仅映射 **backend 子项目**技术栈；前端/其它 monorepo 包不在此文档范围。
- 版本以 **当前 `uv.lock` 解析结果**为准；升级依赖后需重跑映射并更新 `last_mapped_commit`。
- 不记录 `.env` 真实值、私有连接串或密钥。
