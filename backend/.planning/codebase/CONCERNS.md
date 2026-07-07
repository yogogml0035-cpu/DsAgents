# CONCERNS

> backend 风险、技术债、关注点。每条均带证据（文件/commit/配置）。状态分 **已确认**（代码或配置可证）与 **需确认**（推断，需人工核实）。
> 本轮刷新已核对最近相关提交：`c8cc563`（run-ledger 时区统一与 schema 迁移）、`bc383ac`（测试端口配置）。行号以当前源码为准。

## 1. 架构迁移残留（run-first 重构，commit 8890292）

- **已确认**｜代码层已彻底去 session：`session.py` 已删除，`grep` 在 `backend/*.py`（非 `.venv`）中无 `run_session` / `sessions.db` / `session.py` 引用；`run_ledger.py` 接管原 session 职责。
- **已清理**｜旧 session 磁盘遗留 `data/dsagents_sessions.db` 与 `data/artifacts/session-events/` 已删除；代码只引用 `dsagents_runs.db` / `dsagents_store.db` / `dsagents_checkpoints.db`（见 `resources.py:23-31`）。
- **已确认**｜`pyproject.toml` 的 `py-modules` 已更新为 `["api","hands","harness","resources","run_ledger","tools"]`，无 `session` / `self_check`。
- **已确认（文档已同步）**｜`docs/backend.md` 的「已删除」节已压缩为指向 `INTERFACES.md` §1 的一句话引用；旧 session 端点的完整清单唯一权威出处为 `INTERFACES.md` §1。
- **已确认**｜长期事实以当前代码和本目录文档为准；被 `.gitignore` 排除的个人研究目录不作为项目事实来源。

## 2. 安全关注点

| 项 | 状态 | 证据 |
|---|---|---|
| `.env` 属于私有配置文件 | 已确认（已缓解） | `.gitignore` 排除 `backend/.env`，`git ls-files` 未跟踪该文件。长期文档只记录配置键与消费者，不读取、不抄录本地值；分享工作区前仍需检查私有配置和运行时数据。 |
| provider key 通过 `os.getenv` 直读 | 已确认 | `harness.py:59-63` 用 `os.getenv("MINIMAX_API_KEY"/"MINIMAX_MODEL"/"MINIMAX_BASE_URL")` 直接构造 `init_chat_model`，无校验/无脱敏日志护栏。 |
| 文档解析服务配置通过环境变量传入 | 已确认 | `tools.py` 读取 `MINERU_*` 键；开发文档只记录键名与用途，不写本地服务地址或连接串。 |
| `data/*.db` 含运行时数据 | 已确认（已缓解） | `backend/data/dsagents_*.db` 未被 git 跟踪（`.gitignore` 含 `backend/data/`）。从 `run_ledger.py` 可直接确认 `input_messages_json`、`reply`、`error` 与 `run_events.raw` 会入库；上传文件本体则落在 `data/artifacts/uploads/`。分享前需同时清理数据库与 artifacts。 |
| 无鉴权 / 无用户隔离 | 已确认 | `api.py` 的 `create_app` 未注册任何 auth middleware；`/runs`、`/runs/{run_id}`、`/upload` 全部匿名可调。 |
| CORS 未实现 | 已确认 | `grep` 在 `api.py` 中无 `CORSMiddleware` / `add_middleware` —— 浏览器跨域实际不会被处理。 |

## 3. 仓库体积 / 入库风险

- **已清理**｜`backend/instantclient/`（Oracle instant client 19.31，约 109MB，37 个 git 跟踪文件）已删除；`.gitignore` 已加入 `backend/instantclient/` 防止重新入库。
- **已确认**｜`__pycache__/` 与 `*.pyc` 已在 `.gitignore`，未入库（`git ls-files | grep __pycache__` 为空）。

## 4. 测试覆盖不足

- **已确认**｜当前测试源码位于 `backend/tests/`：`test_tools.py`、`test_run_ledger.py`、`test_harness.py`、`test_api.py`、`test_real_image_run.py`，共享替身在 `test_support.py`。
- **已确认**｜没有总控自检脚本；实际验证按影响范围直接运行 `cd backend && python -m tests.test_xxx`。普通本地脚本仍用 `FakeBrain` 替代真实模型，并 patch MinerU；覆盖：env 加载、parse_document env 守卫、resources/ledger、tool status middleware、harness、API（TestClient）、startup recovery、virtual artifacts。`test_real_image_run.py` 是手动真实 HTTP / 模型集成脚本。
- **风险**：无 CI 可运行的自动化测试断言；回归靠人工选择并运行对应测试脚本。

## 5. 错误透传约定

