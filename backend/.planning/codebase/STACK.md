---
last_mapped_commit: 3dadbc4
analysis_date: 2026-07-20
focus: tech
---

# STACK — backend 技术栈事实

Analysis Date: **2026-07-20**
`last_mapped_commit`: **3dadbc4**

本文档描述 `backend/` 子项目的语言运行时、包管理、框架、关键依赖、配置与平台要求。标识符、路径、命令、配置键与 API 名保留原文。

---

## 1. Languages / Runtime / Package Manager

| 项 | 事实 |
|---|---|
| 语言 | Python 3 |
| 版本约束 | `requires-python = ">=3.11,<4.0"`（`pyproject.toml`） |
| 实测本地 venv | Python **3.12.x**（本仓库映射时常见 `3.12.13`） |
| 包管理器 | **`uv`**（`cd backend && uv sync`；锁定文件 `backend/uv.lock`） |
| 构建后端 | `setuptools>=68`（`setuptools.build_meta`） |
| 发行包名 | **`dsagents`** `version = "0.1.0"` |
| 安装形态 | editable 源安装（`uv.lock` 中 `source = { editable = "." }`） |
| 模块布局 | 顶层 `api` 单模块 + 包 `runtime*` / `integrations*` / `skills*` |
| 标准库 SQLite | `sqlite3`（run ledger）；LangGraph 另用 SQLite checkpointer / store |

**不要**用 `pip install -e .` 绕过 `uv.lock`。依赖解析以 `uv.lock` 为准。

### 源码入口与发行结构

- HTTP / ASGI 入口：`backend/api.py` → 模块级 `app = create_app()`
- 生产启动（约定）：`uv run uvicorn api:app --host 0.0.0.0 --port 8500`
- 程序内入口：`AgentResources` + `create_harness(...).execute_run(...)`
- setuptools：
  - `py-modules = ["api"]`
  - packages：`runtime*`、`integrations*`、`skills*`
  - package-data：两个 Skill 的 kebab 资源目录（`SKILL.md` / references / assets）

---

## 2. Frameworks

### 2.1 HTTP / ASGI

| 组件 | 用途 |
|---|---|
| **FastAPI**（锁：`0.139.0`） | 四 HTTP 端点、请求体 Pydantic 模型、`lifespan` 装配资源 |
| **Starlette**（锁：`1.3.1`，FastAPI 传递） | ASGI 底层 |
| **Uvicorn**（锁：`0.49.0`） | ASGI 服务器 |
| **python-multipart**（锁：`0.0.32`） | `POST /upload` 多文件上传 |
| **Pydantic v2**（锁：`2.13.4`） | API 请求模型、`ToolStrategy` 结构化输出、Skill schema |

HTTP 面**仅**四个端点（无 SSE / session API）：

- `POST /upload`
- `POST /runs`
- `GET /runs/{run_id}`
- `POST /runs/{run_id}/cancel`

### 2.2 Agent 运行时

| 组件 | 用途 |
|---|---|
| **deepagents**（锁：`0.6.12`） | `create_deep_agent`、`HarnessProfile` / `register_harness_profile`、Composite 文件系统后端、SubAgent、权限 |
| **LangChain**（锁：`1.3.11`） | `init_chat_model`、middleware、`ToolStrategy` 结构化输出 |
| **langchain-core**（锁：`1.4.8`） | 消息 / 模型抽象 |
| **langchain-anthropic**（锁：`1.4.8`） | Anthropic 兼容 provider（生产走 MiniMax Anthropic 端点） |
| **LangGraph**（锁：`1.2.7`） | 图执行、`stream` v2、`RunControl` 协作 cancel、`GraphDrained` |
| **langgraph-checkpoint-sqlite**（锁：`3.1.0`） | `SqliteSaver` 持久化 checkpoint |
| LangGraph Store SQLite | `SqliteStore`（`/memories/` 持久记忆） |

生产 Brain 工厂：`DeepAgentsBrainFactory`（`runtime/agent.py`）→ `init_chat_model("anthropic:{MINIMAX_MODEL}", ...)` + `create_deep_agent(...)`。
`Brain` / `BrainFactory` 为 `typing.Protocol`；其余资源与工具用具体类 / callable。

### 2.3 本地数据与文件

| 组件 | 用途 |
|---|---|
| **SQLite（stdlib）** | `SqliteRunLedger`：`runs` 投影 + `run_events` append-only |
| **openpyxl**（锁：`3.1.5`） | Philips Tracking `.xlsx` 读取；Tecan 发票箱单 Excel 读写 |
| **oracledb**（锁：`3.4.2`） | Philips 主数据 Oracle 查询（可选 thick mode） |
| **requests**（锁：`2.34.2`） | MinerU HTTP 客户端（提交任务 / 轮询 / 下载） |
| **python-dotenv**（锁：`1.2.2`） | 加载 `backend/.env` |
| 本地文件系统 | `data/artifacts/` 上传与下载；`backend/skills/` 挂载为 `/skills/` |

