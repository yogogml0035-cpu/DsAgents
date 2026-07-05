# STACK

> 技术栈快照。所有事实均基于当前代码（`pyproject.toml` + backend 顶层模块）核对。
> 标注「需确认」表示存在但代码层无引用、用途待核实。

## 1. 运行时 / 语言

| 项 | 值 | 证据 |
|---|---|---|
| 语言 | Python | `pyproject.toml` |
| 版本约束 | `>=3.11,<4.0` | `requires-python` |
| 包管理器 | `uv`（非 pip） | 仓库根存在 `uv.lock` |
| 构建 backend | `setuptools>=68`（`setuptools.build_meta`） | `[build-system]` |
| 包名 / 版本 | `dsagents` / `0.1.0` | `[project]` |

## 2. 构建方式（扁平顶层模块）

`pyproject.toml` 配置：

```toml
[tool.setuptools]
package-dir = {"" = "."}
py-modules = ["api", "hands", "harness", "resources", "run_ledger", "tools", "self_check"]
```

- `backend/` 内的 `.py` 直接作为顶层模块安装。
- 模块内部使用绝对导入（如 `from hands import ...`、`from resources import AgentResources`），安装后同样可用。
- `package-dir = {"" = "."}` 把仓库 `backend/` 目录映射为导入根。

## 3. 核心依赖及用途

| 依赖 | 版本约束 | 用途 | 证据 |
|---|---|---|---|
| `deepagents` | `>=0.6.12` | Agent 主体；`create_deep_agent(...)` 装配可流式 agent | `harness.py`、`resources.py`（`CompositeBackend` 等） |
| `fastapi` | `>=0.116.1` | HTTP 框架；`create_app()` → `FastAPI(lifespan=...)` | `api.py` |
| `langchain` | `>=1.3.11` | `init_chat_model`、`AgentMiddleware`、`ToolCallRequest` | `harness.py`、`hands.py` |
| `langchain-anthropic` | `>=1.4.8` | LLM provider（Anthropic 兼容客户端，实际可指向 MiniMax 端点） | 经 `init_chat_model("anthropic:...")` 间接使用；自检断言 `ChatAnthropic` |
| `langchain-core` | `>=1.4.8` | `BaseChatModel`、`AIMessage` / `AIMessageChunk` | `harness.py`、`self_check.py` |
| `langgraph` | `>=1.2.7` | Agent 编排 / 流式 API；`get_stream_writer` | `hands.py`；harness 调用 `brain.stream(..., version="v2")` |
| `langgraph-checkpoint-sqlite` | `>=3.1.0` | LangGraph checkpointer（`SqliteSaver`） | `resources.py` |
| `python-multipart` | `>=0.0.20` | `POST /files` 文件上传解析（`UploadFile`） | `api.py` |
| `python-dotenv` | `>=1.2.2` | `.env` 加载 | `harness.py`、`tools.py` 各自 `load_dotenv(...)` |
| `requests` | `>=2.34.2` | 外部 HTTP（MinerU 任务提交/轮询/取结果） | `tools.py` |
| `uvicorn` | `>=0.35.0` | ASGI 服务器（运行 FastAPI app） | 依赖声明；`api.py` 未直接 import，由外部 `uvicorn` 命令拉起 |

## 4. 本地持久化（存储栈）

| 组件 | 类型 | 落点 | 证据 |
|---|---|---|---|
| `SqliteRunLedger` | 标准库 `sqlite3` | `data/dsagents_runs.db` | `resources.py`、`run_ledger.py` |
| `SqliteStore` | LangGraph store | `data/dsagents_store.db` | `resources.py` |
| `SqliteSaver` | LangGraph checkpointer | `data/dsagents_checkpoints.db` | `resources.py` |
| `CompositeBackend` | `deepagents.backends` | 路由 `/memories/` `/conversation_history/` `/logs/` → `StoreBackend`；`/artifacts/` `/large_tool_results/` → `FilesystemBackend`；默认 `StateBackend` | `resources.py` |
| 大 run event 外溢 | 文件系统 | `data/artifacts/run-events/*.json` | `run_ledger.py`（`max_inline_bytes=262_144`） |
| 上传文件 | 文件系统 | `data/artifacts/uploads/` | `api.py` |

数据目录固定为 `backend/data/`（`resources.py` 中 `_BACKEND_DIR = Path(__file__).resolve().parent`），与 CWD 无关。

## 5. LLM Provider

| Provider | 集成方式 | 证据 |
|---|---|---|
| Anthropic 兼容（生产） | `init_chat_model("anthropic:<MINIMAX_MODEL>", api_key=..., base_url=..., thinking={"type":"adaptive"})` → `ChatAnthropic`；由 `DeepAgentsBrainFactory` 注入 `create_deep_agent(model=...)` | `harness.py` |
| `FakeBrain`（自检） | `_FakeBrain` / `_FakeBrainFactory`，模拟 `stream(...)` 产出 `values/messages/custom` chunk | `self_check.py` |

## 6. 配置加载

`.env` 由以下模块在导入时加载（`load_dotenv(Path(__file__).with_name(".env"))`）：

- `harness.py`
- `tools.py`

`api.py`、`resources.py`、`run_ledger.py`、`hands.py` 不直接 `load_dotenv`。

## 7. 运行时并发模型

| 项 | 值 |
|---|---|
| HTTP handler | 同步 `def`（FastAPI 同步路由跑在线程池） |
| 后台执行 | `threading.Thread(daemon=True)`，per-run |
| 单飞锁 | per-`session_id` 的 `threading.Lock`，由 `registry_lock`（另一把 `threading.Lock`）保护注册表 |
| LangGraph 流 | classic `stream(..., version="v2")`，`stream_mode=["messages","custom","values"]` |
| 无 | Redis / DB 锁 / 消息队列 / worker 恢复器（仅启动时 `fail_incomplete_runs` 把遗留 `queued/running` 标 `failed`） |

## 8. 需确认（仓库存在但代码层无引用）

| 项 | 状态 |
|---|---|
| `instantclient/`（Oracle Instant Client 19.31，Windows 二进制：`oci.dll`、`ojdbc8.jar` 等） | backend Python 代码无 import / 无 `ORACLE_CLIENT_LIB_DIR` 读取；`.env.example` 含 `ORACLE_*` 键但无代码消费 → 疑似遗留 / 计划中 |
| `.env.example` 中的 `DEEPSEEK_*`、`LANGSMITH_*`、`CORS_ORIGINS` | backend 代码无引用 → 需确认是否由前端 / 部署脚本使用 |
| `uvicorn` | 仅作为依赖声明存在，`api.py` 未 `import uvicorn`，运行方式需确认（外部 `uvicorn api:app` 命令） |
