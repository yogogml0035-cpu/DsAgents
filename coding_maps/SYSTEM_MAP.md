# SYSTEM_MAP

> 系统层跨子项目理解手册。本文件只描述系统形态、边界与读图指南；底层实现细节以 [`backend/.planning/codebase/`](../backend/.planning/codebase/) 为事实来源。
> 上游事实：[`ARCHITECTURE.md`](../ARCHITECTURE.md)、[`INTERFACES.md`](../INTERFACES.md)、[`AGENTS.md`](../AGENTS.md)。

## 1. 系统目的和仓库形态

DsAgents 是一个 **agent 运行时底座**：把能力（Brain、执行器 Hands、工具）做成可插拔，而不绑定具体 runner、容器、模型或工作流。

- **形态**：单子项目仓库，唯一产品子项目是 `backend/`（扁平顶层模块，绝对导入 `from hands import ...`，包管理器 `uv`）。无前端子项目（当前源文档未确认任何前端代码归属本仓库）。
- **架构**：run-first。`session` 模块与 session 持久化层已移除（commit `8890292`）；run 是唯一的执行单位与查询单位，`run_events` 表 append-only，`runs` 表是事件投影出的快照。
- **短期上下文**：完全交给 LangGraph `checkpointer` + `thread_id=session_id`，仓库不再自建 session 事件回放。`session_id` 标识符保留，但用途已收窄为 checkpointer 键和进程内串行保护键，不再是一等持久化对象。
- **能力可插拔**：`Brain` / `BrainFactory` / `Hands` 是 `typing.Protocol`；工具保持普通 callable + `ToolCatalog`。默认装配从 `create_harness` 进入（`DeepAgentsBrainFactory` / `ToolStatusHands` / `default_tool_catalog()`），自检用 `_FakeBrainFactory` 替换。运行时不写死具体模型实现。
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
POST /runs  {message, session_id?}
  ├─ session_id 为空 → 生成 uuid4().hex；run_id = uuid4().hex
  ├─ 进程内按 session_id 取 threading.Lock（单飞锁）；冲突 → 409
  ├─ resources.runs.create_run(run_id, session_id, input_message)   # run_ledger
  ├─ 起 daemon 线程 → HarnessRuntime.execute_run(message, session_id, run_id)
  └─ 立即返回 {run_id, session_id, status:"queued"}

HarnessRuntime.execute_run(...)
  ├─ emit status=running
  ├─ brain.stream({"messages":[{role:user,content:message}]},
  │                config={"configurable":{"thread_id":session_id}},
  │                stream_mode=["messages","custom","values"], version="v2")
  │    ├─ messages chunk → thinking / text_delta
  │    ├─ custom   chunk → tool_status（来自 ToolStatusMiddleware）
  │    └─ values   chunk → values（末位 assistant 文本作 reply）
  ├─ 成功 → emit status=succeeded(reply=...)
  └─ 异常 → emit status=failed(error=...)（真实错误透传，不吞）

GET /runs/{run_id}?after_event_id=N  → 读 runs 快照 + 增量 run_events
POST /files                          → /artifacts/uploads/<uuid>_<filename>
```

- **事件获取靠轮询**，当前无 `StreamingResponse` / `text/event-stream`（[`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §1）。
- run 状态机：`queued → running → succeeded | failed`；启动恢复把遗留 `queued/running` 标 `failed("执行已中断，请重试")`。
- 程序内等价路径（自检走这条路）：`AgentResources(config)` → `create_harness(resources)` → `harness.execute_run(...)` → `Iterator[RunEvent]`。

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
| `POST /runs` | body `{message, session_id?}`；同 session 已有运行中 run → `409` | `200 {run_id, session_id, status:"queued"}` |
| `GET /runs/{run_id}` | query `after_event_id?`；未知 run → `404` | `200 {run, events[]}` |
| `POST /files` | multipart `file`；落到 `data/artifacts/uploads/` | `200 {file_path:"/artifacts/uploads/..."}` |

完整契约（请求/响应 JSON 形状、错误码）见 [`INTERFACES.md`](../INTERFACES.md) §1 与 [`backend/.planning/codebase/INTEGRATIONS.md`](../backend/.planning/codebase/INTEGRATIONS.md) §1。明确**已删除**的旧 session 接口清单亦见 [`INTERFACES.md`](../INTERFACES.md) §1。

