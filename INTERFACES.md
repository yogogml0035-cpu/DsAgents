# 接口边界 (INTERFACES)

> 事实来源：backend/.planning/codebase/ 与 coding_maps/SYSTEM_MAP.md（2026-07-02 生成）

本文件是 DsAgents 仓库的**接口与集成边界**文档，描述已确认的接口边界、未证实的跨系统关系、任务排查建议与可扩展集成入口。系统级架构见 `ARCHITECTURE.md`；全局原则与入口见 `AGENTS.md`。

证据强度区分：**已确认** = 直接见于 `backend/` 源码或 `backend/pyproject.toml`（依赖版本由 `backend/uv.lock` 锁定，包管理器为 uv）；**需确认** = 仅见于 `.env.example` 或规划文档、源码无直接引用。本文不写入任何密钥 / token / 连接串。

---

## 1. 已确认接口边界

### 1.1 MinerU 异步任务 API（文档解析）

- **边界位置**：`backend/tools.py::parse_document_with_mineru`（经 `default_tool_catalog()` 注册为工具）。
- **服务地址**：`MINERU_BASE_URL = "http://10.11.0.110:6006"`（`backend/tools.py:12`，源码**硬编码**）。
- **三步调用流程**（均为同步阻塞的 `requests`）：
  1. **提交任务** `POST /tasks`：multipart 上传文件，表单字段固定 `backend=hybrid-engine`、`effort=high`、`return_md=true`、`response_format_zip=false`；`timeout=60`。从响应递归查找 `task_id / taskId / id` 得到 `task_id`。
  2. **轮询状态** `GET /tasks/{task_id}`：`timeout=30`，读 `status / state`；命中失败态抛错，命中成功态进入取结果，否则 `time.sleep(poll_interval_seconds)`（默认 2.0s）继续，直到 `timeout_seconds`（默认 900s）超时抛 `TimeoutError`。
  3. **取结果** `GET /tasks/{task_id}/result`：`timeout=120`；递归查找 `md / markdown / md_content / markdown_content`，写出本地 Markdown。
- **固定参数**：`backend=hybrid-engine`、`effort=high` 不可由调用方更改（里程碑约束）。
- **产出落点**：默认 `data/mineru_outputs/{stem}.md`，可经 `output_path` 覆盖。
- **认证**：源码未携带任何鉴权头/token；需确认该内网端点是否需要鉴权。
- **传输**：明文 HTTP 无 TLS。

> `.env.example` 虽有 `MINERU_BASE_URL=`、`MINERU_BACKEND=`、`MINERU_TIMEOUT_SECONDS=` 三个键，但 `backend/tools.py` 当前**未读取**这些环境变量——地址与参数均硬编码。属于配置键与实现脱节（死配置）。

### 1.2 DeepAgents BrainFactory Protocol（可插拔 Brain）

