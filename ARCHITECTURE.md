# ARCHITECTURE

> 系统级总览。底层实现事实以 [`backend/.planning/codebase/`](backend/.planning/codebase/) 为准；本文件只沉淀系统边界、子系统职责、理解路径与维护约定。
> 跨子项目系统视图见 [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md)。
> 本轮刷新（2026-07-08）已核对当前 HEAD `349357b`：最终 `assistant_message.payload` 可携带同条 AIMessage 的最后一个 `thinking` 文本。

## 1. 系统定位

`DsAgents` 是一个 **agent 运行时底座**：把能力做成可插拔，而不绑定具体 runner、容器、模型或工作流。

- **能力可插拔**：`Brain` / `BrainFactory` / `Hands` 是 `typing.Protocol`；工具保持普通 callable + `ToolCatalog`。默认装配从 `create_harness` 进入（`DeepAgentsBrainFactory` / `ToolStatusHands` / `default_tool_catalog()`），运行时不写死具体模型实现（本地测试用 `FakeBrainFactory` 替换）。
- **run-first**：`session` 模块与 session 持久化层已在 commit `8890292` 移除；run 是唯一的执行单位与查询单位，`run_events` 表 append-only，`runs` 表是事件投影出的快照。
- **短期上下文**：完全交给 LangGraph `checkpointer` + `thread_id=session_id`，仓库不再自建 session 事件回放。`session_id` 标识符保留，但用途已收窄为 checkpointer 键和进程内串行保护键，不再是一等持久化对象。
- **入口形态**：HTTP（`POST /runs`，轮询模型，无 SSE）+ 程序内组合（`AgentResources` + `create_harness(...).execute_run(...)`）；无单函数 one-shot API。
- **单子项目**：仓库当前只有 `backend/` 一个产品子项目（扁平顶层模块、绝对导入、`uv` 包管理）；当前源文档未确认任何前端子项目归属本仓库。

## 2. 子系统职责

| 子项目 | 目录 | 当前职责 | 边界（不做什么） |
|--------|------|----------|------------------|
| backend | `backend/` | run-first agent runtime：提交 run、轮询 run、上传文件、维护 LangGraph checkpointer/store 与本地 run ledger；能力层（Brain/Hands/Tools）可插拔 | 不提供 session 模块/表/事件回放；不提供 SSE；不提供鉴权/CORS；不绑定具体模型/工具实现；不提供跨进程锁或队列 |

backend 内部架构、目录组织、配置加载、事件源模型等实现事实见 [`backend/.planning/codebase/ARCHITECTURE.md`](backend/.planning/codebase/ARCHITECTURE.md) 与 [`backend/.planning/codebase/STRUCTURE.md`](backend/.planning/codebase/STRUCTURE.md)。

## 3. 推荐理解路径

按任务类型的阅读顺序见 [`docs/reading-order.md`](docs/reading-order.md)（权威）与 [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md) §6（系统层视图）。

系统级导航要点：理解系统边界与接口从本文件 → [`INTERFACES.md`](INTERFACES.md) → [`coding_maps/SYSTEM_MAP.md`](coding_maps/SYSTEM_MAP.md)；理解子系统职责从本文件 §2；理解稳定目录职责从本文件 §4。

## 4. 稳定目录职责（backend 顶层模块）

`backend/` 是扁平顶层模块（非 Python 包）。顶层 `.py` 的系统级职责概览（不展开实现，详见 [`backend/.planning/codebase/STRUCTURE.md`](backend/.planning/codebase/STRUCTURE.md)）：

| 模块 | 系统级职责 |
|------|-----------|
| `api.py` | FastAPI HTTP 适配层（run-first 三端点：`POST /runs` / `GET /runs/{run_id}` / `POST /upload` + 同 session 单飞锁 + 启动恢复） |
| `harness.py` | run 执行核心 + Brain/Hands/Tools 装配 + 默认工厂 `create_harness` |
| `hands.py` | 执行器/中间件抽象（`Hands` Protocol + `ToolStatusMiddleware`） |
| `resources.py` | 资源装配器（`AgentResources`：run ledger + checkpointer + store + `CompositeBackend`） |
| `run_ledger.py` | SQLite run ledger（`runs` + `run_events`，事件源模型 + 大 payload 外溢） |
| `tools.py` | 工具抽象 + 默认业务工具 `parse_documents`（批量调 MinerU，保存 task 级 ZIP）与 `extract_archives`（解压 ZIP） |