### 4.2 LLM provider 边界

- 生产 Brain 强耦合 Anthropic 客户端协议与 `thinking={"type":"adaptive"}`（`init_chat_model("anthropic:...")`）；环境变量 `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` 由 `harness.py` 在导入时 `load_dotenv`。
- `DEEPSEEK_*` 在 `.env` 配置但代码零引用（死配置）。
- 自检 Brain `_FakeBrain` 不触达真实 provider。

### 4.3 持久化边界

`backend/data/` 固定三条**活跃** SQLite 通道（`runs`/`store`/`checkpoints`），完整文件→通道→写入方映射、表结构与 `CompositeBackend` 路由规则详见 [`backend/.planning/codebase/ARCHITECTURE.md`](../backend/.planning/codebase/ARCHITECTURE.md) §7。

- **需确认（遗留物）**：`backend/data/dsagents_sessions.db` 与 `backend/data/artifacts/session-events/` 在当前代码中零引用，属旧 session 时代孤儿文件。

### 4.4 文件 / artifacts 边界

- 上传：`POST /files` → `data/artifacts/uploads/<uuid>_<cleaned_name>`，返回虚拟路径 `/artifacts/uploads/...`。
- 工具层 `tools._resolve_document_path` 把 `/artifacts/...` 解析回物理路径，并拒绝 `..` 越权。
- `parse_document` 默认输出到 `data/document_outputs/<stem>.md`。

### 4.5 鉴权 / 跨域边界（已确认缺失）

- `api.py` 未注册任何 auth middleware；三个端点全部匿名可调。
- `.env` 有 `CORS_ORIGINS`，但代码无 `CORSMiddleware` 注册 → 浏览器跨域实际不会被处理（死配置）。

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

- **instantclient 入库（高危）**：`backend/instantclient/`（Oracle Instant Client 19.31，约 109MB）被 git 跟踪，但 backend 代码零引用（无 `oracledb` import、不读 `ORACLE_*`）。需确认是否应移出仓库。
- **配置漂移（高危）**：`tools.py` 读 `MINERU_EFFORT`，但 `backend/.env` 缺该键（仅 `.env.example` 有 `MINERU_EFFORT=high`）→ 真实调用 `parse_document` 立即 `RuntimeError`。`DEEPSEEK_*` / `ORACLE_*` / `LANGSMITH_*` / `CORS_ORIGINS` 均为代码零引用的死配置。
- **文档同步**：四层文档需手工保持一致（根三件套 → 本文件 → `docs/*.md` → `backend/.planning/codebase/*`）。已知漂移源：`Study/` 全套以 session 为事实源（已被 `.gitignore` 忽略）。
- **明文密钥**：`backend/.env` 含真实密钥（虽 `.gitignore` 排除、git 未跟踪，但已落工作树）；`provider key` 经 `os.getenv` 直读无脱敏护栏。建议轮换并改用 secret manager。
- **错误透传**：真实错误（含 provider 4xx/5xx body、MinerU 内网地址、文件路径）原样落 `runs.error` 与 `run_events.raw`，无脱敏护栏。`_error_text` 在 `api.py` 与 `harness.py` 重复定义。
- **并发语义**：单飞锁仅进程内 `threading.Lock`；多 worker（`uvicorn --workers N`）部署同 `session_id` 可跨进程并发，锁失效。`dsagents_runs.db` 每次操作短连接，未显式开 WAL。
- **运行时数据留存**：`run_events` 只增不删，raw chunk 长期留存（含模型输出与错误细节）；无 TTL/归档/压缩。
- **测试覆盖**：无 pytest 套件、无 CI、无 lint/type-check gate；回归靠 `python backend/self_check.py`（用 `_FakeBrain`，不打真实 provider/MinerU）。

**验证入口**：

- 仅文档变更：`git diff --check`。
- backend 代码变更：`python backend/self_check.py`，必须看到结尾 `self-check passed`（通过判定字符串契约）。
- HTTP 行为变更：已被 `self_check` 的 `_check_api` / `_check_startup_recovery` 用 `TestClient` 覆盖，无需手动起服务。

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