- **边界位置**：`backend/harness.py` 的 `DeepAgentsBrainFactory`（实现 `BrainFactory` Protocol）。
- **集成方式**：`from deepagents import create_deep_agent` 构建 Brain，传入 `model`、`tools`、`system_prompt`、`middleware`、`backend`、`checkpointer`、`store`。Brain 暴露 `invoke(payload, config)` 接口（`Brain` Protocol）。
- **调用约定**：`HarnessRuntime.run_turn` 以 `{"messages": _reset_messages(context)}`、`config={"configurable": {"thread_id": session_id}}` 调用 `brain.invoke`。`_reset_messages` 在上下文前插入 `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 实现重置后回放。
- **后端注入**：Brain 复用 `AgentResources` 提供的 `CompositeBackend` / `checkpointer` / `store`。DeepAgents 内置虚拟文件系统通过该 `backend` 暴露给模型。
- **可替换性**：`BrainFactory` 是 Protocol，`backend/self_check.py` 用 `_FakeBrainFactory` 证明 Brain 可被替换——DeepAgents 并非硬绑定。

### 1.3 DeepAgents CompositeBackend 虚拟文件系统路由

- **边界位置**：`backend/resources.py::AgentResources.__enter__`。
- **路由规则**（`CompositeBackend`，`default=StateBackend()`）：
  - `/memories/`、`/conversation_history/`、`/logs/` → `StoreBackend(store=SqliteStore, namespace=("dsagents",))`（持久，落 SQLite）。
  - `/artifacts/`、`/large_tool_results/` → `FilesystemBackend(root_dir=data/artifacts, virtual_mode=True)`（落盘）。
  - 其余路径 → `StateBackend()`（图状态/内存，默认）。
- **作用**：模型写"记忆/历史/日志"落 SQLite Store，写"大产物/大工具结果"落本地磁盘，写一般内容随图状态保存。遵循根 `AGENTS.md`"使用 DeepAgents 内置虚拟文件系统，不另加包装"。

### 1.4 Python 导入 API（对外主接口）

`backend/` 通过 `__init__.py` re-export 四个顶层 API（当前**唯一**对外形态，非 HTTP）：

| API | 位置 | 用途 |
|-----|------|------|
| `run_session(message, session_id=None)` | `backend/session.py` | 最小 session runner：装配资源 → 单轮执行 → 返回 `HarnessTurn` |
| `create_mineru_agent(resources)` | `backend/harness.py` | 便捷装配：resources → mineru harness → agent |
| `create_mineru_harness(resources)` | `backend/harness.py` | 由 resources 构造 MinerU 工具 + DeepAgents Brain 的 `HarnessRuntime` |
| `parse_document_with_mineru(...)` | `backend/tools.py` | MinerU 解析工具（亦可直接调用） |

典型用法：`from backend import run_session; run_session("帮我解析 xxx.pdf")`。无 `backend/__main__.py`，故 `python -m backend`（无子模块名）当前不可用，需确认是否计划补全。可用命令入口为 `python -m backend.self_check`（自检）。

### 1.5 三条独立 SQLite 持久化通道

| 用途 | 路径 | 谁建/写 |
|------|------|---------|
| 会话事件库（append-only） | `data/dsagents_sessions.db` | `SqliteSessionStore`（标准库 `sqlite3`，`backend/session.py`） |
| LangGraph Store（持久记忆/历史/日志） | `data/dsagents_store.db` | `SqliteStore.from_conn_string` + `.setup()`（`backend/resources.py`） |
| LangGraph Checkpoint（线程状态检查点） | `data/dsagents_checkpoints.db` | `SqliteSaver.from_conn_string` + `.setup()`（`backend/resources.py`） |

三库相互独立，均由 `AgentResources.__enter__` 创建 + `.setup()`，`__exit__` 经 `ExitStack` 关闭。均为本地文件 SQLite，无连接串/网络、无远程 DB。会话事件库为 append-only，超大 payload（> 256KiB）外溢到 `data/artifacts/session-events/<uuid>.json`，DB 仅存 `{artifact_path, bytes}` 指针。

### 1.6 MiniMax LLM（OpenAI 兼容，默认 LLM 提供方）

- **边界位置**：`backend/harness.py::DeepAgentsBrainFactory.__init__`。
- **默认模型**：`openai:{MINIMAX_MODEL or "MiniMax-M3"}`，默认 base url `https://api.minimaxi.com/v1`。
- **凭据映射**：当 `MINIMAX_API_KEY` 存在时 `os.environ.setdefault("OPENAI_API_KEY", api_key)`，并把 `MINIMAX_BASE_URL`（或默认）`setdefault` 到 `OPENAI_API_BASE`。即以 OpenAI 兼容协议调用 MiniMax。`setdefault` 不覆盖已显式设置的值。

---

## 2. 未证实的跨系统关系（需确认）

以下键仅出现在 `backend/.env.example`，`backend/` 自身 `.py` 源码**无任何直接引用**，视为预留、规划中或前端边界。改动前须先确认归属，不要当作已生效集成处理：

| 名称 | `.env.example` 键 | 现状与判断 |
|------|-------------------|------------|
| **DeepSeek** | `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL=https://api.deepseek.com`、`DEEPSEEK_MODEL=deepseek-v4-flash` | backend 源码零引用，疑似预留为可切换 LLM 提供方，需确认。 |
| **Oracle** | `ORACLE_DSN`、`ORACLE_USERNAME`、`ORACLE_PASSWORD`、`ORACLE_CLIENT_LIB_DIR`、`ORACLE_TIMEOUT_SECONDS` | backend 源码零引用；`backend/pyproject.toml` 的 `[project.dependencies]` 未列 `oracledb`/`cx_Oracle`；但 `backend/instantclient/` Oracle 二进制已提交进 git。配置先于实现进入仓库，疑似范围蔓延前兆，需确认是否有未列入里程碑的 Oracle 数据源需求。 |
| **LangSmith** | `LANGSMITH_TRACING=false`、`LANGSMITH_ENDPOINT`、`LANGSMITH_PROJECT=DsAgents` | 默认关闭，backend 源码无直接引用，经 LangChain/LangGraph 运行时间接生效。若误开启会把 trace 上传外部服务。需确认是否计划启用。 |
| **CORS / 前端** | `CORS_ORIGINS=http://localhost:8500,8500` | 端口 8500 暗示 Streamlit。backend 无 FastAPI/uvicorn 等 web 框架、无 HTTP server，源码未引用。属预留 / 前端边界，服务层归属需确认。 |

