# INTERFACES — 接口边界与调用关系

> 根级系统级接口文档。承接跨子项目与外部系统的接口边界、调用关系与排查建议。实现细节见 `backend/.planning/codebase/INTEGRATIONS.md`，系统总图见 `coding_maps/SYSTEM_MAP.md`。说明性文字为简体中文，代码标识符/路径/命令/配置键保留原文。

## 1. 已确认接口边界

### 1.1 对外 CLI 接口（当前唯一的对外接口）

| 入口 | 命令 | 说明 |
|---|---|---|
| 真实会话 | `python -m backend "<message>" --session-id <可选>` | 依赖真实 DeepAgents + 模型（`DSAGENTS_MODEL`，缺省 `openai:gpt-5.5`）+ 可达 MinerU |
| 离线自检 | `python -m backend.self_check` | 无需网络/LLM，覆盖 Session/Hands/Harness/大事件落盘（`_FakeBrain`） |

> 当前**无 Web 前端、无 HTTP 服务层、无 REST API**。CLI 是唯一对外接口。

### 1.2 五大模块的稳定接口（Protocol 契约）

| 边界 | Protocol / 公共面 | 关键方法 | 默认实现 |
|---|---|---|---|
| Session | `SessionStore` | `ensure_session` / `get_session` / `get_events` / `emit_event` / `context_window` | `SqliteSessionStore` |
| Harness | `Brain` / `BrainFactory` | `Brain.invoke` / `BrainFactory.create` | `DeepAgentsBrainFactory`（内部 `create_deep_agent`） |
| Hands | `Hands` | `middleware(session_id)` | `TraceHands`（产出 `TraceMiddleware(AgentMiddleware)`） |
| Tools | `ToolHandler = Callable[..., Any]` / `ToolCatalog` | `as_list()` | `default_tool_catalog()`（含 `parse_document_with_mineru`） |
| Resources | `AgentResources` / `ResourceConfig` | 上下文管理器，装配 store/checkpointer/backend | `CompositeBackend`（State/Store/Filesystem） |

实现可替换；`HarnessRuntime` 与 `AgentResources` 只依赖 Protocol，不依赖具体后端。

### 1.3 MinerU 外部服务接口（异步任务 API）

目标：`http://10.11.0.110:6006`（`MINERU_BASE_URL`，固定）。

| 步骤 | 方法 + 路径 | 关键点 |
|---|---|---|
| 提交 | `POST /tasks` | multipart `files` 上传；表单固定 `backend=hybrid-engine`、`effort=high`、`return_md=true`、`response_format_zip=false`；超时 60s |
| 轮询 | `GET /tasks/{task_id}` | 状态键 `status`/`state`（忽略大小写）；成功/失败状态集见 `backend/.planning/codebase/INTEGRATIONS.md`；轮询间隔 2s，总超时 900s |
| 取结果 | `GET /tasks/{task_id}/result` | 提取 markdown 文本写入本地 `.md`；超时 120s |

约束：`backend=hybrid-engine` 与 `effort=high` 在本里程碑**固定不可由用户配置**（`AGENTS.md` Runtime Rules）。

### 1.4 存储与产物接口

| 路由 / 位置 | 后端 | 用途 |
|---|---|---|
| `default` | `StateBackend` | 文件系统默认（瞬时状态） |
| `/memories/` `/conversation_history/` `/logs/` | `StoreBackend`（`SqliteStore`，`data/dsagents_store.db`） | 持久历史/记忆/日志 |
| `/artifacts/` `/large_tool_results/` | `FilesystemBackend(virtual_mode=True)`（`data/artifacts/`） | 大产物与大 tool/model 日志，复用 DeepAgents 内建虚拟文件系统 |
| `data/dsagents_sessions.db` | `SqliteSessionStore` | append-only 会话事件（真相源） |
| `data/dsagents_checkpoints.db` | `SqliteSaver` | LangGraph 线程检查点 |
| `data/artifacts/session-events/*.json` | 文件系统 | 超大事件 payload（>`max_inline_bytes`=256KB）溢出 |

## 2. 未证实的跨系统关系

- **LLM provider 可达性与鉴权**：`DSAGENTS_MODEL` 缺省 `openai:gpt-5.5`，但 provider 实际可达性、API key 来源、是否需鉴权——当前源文档未确认（代码不读 `.env`，无显式鉴权字段）。
- **MinerU 内网可达性**：`10.11.0.110:6006` 为内网地址，依赖部署环境网络；不可达即全链路失败，当前无 fallback。
- **隐藏 CoT 入库风险**：trace middleware 仅记录 model-visible 内容（`AGENTS.md` 第 42 行），但事件 payload 含整个 messages 列表，是否混入 hidden CoT 当前源文档未确认。

## 3. 任务排查建议

| 现象 | 先查 |
|---|---|
| MinerU 解析失败/超时 | `MINERU_BASE_URL` 可达性 → `backend/.planning/codebase/CONCERNS.md` §4 → `INTEGRATIONS.md` MinerU 契约 |
| 模型调用失败 | `DSAGENTS_MODEL` 配置 → provider 可达性/鉴权（当前源文档未确认） |
| 事件丢失/不一致 | 是否误把 `context_window` 截断当清理 → `CONCERNS.md` §3（原始事件是真相，不得覆盖） |
| 移动 `data/` 后 artifact 失效 | `artifact_path` 以绝对字符串入库 → `CONCERNS.md` §5 |
| 错误被吞 | 是否在 `wrap_model_call`/`wrap_tool_call` 加了 `except: return` → `CONCERNS.md` §6（必须透传） |
| 误引入第二套虚拟 FS / 早抽象 | `CONCERNS.md` §8 / §1（里程碑纪律与 Simplicity Constraint） |

## 4. 可扩展的集成文档入口

- 端到端调用链与外部服务总图：`coding_maps/SYSTEM_MAP.md`
- backend 实现级集成细节：`backend/.planning/codebase/INTEGRATIONS.md`
- 风险/陷阱/护栏：`backend/.planning/codebase/CONCERNS.md`
- 系统边界与子系统职责：`ARCHITECTURE.md`
- 导航与阅读顺序：`AGENTS.md`