### 2.4 测试形态

- 可执行 assert 脚本：`cd backend && python -m tests.<name>`（**非 pytest**）
- 本地回归与真实模型 / MinerU / Oracle / 外部 HTTP 集成通过环境变量开关分离

---

## 3. Key Dependencies

下列为 `pyproject.toml` 直接依赖及本项目内的主要用途（版本以 `uv.lock` 锁定值为准）。

| 包 | 锁版本（约） | 用途 |
|---|---|---|
| `deepagents` | 0.6.12 | Deep Agent 图、文件系统 backend、SubAgent、权限、profile |
| `fastapi` | 0.139.0 | HTTP API |
| `uvicorn` | 0.49.0 | 服务进程 |
| `langchain` | 1.3.11 | 聊天模型初始化、agent middleware、结构化输出策略 |
| `langchain-core` | 1.4.8 | 核心消息 / LLM 类型 |
| `langchain-anthropic` | 1.4.8 | Anthropic Messages API 兼容客户端 |
| `langgraph` | 1.2.7 | 流式执行、cancel drain、checkpointer/store 集成 |
| `langgraph-checkpoint-sqlite` | 3.1.0 | checkpoint SQLite 实现 |
| `oracledb` | 3.4.2 | Oracle 主数据查询（Philips `lookup_philips_wgq_master_data`） |
| `openpyxl` | 3.1.5 | Excel Tracking / 帝肯模板 |
| `requests` | 2.34.2 | MinerU REST |
| `python-multipart` | 0.0.32 | 上传 multipart |
| `python-dotenv` | 1.2.2 | `.env` 加载 |
| `httpx2` | 2.5.0 | 声明于 `pyproject.toml`；**当前业务源码无直接 import**（保留为依赖树/工具链项） |

传递依赖中常见但非直接声明的关键件：`pydantic`、`starlette`、`langgraph-checkpoint`、`sqlite-vec` 相关（checkpoint-sqlite 栈）等。

---

## 4. Configuration

### 4.1 配置文件路径

| 路径 | 作用 |
|---|---|
| `backend/pyproject.toml` | 项目元数据、依赖、setuptools 打包 |
| `backend/uv.lock` | 锁定依赖图 |
| `backend/.env.example` | 环境变量模板（无密钥） |
| `backend/.env` | 本地密钥与端点（git 忽略；**不要写入文档**） |
| `backend/data/` | 运行时数据根（与 CWD 无关，锚定 `backend/`） |
| `backend/log/oms_log.log` | OMS 旁路 JSONL 索引（非 run_events） |

`ResourceConfig` 默认路径（`runtime/resources.py`）：

| 属性 | 默认路径 |
|---|---|
| `data_dir` | `backend/data` |
| `run_db` | `data/dsagents_runs.db` |
| `store_db` | `data/dsagents_store.db` |
| `checkpoint_db` | `data/dsagents_checkpoints.db` |
| `artifacts_dir` | `data/artifacts` |
| `run_events_dir` | `data/internal/run-events` |
| `skills_dir` | `backend/skills` |

`.env` 加载点（`load_dotenv(BACKEND_ENV_PATH)`）：

- `runtime/agent.py`（`BACKEND_ENV_PATH = backend/.env`）
- `integrations/mineru.py`（同路径）

### 4.2 环境变量名（仅名称，不写值）

来自 `.env.example` 与生产代码：

**LLM / MiniMax（Anthropic-compatible）**

| 变量 | 使用位置 | 说明 |
|---|---|---|
| `MINIMAX_API_KEY` | `DeepAgentsBrainFactory` | API key |
| `MINIMAX_BASE_URL` | 同上 | 默认模板：`https://api.minimaxi.com/anthropic` |
| `MINIMAX_MODEL` | 同上 | 默认模板：`MiniMax-M3`；`init_chat_model(f"anthropic:{model}")` |

**MinerU 文档解析**

| 变量 | 使用位置 | 说明 |
|---|---|---|
| `MINERU_BASE_URL` | `integrations/mineru.py` | **必填**（缺失 `RuntimeError`） |
| `MINERU_BACKEND` | 同上 | **必填**（如 `vlm-engine`） |
| `MINERU_EFFORT` | 同上 | 可选；空串可接受 |
| `MINERU_TIMEOUT_SECONDS` | 同上 | **必填**（整型秒） |

