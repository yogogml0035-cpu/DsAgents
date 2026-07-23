# STACK — backend 技术栈事实

> Analysis Date: 2026-07-22
> 范围：`backend/` 权威源码（`api.py`、`runtime/`、`integrations/`、`skills/`、`tests/`、`pyproject.toml`、`uv.lock`）。
> **不**把 setuptools 构建产物（历史 `backend/build/`、`dist/`、`*.egg-info`）当源码；忽略 `data/`、`log/`、`__pycache__/`、`.venv/`。

## Languages

| 语言 | 用途 |
|------|------|
| **Python** `>=3.11,<4.0` | 全部产品与测试代码；当前环境常见 3.12 |
| Markdown | Skill 资源：`skills/*/SKILL.md`、两个渠道的 `references/*.md` |
| SQL | Philips Oracle 查询字符串（`skills/philips_wgq_inbound_recognition/scripts/tools.py` 内 `_ORACLE_SQL`） |
| JSON / JSONL | run ledger 投影、artifacts、OMS 索引行 |

无 TypeScript/前端；发行包为纯 Python wheel（`dsagents`）。

## Runtime

| 项目 | 事实 |
|------|------|
| 解释器 | CPython 3.11+ |
| ASGI 服务器 | **uvicorn** 加载 `api:app` |
| 进程模型 | 单进程 uvicorn + FastAPI；每个 run 在 **daemon 线程**中执行（`api._run_background` → `harness.execute_run`） |
| 会话单飞 | 进程内 `session_id` 锁：`app.state.session_locks`（`threading.Lock`）+ `active_runs`；冲突返回 HTTP 409；**无**跨 worker 互斥 |
| 取消 | `langgraph.runtime.RunControl` 协作 drain；`GraphDrained` → run `cancelled`；无法强杀外部 HTTP/Oracle |
| 入口模块 | `api:app`（`create_app()` 模块级实例） |
| 程序内入口 | `AgentResources` + `runtime.execution.create_harness(...).execute_run(...)` |
| 环境加载 | `python-dotenv`：`runtime/agent.py` 与 `integrations/mineru.py` 均 `load_dotenv(backend/.env)` |
| 数据锚定 | `ResourceConfig` 以 `backend/` 为根（`Path(__file__).parents[1]`），与 CWD 无关 |
| 启动清理 | lifespan 内 `runs.fail_incomplete_runs("执行已中断，请重试")` 将 `queued`/`running`/`cancelling` 标为 `failed` |

启动示例：

```powershell
cd backend
uv sync
uv run uvicorn api:app --host 0.0.0.0 --port 8500
```

### 执行路径（运行时）

1. `POST /runs` 写 ledger（`queued`）→ 可选 OMS JSONL → 启动 daemon 线程。
2. 线程内 `HarnessRuntime.execute_run`：`emit_run_status(running)` → `BrainFactory.create` → `brain.stream(..., control=RunControl)`。
3. stream 模式：`stream_mode=["messages", "custom", "updates"]`，`version="v2"`，`subgraphs=True`；`thread_id=session_id`。
4. 事件写入 `SqliteRunLedger`；终态 `succeeded` / `failed` / `cancelled`；`finally` 释放 session 锁。

## Package Manager (uv)

| 项目 | 事实 |
|------|------|
| 管理器 | **`uv`**；以 `backend/uv.lock` 为准 |
| 清单 | `backend/pyproject.toml`（`name = "dsagents"`，`version = "0.1.0"`） |
| 构建 | setuptools `>=68`（`build-backend = "setuptools.build_meta"`） |
| 安装布局 | `py-modules = ["api"]`；packages：`runtime*`、`integrations*`、`skills*` |
| package-data | Skill 资源：Philips / Tecan 的 `SKILL.md` 与 `references/*.md` |
| 同步 | `cd backend && uv sync`；**不要**用 `pip install -e .` 绕过 lock |

### 直接依赖（`pyproject.toml` → `uv.lock` 锁定版本）

