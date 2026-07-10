# STACK

> 技术栈快照。所有事实均基于当前代码（`pyproject.toml` + backend 顶层模块）核对。
> 本轮刷新（2026-07-10）已核对当前工作树：`openpyxl`/`oracledb` 已锁定，四个业务模块已纳入 `py-modules`，DeepAgents 默认装配已增加 Skills/Subagents。
> 本文件优先记录代码与仓库配置可直接证实的事实；运行命令优先以仓库内 `scripts/start-backend.bat` 与测试默认值为准。

## 1. 运行时 / 语言

| 项 | 值 | 证据 |
|---|---|---|
| 语言 | Python | `pyproject.toml` |
| 版本约束 | `>=3.11,<4.0` | `requires-python` |
| 包管理器 | `uv`（非 pip） | `backend/uv.lock` |
| 构建 backend | `setuptools>=68`（`setuptools.build_meta`） | `[build-system]` |
| 包名 / 版本 | `dsagents` / `0.1.0` | `[project]` |

## 2. 构建方式（扁平顶层模块）

`pyproject.toml` 配置：

```toml
[tool.setuptools]
package-dir = {"" = "."}
py-modules = ["api", "artifact_names", "hands", "harness", "philips_wgq_import", "resources", "run_ledger", "subagents", "tecan_import", "tools", "workflow_artifacts"]
```

- `backend/` 内的 `.py` 直接作为顶层模块安装。
- 模块内部使用绝对导入（如 `from hands import ...`、`from resources import AgentResources`），安装后同样可用。
- `package-dir = {"" = "."}` 把仓库 `backend/` 目录映射为导入根。

## 3. 核心依赖及用途

| 依赖 | 版本约束 | 用途 | 证据 |
|---|---|---|---|
| `deepagents` | `>=0.6.12`（lock: `0.6.12`） | Agent 主体；`create_deep_agent(...)` 装配 Skills、临时 subagents、权限与可流式 agent | `harness.py`、`subagents.py`、`resources.py` |
| `fastapi` | `>=0.116.1` | HTTP 框架；`create_app()` → `FastAPI(lifespan=...)` | `api.py` |
| `httpx2` | `>=2.5.0` | `fastapi.testclient.TestClient` 的 HTTP 客户端传输层，用于本地 HTTP 断言且避免 `starlette.testclient` 对 `httpx` 的弃用警告 | `backend/tests/test_api.py`、`backend/tests/test_support.py` |
| `langchain` | `>=1.3.11` | `init_chat_model`、`AgentMiddleware`、`ToolCallRequest` | `harness.py`、`hands.py` |
| `langchain-anthropic` | `>=1.4.8` | LLM provider（Anthropic 兼容客户端，实际可指向 MiniMax 端点） | 经 `init_chat_model("anthropic:...")` 间接使用；测试脚本断言 `ChatAnthropic` |
| `langchain-core` | `>=1.4.8` | `BaseChatModel`、`AIMessage` / `AIMessageChunk`；测试最终 AIMessage 的 `thinking`/`text` block 载荷 | `harness.py`、`backend/tests/test_support.py`、`backend/tests/test_harness.py` |
| `langgraph` | `>=1.2.7` | Agent 编排 / 流式 API；`get_stream_writer` | `hands.py`；harness 调用 `brain.stream(..., version="v2")` |
| `langgraph-checkpoint-sqlite` | `>=3.1.0` | LangGraph checkpointer（`SqliteSaver`） | `resources.py` |
| `openpyxl` | `>=3.1,<4`（lock: `3.1.5`） | 读取 tracking/订单/信息表并基于模板生成 Philips/Tecan Excel | `philips_wgq_import.py`、`tecan_import.py` |
| `oracledb` | `>=3,<4`（lock: `3.4.2`） | Philips 申报/法定单位可选查询；运行时延迟 import，失败走人工校验 | `philips_wgq_import.py` |
| `python-multipart` | `>=0.0.20` | `POST /upload` 多文件上传解析（`UploadFile`） | `api.py` |
| `python-dotenv` | `>=1.2.2` | `.env` 加载 | `harness.py`、`tools.py` 各自 `load_dotenv(...)` |
| `requests` | `>=2.34.2` | 外部 HTTP（MinerU 任务提交/轮询/取结果） | `tools.py` |
| `uvicorn` | `>=0.35.0` | ASGI 服务器（运行 FastAPI app） | 依赖声明；`api.py` 未直接 import，由外部 `uvicorn` 命令拉起 |