- **已确认**｜`harness.py:154-161` 捕获异常后只把 `_error_text(exc)`（即 `str(exc)`，空则取类名）写入 run status `error` 字段，并将 `repr(exc)` 放进 `raw`。
- **已确认**｜`api.py:160-172` `_ensure_failed_run` 同样透传 `_error_text(exc)`；HTTP 层不包装、不脱敏。
- **风险**｜真实错误（含 provider 4xx/5xx body、MinerU 内网地址、文件路径）会原样落到 `runs.error` 与 `run_events.raw`，进而可能暴露给前端调用方；约定是"调用方自行处理"，但无护栏。
- **已确认**｜`_error_text` 同时定义在 `api.py:231` 与 `harness.py:310`（重复实现）。

## 6. 持久化边界（SQLite 多 db）

- **已确认**｜三个 db 职责明确（`resources.py:23-31`）：
  - `dsagents_runs.db` — run 与 run_events（`run_ledger.py`，自建表 `runs` / `run_events`，含 `idx_runs_session_created` / `idx_run_events_run_order`）。
  - `dsagents_store.db` — LangGraph `SqliteStore`（仅 `/memories/` 显式长期记忆路由，见 `resources.py:55-66`）。
  - `dsagents_checkpoints.db` — LangGraph `SqliteSaver` checkpointer（`thread_id=session_id`）。
- **风险**｜三 db 各自独立连接、无跨库事务；`run_ledger` 每次操作都 `sqlite3.connect()` 短连接（`run_ledger.py` 当前 8 处 `connect`：`50/79/111/152/186/218/249/341`），高并发下锁竞争与写吞吐有限（SQLite 默认 WAL 未显式开启）。
- **需确认**｜是否需要在 `_setup` 中 `PRAGMA journal_mode=WAL` 以提升并发写。

## 7. provider 耦合（Anthropic / langchain / deepagents）

- **已确认**｜`harness.py:59-63` 把 MiniMax（OpenAI/Anthropic 兼容）密钥/模型硬编码塞进 `init_chat_model(f"anthropic:{model}", api_key=..., base_url=..., thinking={"type":"adaptive"})` —— 强耦合 Anthropic 客户端协议与 `thinking` 参数。
- **已确认**｜依赖下限在 `pyproject.toml`：`deepagents>=0.6.12`、`langchain>=1.3.11`、`langchain-anthropic>=1.4.8`、`langchain-core>=1.4.8`、`langgraph>=1.2.7` —— 均为 `>=` 无上限，`uv.lock`（311KB）锁具体版本，但任一主版本升级可能破坏 stream chunk 解析（`harness.py` 的 `_message_delta`/`_thinking_delta` 大量依赖 chunk 字段形状）。
- **风险**｜stream chunk 形状（`chunk["type"]` ∈ `messages`/`custom`/`values`、`event` 后缀 `delta`、`thinking`/`reasoning`/`non_standard` block 类型）是 langchain/deepagents 内部约定，无版本契约保护。

## 8. 文档同步风险

- **已确认**｜文档分四层需手工保持一致：根 `AGENTS.md`/`ARCHITECTURE.md`/`INTERFACES.md` → `coding_maps/SYSTEM_MAP.md` → `docs/*.md` → `backend/.planning/codebase/*`。`AGENTS.md` 明确要求"改代码后先更新 `.planning/codebase/` 再回看上层"。
- **风险**｜`MINERU_*` 这类必需配置缺失只能在运行时 fail-fast 暴露，目前无自动化配置完整性校验。

## 9. 配置完整性风险（已确认）

- **已确认**｜`tools.py` 对 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_EFFORT` / `MINERU_TIMEOUT_SECONDS` 都走 `_required_env(...)`；任一缺失都会在真实调用 `parse_document` 时 fail-fast。
- **建议**：维护本地或部署环境时按 `.env.example` 的键名补齐配置；长期文档不记录本地 `.env` 的实际值。

## 10. 并发 / 运行时边界

- **已确认**｜`api.py:66-68/175-191` 并发保护靠**进程内** `threading.Lock` + `dict[session_id, Lock]`（`app.state.session_locks`）。多 worker（如 `uvicorn --workers N`）部署时，同一 `session_id` 可在不同进程并发执行 run，锁失效。
- **已确认**｜run 在 daemon 线程跑（`api.py:94-98` `threading.Thread(..., daemon=True)`）：进程被强杀时，run 状态可能停在 `running`，靠下次启动 `fail_incomplete_runs` 兜底（`api.py:63` lifespan 调用 + `run_ledger.py:247-267`）。
- **已确认**｜`dsagents_runs.db` 与 `artifacts/run-events/` 只增不删，无 TTL/归档/压缩（`run_ledger.py` 无清理方法）；raw chunk 长期留存（见原 §2，调试有利但占空间且保留模型/错误细节）。

## 11. 程序内入口

- **已确认**｜旧的 `from session import run_session` 已不存在；如需库式调用，只能显式组合 `AgentResources` + `create_harness(resources)` + `harness.execute_run(...)`（见 `AGENTS.md`、`harness.py:173`）。非缺陷，仅作记录以防误用。