| 包 | 约束 | 锁定版本 | 用途 |
|----|------|----------|------|
| `deepagents` | `>=0.6.12` | **0.6.12** | Agent harness、Skill 挂载、CompositeBackend、permissions |
| `fastapi` | `>=0.116.1` | **0.139.0** | HTTP 四端点 |
| `uvicorn` | `>=0.35.0` | **0.49.0** | ASGI 服务器 |
| `python-multipart` | `>=0.0.20` | **0.0.32** | `POST /upload` multipart |
| `langchain` | `>=1.3.11` | **1.3.11** | agents middleware / ToolStrategy |
| `langchain-core` | `>=1.4.8` | **1.4.8** | messages、BaseChatModel |
| `langchain-anthropic` | `>=1.4.8` | **1.4.8** | Anthropic 兼容 chat（MiniMax 经此路径） |
| `langgraph` | `>=1.2.7` | **1.2.7** | stream、RunControl、GraphDrained |
| `langgraph-checkpoint-sqlite` | `>=3.1.0` | **3.1.0** | SqliteSaver / SqliteStore |
| `openpyxl` | `>=3.1,<4` | **3.1.5** | 只读 XLSX（Tracking / 供应链 workbook） |
| `oracledb` | `>=3,<4` | **3.4.2** | Philips 可选主数据补齐 |
| `python-dotenv` | `>=1.2.2` | **1.2.2** | 本地 `.env` |
| `requests` | `>=2.34.2` | **2.34.2** | MinerU HTTP |
| `httpx2` | `>=2.5.0` | **2.5.0** | 声明依赖；**源码未直接 import**（预留/传递） |

### 关键传递依赖（锁定）

| 包 | 版本 | 备注 |
|----|------|------|
| `anthropic` | 0.115.1 | MiniMax Anthropic 兼容 SDK 底层 |
| `pydantic` | 2.13.4 | FastAPI 与业务 schema |
| `starlette` | 1.3.1 | FastAPI 底座 |
| `httpx` | 0.28.1 | LangChain / anthropic 客户端 |
| `aiosqlite` | 0.22.1 | checkpoint-sqlite 异步侧 |
| `sqlite-vec` | 0.1.9 | checkpoint-sqlite 依赖 |
| `langgraph-checkpoint` | 4.1.1 | 检查点抽象 |
| `langgraph-prebuilt` | 1.1.0 | prebuilt 图组件 |
| `langsmith` | 0.9.5 | LangChain 可观测（依赖链，非本仓库显式接线） |
| `tenacity` | 9.1.4 | 重试工具 |
| `orjson` / `ormsgpack` | 3.11.9 / 1.12.2 | 序列化 |
| `xxhash` | 3.8.0 | LangGraph 哈希 |

DeepAgents 传递依赖可含 `langchain-google-genai`；**本仓库未接线**第二 LLM provider。

## Frameworks

### HTTP：FastAPI（轮询，无 SSE）

定义于 `backend/api.py`：

| 方法 | 路径 | 作用 |
|------|------|------|
| `POST` | `/upload` | multipart → `data/artifacts/uploads/`，返回 `/artifacts/uploads/...` |
| `POST` | `/runs` | 创建 run + daemon 线程执行；可选 `workflow` |
| `GET` | `/runs/{run_id}` | 投影快照 + events + usage（可选 `after_event_id`） |
| `POST` | `/runs/{run_id}/cancel` | 协作 cancel（`RunControl.request_drain`） |

请求体用 Pydantic v2（`extra="forbid"`）。HTTP workflow 字面量：`WGQ`（飞利浦外高桥）与 `DK`（帝肯境外供应链）。
**无** session 管理 API、**无** SSE、**无** 下载端点、**无** HTTP Auth 中间件。

### Agent：DeepAgents + LangGraph + LangChain

| 组件 | 路径 / 符号 | 说明 |
|------|-------------|------|
| Brain 工厂 | `runtime.agent.DeepAgentsBrainFactory` | `create_deep_agent(**kwargs)` |
| Protocol | `Brain` / `BrainFactory` | 项目中 **仅** 这两处使用 `typing.Protocol` |
| 执行 | `runtime.execution.HarnessRuntime` | stream → 7 类事件投影 |
| 取消 | `langgraph.runtime.RunControl` / `GraphDrained` | 协作 drain，非强杀 |
| 模型 | `langchain.chat_models.init_chat_model` | `anthropic:{MINIMAX_MODEL}` + `base_url`/`api_key` + `thinking={"type":"adaptive"}` |
| 结构化输出 | `ToolStrategy(PhilipsWgqRecognitionResult)` | 仅 WGQ workflow |
| Harness profile | `register_harness_profile("anthropic", ...)` | `GeneralPurposeSubagentProfile(enabled=False)` |
| Skills 挂载 | `skills=[SKILLS_SOURCE]` → `"/skills/"` | 资源目录只读 deny write |
| Middleware | `runtime.middleware` | ToolTelemetry、NoProgress、Memory、Philips recovery 等 |
| Checkpointer | `langgraph.checkpoint.sqlite.SqliteSaver` | 图状态按 `thread_id` |
| Store | `langgraph.store.sqlite.SqliteStore` | `/memories/` 跨 run |