**Oracle 主数据（Philips，可选）**

| 变量 | 使用位置 | 说明 |
|---|---|---|
| `ORACLE_DSN` | `lookup_philips_wgq_master_data` / `_oracle_data` | 与 user/password 同时非空才连接 |
| `ORACLE_USERNAME` | 同上 | |
| `ORACLE_PASSWORD` | 同上 | |
| `ORACLE_CLIENT_LIB_DIR` | `_init_oracle_client` | Instant Client 目录；非空则 thick mode |
| `ORACLE_TIMEOUT_SECONDS` | 同上 | 默认代码回落 `"30"` |

**真实集成 / 压测脚本（测试专用，非服务启动必需）**

示例（`tests/test_real_*.py`、`test_minimax_cache_baseline.py`）：
`DSAGENTS_API_BASE_URL`、`DSAGENTS_BASE_URL`、`DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST`、`DSAGENTS_PHILIPS_WGQ_SAMPLE_ROOT`、`DSAGENTS_REAL_PHILIPS_WGQ_TIMEOUT_SECONDS`、`DSAGENTS_REAL_PHILIPS_WGQ_POLL_SECONDS`、`DSAGENTS_RUN_REAL_MULTI_PDF_TEST`、`DSAGENTS_PDF_DIR`、`DSAGENTS_RUN_REAL_IMAGE_TEST`、`DSAGENTS_IMAGE_PATH` 等。

### 4.3 进程内 / 硬编码约定

- 时间戳：中国标准时间 **UTC+8** 本地 `YYYY-MM-DD HH:MM:SS`（ledger 与 OMS）
- 唯一固定 workflow 字面量：`philips_wgq_inbound_recognition`
- 主 Agent 名：`MAIN_AGENT_NAME = "dsagents-main"`
- 静态工具 **5** 个：`parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`、`save_tecan_extraction`、`generate_tecan_import`
- deepagents profile：`register_harness_profile("anthropic", HarnessProfile(general_purpose_subagent=...enabled=False))`
- 大事件外溢阈值：`SqliteRunLedger.max_inline_bytes = 262_144`
- MiniMax 计价估算仅 API 层（`api.py` 中 `_PRICEABLE_MODELS = {"MiniMax-M3"}`），与模型调用解耦

---

## 5. Platform Requirements

| 要求 | 说明 |
|---|---|
| OS | 开发与部署以 Windows 为主（PowerShell 脚本、路径处理）；路径逻辑使用 `pathlib`，Linux 亦可 |
| Python | ≥3.11，推荐 3.12 |
| 磁盘 | `backend/data/` 可写（三 SQLite + artifacts + 可选 run-events spill） |
| 网络 | 出站：MiniMax Anthropic 端点；MinerU HTTP；可选 Oracle TCP |
| Oracle thick mode | 可选。将 Instant Client 置于如 `backend/.oracle/instantclient/instantclient_19_31`（树 gitignore），设置 `ORACLE_CLIENT_LIB_DIR` 为该绝对路径；调用 `oracledb.init_oracle_client(lib_dir=...)`。**未设置 lib_dir 时不 init thick**；凭证缺失时工具降级为 problem，不崩服务 |
| 鉴权 | **当前无** HTTP API 鉴权 / CORS 中间件 |
| 多进程 | session 单飞锁与 `run_controls` 为**进程内**；多 worker 不保证跨进程 cancel / session 互斥 |

---

## 6. 发行包与入口摘要

```
发行名:     dsagents 0.1.0
描述:       Agent runtime for DeepAgents with pluggable document parsing.
入口模块:   api.py  →  FastAPI app
程序内:     runtime.create_harness / AgentResources
资源包:     runtime/, integrations/, skills/
Skill 资源: skills/philips-wgq-inbound-recognition/, skills/tecan-import/
Skill 代码: skills/philipswgqinboundrecognition/, skills/tecanimport/
```

**Skill 成对目录约定**：kebab-case 资源目录（`SKILL.md` / references / assets，挂载 `/skills/`）+ 可 import 的 Python 包；`package-data` 必须同步。

---

## 7. 常用命令

```powershell
cd backend
uv sync
uv run uvicorn api:app --host 0.0.0.0 --port 8500

python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
```

---

## 8. 映射边界说明

- 范围：`backend/` 源码、配置、测试；跳过 `.venv`、`__pycache__`、`dist`、`build` 内容与 `data/artifacts` 大文件内容。
- 未将 `build/lib/` 副本视为权威源码（权威为 `api.py`、`runtime/`、`integrations/`、`skills/`）。
- 外部系统契约细节见同目录 `INTEGRATIONS.md`。
---

*End of STACK.md*