---

## 3. 任务排查建议

按任务类型，应**先读**下列事实文档（路径相对仓库根），再到本文件定位接口边界：

- **改 MinerU 工具**：读 `backend/.planning/codebase/INTEGRATIONS.md` §1（三步 API、固定参数、字段模糊匹配）、`CONCERNS.md` §1（地址硬编码、无重试/降级、明文 HTTP）。提醒：`backend=hybrid-engine`/`effort=high` 固定不可配；MinerU 协议字段变更会冲击 `tools.py::_find_value` 模糊匹配；服务不可用为硬失败。
- **改存储 / 持久化**：读 `backend/.planning/codebase/STRUCTURE.md` §4（资源目录约定）、`INTEGRATIONS.md` §3-4（三 SQLite 库 + CompositeBackend 路由）。提醒：会话事件 append-only、不可 update/delete；三库相互独立；超大 payload 外溢机制需保持。
- **加 / 改 Provider（LLM 或外部服务）**：读本文件 §1.6 / §2、`backend/.planning/codebase/STACK.md`。提醒：当前唯一外部网络依赖是 MinerU 内网端点；MiniMax 经 OpenAI 兼容协议、`setdefault` 不覆盖显式值；DeepSeek/Oracle/LangSmith/CORS 均需先确认归属再动。
- **改 Brain / Harness 执行**：读 `backend/.planning/codebase/ARCHITECTURE.md` §3-4（运行时数据流、关键设计决策）、本文件 §1.2。提醒：保持 `BrainFactory` Protocol 可替换、Harness 薄、真实错误透传。
- **改可观测 / trace**：读 `backend/.planning/codebase/CONCERNS.md` §4。提醒：middleware 仅记录模型可见层，不触碰隐藏思维链；trace 写入 SQLite 的错误事件含 `repr(exc)`，当前无脱敏。

---

## 4. 可扩展集成入口

未来扩展时，应沿下列边界接入，避免破坏五大模块边界（详见 `AGENTS.md` Harness 原则与 `ARCHITECTURE.md` §5）：

- **新增前端 / 服务层**：当前 backend 是 Python API 而非 HTTP 服务。新增前端须同时决定"是否引入服务层"——根 `AGENTS.md` 明确服务层只在真实 caller 需要时才加，每个新抽象必须保护五大边界之一。`.env.example` 的 `CORS_ORIGINS=http://localhost:8500` 已预留前端端口（疑似 Streamlit），属未实现边界。新增子项目后须在 `coding_maps/SYSTEM_MAP.md` §2-4 同步子项目职责表、调用链、接口边界。
- **新增可插拔 Brain / runner**：实现 `BrainFactory` Protocol 即可（参照 `DeepAgentsBrainFactory` 与 `self_check.py::_FakeBrainFactory`），不要把新 runner 硬绑到现有工具/资源。
- **新增工具**：实现 `ToolHandler` 并加入 `ToolCatalog`，由 Harness 注入；工具不绑定单一 runner。
- **新增 LLM Provider**：参照 `DeepAgentsBrainFactory` 的 OpenAI 兼容映射方式（`setdefault` 回填 `OPENAI_API_KEY`/`OPENAI_API_BASE`）；DeepSeek 等 `.env.example` 预留键的归属需先确认。
- **新增持久化通道**：当前三 SQLite 库 + 文件系统产物均由 `AgentResources` / `ResourceConfig` 集中管理。新增存储应经同一资源装配层，保持会话事件 append-only 语义。
- **新增子项目访问 backend 数据**：应通过明确接口访问而非直连 SQLite 库；事件 / 产物 / 三库归属 backend。

> 完整集成调用链（用户输入 → MinerU 解析 → 产物落盘的全链路）见 `coding_maps/SYSTEM_MAP.md` §3 与 `backend/.planning/codebase/INTEGRATIONS.md`「集成调用链」小节。