固定数据目录 `backend/data/`（路径由 `ResourceConfig` 决定，与 CWD 无关）：三条逻辑 SQLite 通道（文件按需创建）+ `artifacts/`（`uploads/` 上传落地、`downloads/` 文档解析产物、`run-events/` 大 payload 外溢）。

## 5. 系统层面维护约定

- **改动归属**：改 backend 代码后，**先更新** [`backend/.planning/codebase/`](backend/.planning/codebase/) 对应事实文档，**再视影响回看**根级 `ARCHITECTURE.md` / `INTERFACES.md` 与 `coding_maps/SYSTEM_MAP.md`（详见 [`AGENTS.md`](AGENTS.md) 关键约定）。
- **文档分层维护**：根级三件套（系统边界与导航）→ `coding_maps/SYSTEM_MAP.md`（系统层跨子项目视图）→ `docs/*.md`（详细说明）→ `backend/.planning/codebase/*`（实现事实来源）。四层需手工保持一致。
- **系统级文档不堆实现**：本文件与 `INTERFACES.md` 只描述系统边界与接口契约；具体表结构、主调用链细节、配置键清单归 backend 事实文档。

## 6. 关键约束

- **run-first**：无 `session.py`、无 session 表、无 `context_window`、无 `RemoveMessage(REMOVE_ALL_MESSAGES)`、无 `run_turn`/`stream_turn`；旧 `from session import run_session` 已删除。
- **事件规范化**：公开 run event type 固定为 `status` / `thinking` / `text_delta` / `assistant_message` / `tool_call` / `tool_status` / `tool_result`；LangGraph `values` snapshot 只保留在 `raw` 中，最终 `assistant_message.payload` 可带 `thinking` 与 `text`。
- **扁平顶层模块 + 绝对导入**：`backend/` 不是包，无 `__init__.py` / `__main__.py`；模块内一律 `from harness import ...` 这类绝对导入。新增顶层 `.py` 必须同步追加到 `pyproject.toml` 的 `py-modules`；无 `python -m backend.*`。
- **`uv` 包管理**：安装 `cd backend && uv sync`；禁止 `pip install -e .` 绕过 `uv.lock`。
- **`.env` 加载**：由 `harness.py` 与 `tools.py` 在导入时 `load_dotenv(Path(__file__).with_name(".env"))`（删除 `session.py` 后保留配置加载点）。
- **run ledger 时间戳**：统一写本机时区秒级文本 `YYYY-MM-DD HH:mm:ss`；首次进入 `AgentResources` 时由 `_migrate`（`pragma user_version`）把旧 UTC / naive UTC 文本幂等平移到本机时区（commit `c8cc563`）。

## 7. 当前风险（系统级）

提炼自 [`backend/.planning/codebase/CONCERNS.md`](backend/.planning/codebase/CONCERNS.md)（每条证据见该文档），改动涉及以下面时按提示核对：

- **配置完整性**：`parse_documents` 在存在可提交文件时对 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 必需键 fail-fast；`MINERU_EFFORT` 可留空；本地/部署环境需按示例键名补齐，长期文档不记录私有值。
- **配置文档边界**：长期文档只记录配置键与消费者，不抄录本地 `.env` 中的真实值、连接串或服务地址。
- **run 锁单进程语义**：单飞锁仅进程内 `threading.Lock`；多 worker（`uvicorn --workers N`）部署同 `session_id` 可跨进程并发，锁失效。
- **文档同步**：四层文档手工保持一致。
- **运行时数据留存**：`run_events` 只增不删，raw chunk 长期留存（含模型输出与错误细节）；无 TTL/归档/压缩。
- **错误透传**：真实错误（含 provider 4xx/5xx body、MinerU 内网地址、文件路径）原样落 `runs.error` 与 `run_events.raw`，无脱敏护栏。
- **测试覆盖**：无 pytest 套件、无 CI；回归按影响范围直接运行 `backend/tests/test_*.py` 脚本，普通本地脚本用 `FakeBrain` 替身，不打真实 provider/MinerU。