生产 **不**配置业务 SubAgent：`subagents=[]`。

### 虚拟文件系统（DeepAgents backends）

`runtime.resources.AgentResources`：

| 虚拟前缀 | Backend | 物理/存储 |
|----------|---------|-----------|
| 默认 | `StateBackend` | 图内临时状态 |
| `/memories/` | `StoreBackend` → `SqliteStore` | `data/dsagents_store.db`，namespace `("dsagents",)` |
| `/artifacts/` | `FilesystemBackend` | `data/artifacts/` |
| `/large_tool_results/` | 同上磁盘 backend | 大工具结果落盘 |
| `/skills/` | `FilesystemBackend` | `backend/skills/`（源码树） |

启动时若缺失则写入 `/memories/AGENTS.md` 基线手册（`RUNTIME_AGENTS_BASELINE`）。`FilesystemPermission` deny write `/skills/**`。

## Key Dependencies（按能力）

### LLM / Agent 栈

- **MiniMax** 经 Anthropic 兼容接口：环境变量 `MINIMAX_MODEL`、`MINIMAX_API_KEY`、`MINIMAX_BASE_URL`。
- 可观测模型名常量：`runtime.observability.MAIN_AGENT_MODEL = "MiniMax-M3"`。
- `api.py` 内嵌 MiniMax-M3 用量估价（`PRICING_AS_OF = "2026-07-12"`，标准/长上下文分档）；仅趋势估算，非账单。
- 测试可向 `DeepAgentsBrainFactory(model=...)` 注入假模型。

### 工具静态目录（5 个）

`runtime.tools.default_tool_catalog()` — **静态** import 注册，不自动扫描：

1. `parse_documents` — MinerU（`integrations/mineru.py`）
2. `extract_archives` — 本地 ZIP 解压到 artifacts
3. `lookup_philips_wgq_master_data` — Tracking XLSX + 可选 Oracle
4. `inspect_supply_chain_workbooks` — openpyxl 只读 → JSON artifact
5. `finalize_tecan_overseas_recognition` — Tecan 终态 schema 校验

Workflow **denylist**（排除其他业务工具，保留共享 MinerU / XLSX）：

| workflow | 排除 |
|----------|------|
| `WGQ` | `finalize_tecan_overseas_recognition` |
| `DK` | `lookup_philips_wgq_master_data` |

### 文档解析

- MinerU：HTTP `POST {MINERU_BASE_URL}/tasks`，轮询 `status_url`（间隔 `MINERU_POLL_INTERVAL_SECONDS = 30`），下载 JSON 或 ZIP 到 `/artifacts/downloads/`。
- 客户端库：`requests`（非 httpx 直接调用 MinerU）。

### 表格

- `openpyxl.load_workbook(..., read_only=True, data_only=True)`：Philips Tracking、Tecan workbook 检查。
- **不**生成业务 Excel / 模板（Tecan 不携带模板生成器）。

### 数据库驱动

| 存储 | 库/驱动 | 路径默认 |
|------|---------|----------|
| Run ledger | stdlib `sqlite3`（`SqliteRunLedger`） | `data/dsagents_runs.db` + 大事件外置 `data/internal/run-events/` |
| Checkpoints | `langgraph.checkpoint.sqlite.SqliteSaver` | `data/dsagents_checkpoints.db` |
| Store | `langgraph.store.sqlite.SqliteStore` | `data/dsagents_store.db` |
| 主数据（可选） | `oracledb` | 环境变量 DSN，非本地文件 |

三库连接**不共享**。时间戳统一 **UTC+8** 文本 `YYYY-MM-DD HH:MM:SS`。

### Skills 单目录

| Skill 包（资源 + 代码同包） |
|----------------------------|
| `skills/philips_wgq_inbound_recognition/` |
| `skills/tecan_import/` |

共享合同：`skills/channel_contract.py`（`items[]` 完整 24 字段等）。

## Configuration

### 运行时环境变量（代码读取；不记录密钥值）

**LLM（`runtime/agent.py`）**

