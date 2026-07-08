# SYSTEM_MAP

> 系统层跨子项目理解手册。本文件只描述系统形态、边界与读图指南；底层实现细节以 [`backend/.planning/codebase/`](../backend/.planning/codebase/) 为事实来源。
> 上游事实：[`ARCHITECTURE.md`](../ARCHITECTURE.md)、[`INTERFACES.md`](../INTERFACES.md)、[`AGENTS.md`](../AGENTS.md)。
> 本轮刷新已核对最近相关提交：`c8cc563`（run-ledger 时区统一与 schema 迁移）、`bc383ac`（测试端口配置）。

## 1. 系统目的和仓库形态

DsAgents 是一个 **agent 运行时底座**：把能力（Brain、执行器 Hands、工具）做成可插拔，而不绑定具体 runner、容器、模型或工作流。

- **形态**：单子项目仓库，唯一产品子项目是 `backend/`（扁平顶层模块，绝对导入 `from hands import ...`，包管理器 `uv`）。无前端子项目（当前源文档未确认任何前端代码归属本仓库）。
- **架构**：run-first。`session` 模块与 session 持久化层已移除（commit `8890292`）；run 是唯一的执行单位与查询单位，`run_events` 表 append-only，`runs` 表是事件投影出的快照。
- **短期上下文**：完全交给 LangGraph `checkpointer` + `thread_id=session_id`，仓库不再自建 session 事件回放。`session_id` 标识符保留，但用途已收窄为 checkpointer 键和进程内串行保护键，不再是一等持久化对象。
- **能力可插拔**：`Brain` / `BrainFactory` / `Hands` 是 `typing.Protocol`；工具保持普通 callable + `ToolCatalog`。默认装配从 `create_harness` 进入（`DeepAgentsBrainFactory` / `ToolStatusHands` / `default_tool_catalog()`），本地测试用 `FakeBrainFactory` 替换。运行时不写死具体模型实现。
- **入口形态**：HTTP（`POST /runs`，run-first 轮询模型，无 SSE）+ 程序内组合（`AgentResources` + `create_harness(...).execute_run(...)`）；无单函数 one-shot API。

详细运行时原则与维护规则见根级 [`docs/conventions.md`](../docs/conventions.md)（`AGENTS.md` 要求改动 backend 前必读）。

## 2. 子项目职责表

| 子项目 | 目录 | 当前职责 | 技术栈要点 | 边界 |
|--------|------|----------|------------|------|
| backend | `backend/` | run-first agent runtime：提交 run、轮询 run、上传文件、维护 LangGraph checkpointer/store 与本地 run ledger | Python `>=3.11,<4.0`；`uv` + setuptools（扁平顶层 `py-modules`）；FastAPI + uvicorn；`deepagents` + `langchain`/`langgraph` + `langchain-anthropic`；SQLite（标准库 `sqlite3` + LangGraph savers）；`requests`（MinerU） | 不提供 session 模块/表/事件回放；不提供 SSE；不提供鉴权/CORS；不绑定具体模型/工具实现 |

## 3. 跨子项目调用链和数据流

当前是**单子项目**，以下描述 backend 内部主调用链与外部 provider 边界（详细分层见 [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) §3）。

### 3.1 主调用链（HTTP 入口）

```text
POST /upload  multipart files[]
  └─ 保存到 /artifacts/uploads/<uuid>_<filename>，返回 file_path/name/mime_type/size

POST /runs  {messages, session_id?}
  ├─ session_id 为空 → 生成 uuid4().hex；run_id = uuid4().hex
  ├─ 进程内按 session_id 取 threading.Lock（单飞锁）；冲突 → 409
  ├─ resources.runs.create_run(run_id, session_id, input_messages_json)   # run_ledger
  ├─ 起 daemon 线程 → HarnessRuntime.execute_run(messages, session_id, run_id)
  └─ 立即返回 {run_id, session_id, status:"queued"}

HarnessRuntime.execute_run(...)
  ├─ emit status=running
  ├─ 归一化 content blocks：
  │    ├─ text     → 原样保留
  │    └─ artifact → "Uploaded artifact: /artifacts/uploads/..."
  ├─ brain.stream({"messages": normalized_messages},
  │                config={"configurable":{"thread_id":session_id}},
  │                stream_mode=["messages","custom","values"], version="v2")
  │    ├─ messages chunk → thinking / text_delta
  │    ├─ custom   chunk → tool_status（来自 ToolStatusMiddleware）
  │    └─ values   snapshot → tool_call / tool_result / assistant_message（同时更新 reply 候选）
  ├─ 成功 → emit status=succeeded(reply=...)
  └─ 异常 → emit status=failed(error=...)（真实错误透传，不吞）

GET /runs/{run_id}?after_event_id=N  → 读 runs 快照 + 增量 run_events + latest_content_event
```