## 4. 本地持久化（存储栈）

| 组件 | 类型 | 落点 | 证据 |
|---|---|---|---|
| `SqliteRunLedger` | 标准库 `sqlite3` | `data/dsagents_runs.db` | `resources.py`、`run_ledger.py` |
| `SqliteStore` | LangGraph store | `data/dsagents_store.db` | `resources.py` |
| `SqliteSaver` | LangGraph checkpointer | `data/dsagents_checkpoints.db` | `resources.py` |
| `CompositeBackend` | `deepagents.backends` | `/memories/` → store；`/artifacts/`、`/large_tool_results/` → artifact 文件系统；`/skills/` → 仓库 Skill 目录；其余 → state | `resources.py` |
| 大 run event 外溢 | 文件系统 | `data/internal/run-events/*.json` | `run_ledger.py`（`max_inline_bytes=262_144`，lazy mkdir） |
| 上传文件（上传源） | 文件系统 | `data/artifacts/uploads/` | `api.py` |
| 工具/业务产物 | 文件系统 | `data/artifacts/downloads/` | MinerU JSON/ZIP、解压目录、immutable 业务 JSON 与 Excel |

数据目录固定为 `backend/data/`（`resources.py` 中 `_BACKEND_DIR = Path(__file__).resolve().parent`），与 CWD 无关；其中 `dsagents_runs.db`、`artifacts/uploads/`、`artifacts/downloads/` 这类路径会在首次运行对应流程时按需创建（`downloads/` 由 `tools.py` 在落盘时 lazy mkdir）。

## 5. LLM Provider

| Provider | 集成方式 | 证据 |
|---|---|---|
| Anthropic 兼容（生产） | `init_chat_model("anthropic:<MINIMAX_MODEL>", api_key=..., base_url=..., thinking={"type":"adaptive"})` → `ChatAnthropic`；由 `DeepAgentsBrainFactory` 注入 `create_deep_agent(model=...)` | `harness.py` |
| `FakeBrain`（本地测试） | `FakeBrain` / `FakeBrainFactory`，模拟 `stream(...)` 产出 `values/messages/custom` chunk，并覆盖 snapshot → `tool_call` / `tool_result` / `assistant_message` 派生 | `backend/tests/test_support.py` |

锁定的 `deepagents==0.6.12` 已确认支持 `skills`、`subagents`、`permissions`、`response_format` 与 `name`，但不支持当前官方文档出现的 `harness_profile` 调用参数。当前实现按该版本公开 API 使用 `register_harness_profile("anthropic", ...)` 禁用自动 general-purpose subagent，不增加未来兼容层。

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
| Subagent | 四个临时声明式 extractor；主 agent 可在同一模型回合发出并行 `task` 调用，subagent messages/todos/structured response 与父状态隔离 |
| 无 | Redis / DB 锁 / 消息队列 / worker 恢复器（仅启动时 `fail_incomplete_runs` 把遗留 `queued/running` 标 `failed`） |

## 8. 运行命令说明（代码外约定）

| 项 | 说明 |
|---|---|
| `uvicorn` | 仅作为依赖声明存在，`api.py` 不直接 `import uvicorn`；由外部命令拉起 `api:app` |
| 启动命令 | `scripts/start-backend.bat`：`cd backend` 后 `uv run uvicorn api:app --host 0.0.0.0 --port 8500` |
| 默认端口 | `8500`（与 `backend/tests/test_real_image_run.py` 的 `DEFAULT_BASE_URL = "http://127.0.0.1:8500"` 一致） |