| 变量 | 必需性 | 用途 |
|------|--------|------|
| `MINIMAX_MODEL` | 默认 Brain 需要 | 模型名，拼入 `anthropic:{model}` |
| `MINIMAX_API_KEY` | 默认 Brain 需要 | API 密钥 |
| `MINIMAX_BASE_URL` | 默认 Brain 需要 | Anthropic 兼容 base URL |

**MinerU（`integrations/mineru.py`）**

| 变量 | 必需性 | 用途 |
|------|--------|------|
| `MINERU_BASE_URL` | 调用 `parse_documents` 时必需 | API 根 |
| `MINERU_BACKEND` | 必需 | 提交 form 的 `backend` |
| `MINERU_TIMEOUT_SECONDS` | 必需 | 请求与轮询超时（秒，int） |
| `MINERU_EFFORT` | 可选 | 空字符串则传 `""` |

**Oracle（`skills/philips_wgq_inbound_recognition/scripts/tools.py`）**

| 变量 | 必需性 | 用途 |
|------|--------|------|
| `ORACLE_DSN` | 三者齐备才连库 | 连接串 |
| `ORACLE_USERNAME` | 同上 | 用户 |
| `ORACLE_PASSWORD` | 同上 | 密码 |
| `ORACLE_CLIENT_LIB_DIR` | 可选 | thick mode `init_oracle_client(lib_dir=...)` |
| `ORACLE_TIMEOUT_SECONDS` | 可选，默认 `30` | 连接/调用超时 |

缺 Oracle 配置时返回 `problems`，不抛死。

**集成/真实测试（仅 `tests/`，不进生产路径）**

| 变量族 | 用途 |
|--------|------|
| `DSAGENTS_API_BASE_URL` / `DSAGENTS_BASE_URL` | 真实 HTTP 回归 base URL |
| `DSAGENTS_RUN_REAL_*` | 开关（如 `DSAGENTS_RUN_REAL_IMAGE_TEST=1`） |
| `DSAGENTS_IMAGE_PATH`、`DSAGENTS_PDF_DIR`、`DSAGENTS_PHILIPS_WGQ_*` 等 | 样本路径与超时/轮询间隔 |

### 路径与本地配置文件

| 路径 | 作用 |
|------|------|
| `backend/.env` | dotenv 加载目标（**勿**把真实内容写入文档/VCS） |
| `backend/data/` | 三 SQLite + artifacts + internal run-events |
| `backend/data/artifacts/uploads/` | HTTP 上传 |
| `backend/data/artifacts/downloads/` | MinerU / 工具输出 |
| `backend/log/oms_log.log` | OMS JSONL 索引（默认） |
| `backend/skills/` | Skill 资源 + Python 包 |

`ResourceConfig` 可注入覆盖 `data_dir`（测试常用临时目录）。

### 测试运行方式

可执行 assert 脚本（**非 pytest**）：

```powershell
cd backend
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
```

真实模型 / MinerU / Oracle / 外部 HTTP 与本地 mock 回归分离。

## Platform Requirements

| 要求 | 说明 |
|------|------|
| OS | 开发/部署以 Windows 与 Linux 通用 Python 路径为主；路径用 `pathlib` |
| Python | `>=3.11,<4.0` |
| 磁盘 | 可写 `backend/data/`、`backend/log/` |
| 网络 | 出站访问 MiniMax（或兼容端点）与 MinerU；Oracle 按部署网络 |
| **Oracle thick（可选）** | 需本机 Instant Client 目录 + 环境变量 `ORACLE_CLIENT_LIB_DIR`；缺失时不 init thick，配置不全则软降级为 `problems` |
| 多 worker | 不支持跨进程 session 锁与 cancel 协调；**单 worker** 部署假设 |
| 包管理 | 生产同步必须 `uv sync` + `uv.lock` |

## 架构边界摘要（栈视角）

- **run-first**：`runs` 投影 + append-only `run_events`；`session_id` 仅作 LangGraph `thread_id` 与进程内单飞。
- **渠道终态**：Philips（WGQ）→ `ToolStrategy` → `run.result`；Tecan（DK）→ finalizer 工具消息 → `run.result`。
- **OMS**：`runtime.oms_log` 旁路 JSONL，best-effort，不阻塞 HTTP 200 queued。
- **权威源码树**：`api.py` + `runtime/` + `integrations/` + `skills/`；忽略 `build/` 历史产物。