- **事件获取靠轮询**，当前无 `StreamingResponse` / `text/event-stream`（[`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §1）。
- run 状态机：`queued → running → succeeded | failed`；启动恢复把遗留 `queued/running` 标 `failed("执行已中断，请重试")`。
- 程序内等价路径：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(...)` → `Iterator[RunEvent]`。

### 3.2 外部 provider 边界

| 边界 | 用途 | 集成方式 | 证据 |
|------|------|----------|------|
| Anthropic 兼容（生产） | LLM | `DeepAgentsBrainFactory` 用 `init_chat_model("anthropic:<MINIMAX_MODEL>", api_key=..., base_url=..., thinking={"type":"adaptive"})` → `ChatAnthropic`，注入 `create_deep_agent(...)`；实际端点可指向 MiniMax | `harness.py` |
| MinerU（内网 HTTP） | 文档解析（`parse_document` 工具） | `tools.py` 用 `requests` 调 `POST {MINERU_BASE_URL}/tasks`、轮询 `GET /tasks/{id}`、`GET /tasks/{id}/result` | `tools.py` |
| LangGraph savers | checkpointer / store 持久化 | `SqliteSaver` / `SqliteStore`（本地 SQLite） | `resources.py` |

provider/集成键名（不含值）见 [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §2/§5/§6 与 [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md) §5。

## 4. 接口边界

### 4.1 HTTP API 边界

| 方法 / 路径 | 行为 | 返回 |
|---|---|---|
| `POST /runs` | body `{messages, session_id?}`；`messages[]` 的 `content` 只接受 `text` / `artifact` blocks；同 session 已有运行中 run → `409` | `200 {run_id, session_id, status:"queued"}` |
| `GET /runs/{run_id}` | query `after_event_id?`；未知 run → `404` | `200 {run, events[], latest_content_event}` |
| `POST /upload` | multipart `files[]`；支持一个或多个文件；只保存不解析 | `200 {files:[{file_path,name,mime_type,size}]}` |

完整契约（请求/响应 JSON 形状、错误码）见 [`INTERFACES.md`](../INTERFACES.md) §1 与 [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §1。明确**已删除**的旧 session 接口清单亦见 [`INTERFACES.md`](../INTERFACES.md) §1。
`after_event_id` 只裁剪 `events[]`，不会影响 `latest_content_event`。

`api.py` 通过 `create_app(*, resource_config=None, harness_factory=create_harness)` 工厂构造 FastAPI 应用，支持注入测试用的 `ResourceConfig` 与 `Brain` 工厂（本地测试用 `FakeBrainFactory`）；模块级 `app = create_app()` 是生产装配。默认启动命令 `scripts/start-backend.bat`：`uv run uvicorn api:app --host 0.0.0.0 --port 8500`（端口与 `backend/tests/test_real_image_run.py` 的 `DEFAULT_BASE_URL` 一致）。

### 4.2 LLM provider 边界

- 生产 Brain 强耦合 Anthropic 客户端协议与 `thinking={"type":"adaptive"}`（`init_chat_model("anthropic:...")`）；环境变量 `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 由 `harness.py` 在导入时 `load_dotenv`。
- 本地测试 Brain `FakeBrain` 不触达真实 provider。

### 4.3 持久化边界

`backend/data/` 固定三条**逻辑** SQLite 通道（`runs`/`store`/`checkpoints`，文件按需创建），完整文件→通道→写入方映射、表结构与 `CompositeBackend` 路由规则详见 [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) §7。run ledger 时间字段统一为本机时区秒级文本（`YYYY-MM-DD HH:mm:ss`），并通过 `pragma user_version` + `_migrate` 做一次性幂等迁移（`assume_naive_utc=True`），把旧 UTC / naive UTC 文本平移到本机时区（commit `c8cc563`，已在 `test_run_ledger.py` 验证幂等）。

### 4.4 文件 / artifacts 边界

- 上传：`POST /upload` → `data/artifacts/uploads/<uuid>_<cleaned_name>`，返回虚拟路径 `/artifacts/uploads/...` 与元数据数组。
- 工具层 `tools._resolve_document_path` 把 `/artifacts/...` 解析回物理路径，并拒绝 `..` 越权。
- `parse_document` 默认输出到 `data/document_outputs/<stem>.md`。
- `artifact` block 是项目 API 语义；进入 Brain 前会被转成文本路径提示，再由 agent 通过 `read_file` / `parse_document` 处理。常见办公文件和任意图片都可以上传保存，但能否被解析或理解取决于 DeepAgents、MinerU 与模型能力。

### 4.5 鉴权 / 跨域边界（已确认缺失）

- `api.py` 未注册任何 auth middleware；三个端点全部匿名可调。
- 代码无 `CORSMiddleware` 注册 → 浏览器跨域实际不会被处理。

## 5. 依赖和归属规则

- **后端代码改动**归属 `backend/`：先更新 [`backend/.planning/codebase/`](../backend/.planning/codebase/) 对应事实文档，再视影响回看 [`ARCHITECTURE.md`](../ARCHITECTURE.md) / [`INTERFACES.md`](../INTERFACES.md) / 本文件（[`AGENTS.md`](../AGENTS.md) §关键约定明确此规则）。
- **文档分层归属**：
  - 根级 `AGENTS.md` / `ARCHITECTURE.md` / `INTERFACES.md` — 系统边界与导航。
  - `coding_maps/SYSTEM_MAP.md`（本文件）— 系统层跨子项目视图。
  - `docs/*.md` — 详细说明（项目总览、约定、命令、阅读顺序、backend 摘要）。
  - `backend/.planning/codebase/*` — backend 实现细节的事实来源。
- **包管理**：`uv`（非 pip）；安装 `cd backend && uv sync`；禁止 `pip install -e .` 绕过 `uv.lock`。
- **模块组织**：扁平顶层模块，新增顶层 `.py` 必须同步追加到 `pyproject.toml` 的 `py-modules`；无 `__init__.py` / `__main__.py`，无 `python -m backend.*`。

## 6. 按任务分类的阅读指南

| 任务类型 | 先读 |
|----------|------|
| 后端业务/API/存储/runner 修改 | [`docs/conventions.md`](../docs/conventions.md)（改动前必读）→ [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) + [`backend/.planning/codebase/STRUCTURE.md`](../backend/.planning/codebase/STRUCTURE.md) → 目标模块（如 `api.py` / `harness.py` / `run_ledger.py` / `resources.py`） |
| 改 run 状态/事件/持久化 | [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) §4/§7 → `backend/run_ledger.py` |
| 改模型流式行为 / Brain | [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §2 → `backend/harness.py` |
| 改工具 / MinerU 集成 | [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §6 → `backend/tools.py` |
| 改 HTTP 契约 | [`INTERFACES.md`](../INTERFACES.md) §1 → [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §1 → `backend/api.py` |
| 跨系统接口修改 | [`INTERFACES.md`](../INTERFACES.md)（provider/存储/artifacts 边界）→ 本文件 §4 |
| 文档维护 | [`AGENTS.md`](../AGENTS.md) §关键约定 + §5 文档同步规则 → [`backend/.planning/codebase/CONVENTIONS.md`](../backend/.planning/codebase/CONVENTIONS.md) §12 |
| 验证 / 测试策略 | [`backend/.planning/codebase/TESTING.md`](../backend/.planning/codebase/TESTING.md) |

完整任务→阅读顺序映射见根级 [`docs/reading-order.md`](../docs/reading-order.md)。

## 7. 集成风险检查清单和验证入口

提炼自 [`backend/.planning/codebase/CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md)（每条证据见该文档）。改动涉及以下面时按提示核对：

- **配置完整性**：`parse_document` 对 `MINERU_*` 必需键 fail-fast；本地/部署环境需按示例键名补齐，长期文档不记录私有值。
- **配置文档边界**：长期文档只保留配置键、消费者与归属规则，不抄录本地 `.env` 的真实值、连接串或服务地址。
- **文档同步**：四层文档需手工保持一致（根三件套 → 本文件 → `docs/*.md` → `backend/.planning/codebase/*`）。
- **私有配置**：`backend/.env` 被 `.gitignore` 排除且不应进入长期文档；provider key 经 `os.getenv` 直读，无统一脱敏或 secret manager 封装。
- **错误透传**：真实错误（含 provider 4xx/5xx body、MinerU 内网地址、文件路径）原样落 `runs.error` 与 `run_events.raw`，无脱敏护栏。`_error_text` 在 `api.py` 与 `harness.py` 重复定义。
- **并发语义**：单飞锁仅进程内 `threading.Lock`；多 worker（`uvicorn --workers N`）部署同 `session_id` 可跨进程并发，锁失效。`dsagents_runs.db` 每次操作短连接，未显式开 WAL。
- **运行时数据留存**：`run_events` 只增不删，raw chunk 长期留存（含模型输出与错误细节）；无 TTL/归档/压缩。
- **测试覆盖**：无 pytest 套件、无 CI、无 lint/type-check gate；回归按影响范围直接运行 `backend/tests/test_*.py` 脚本，普通本地脚本用 `FakeBrain`，不打真实 provider/MinerU。

**验证入口**：

- 仅文档变更：`git diff --check`。
- backend 代码变更：按影响范围运行对应脚本，例如 `cd backend && python -m tests.test_api`。
- HTTP 行为变更：已被 `backend/tests/test_api.py` 用 `TestClient` 覆盖，无需手动起服务。

## 8. 使用过的源文档索引

根级（系统边界与导航）：

- [`AGENTS.md`](../AGENTS.md)
- [`ARCHITECTURE.md`](../ARCHITECTURE.md)
- [`INTERFACES.md`](../INTERFACES.md)

子项目事实（backend 实现细节事实来源）：

- [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md)
- [`backend/.planning/codebase/STRUCTURE.md`](../backend/.planning/codebase/STRUCTURE.md)
- [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md)
- [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)
- [`backend/.planning/codebase/CONVENTIONS.md`](../backend/.planning/codebase/CONVENTIONS.md)
- [`backend/.planning/codebase/TESTING.md`](../backend/.planning/codebase/TESTING.md)
- [`backend/.planning/codebase/CONCERNS.md`](../backend/.planning/codebase/CONCERNS.md)

刷新时参考的旧版：`coding_maps/SYSTEM_MAP.md`（保留仍正确的调用链与接口面，改写/补全系统层视图）。
