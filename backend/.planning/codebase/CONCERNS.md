# CONCERNS

> backend 风险、技术债、关注点。每条均带证据（文件/commit/配置）。状态分 **已确认**（代码或配置可证）与 **需确认**（推断，需人工核实）。

## 1. 架构迁移残留（run-first 重构，commit 8890292）

- **已确认**｜代码层已彻底去 session：`session.py` 已删除，`grep` 在 `backend/*.py`（非 `.venv`）中无 `run_session` / `sessions.db` / `session.py` 引用；`run_ledger.py` 接管原 session 职责。
- **已确认**｜`data/dsagents_sessions.db` 仍是磁盘遗留文件（`ls backend/data/` 可见，155KB→实为 77KB sessions.db），但代码只引用 `dsagents_runs.db` / `dsagents_store.db` / `dsagents_checkpoints.db`（见 `resources.py:23-31`）。**需确认**：是否应手动删除该孤儿 db。
- **已确认**｜`pyproject.toml` 的 `py-modules` 已更新为 `["api","hands","harness","resources","run_ledger","tools","self_check"]`，无 `session`。
- **已确认（文档已同步）**｜`docs/backend.md` 的「已删除」节已压缩为指向 `INTERFACES.md` §1 的一句话引用；旧 session 端点的完整清单唯一权威出处为 `INTERFACES.md` §1。
- **已确认（文档未同步）**｜`Study/` 整个目录（`ch02-Session是单一事实源.md`、`ch03-一次run_turn的完整旅程.md` 等）仍以 session 为事实源叙事；`Study/` 已被 `.gitignore` 忽略（commit f65247f），但仍属知识库漂移风险。

## 2. 安全关注点

| 项 | 状态 | 证据 |
|---|---|---|
| `.env` 含真实密钥入库 | 已确认（已缓解） | `backend/.env` 含 `MINIMAX_API_KEY=sk-cp-...`、`DEEPSEEK_API_KEY=sk-...`、`LANGSMITH_API_KEY=lsv2_pt_...`、`ORACLE_PASSWORD=dongsong`；`.gitignore` 含 `backend/.env`，`git ls-files` 未跟踪 `.env`。**风险**：明文密钥已落工作树，任何本地读取/备份/分享仓库目录都会泄露；建议轮换并改用 secret manager。 |
| provider key 通过 `os.getenv` 直读 | 已确认 | `harness.py:53-57` 用 `os.getenv("MINIMAX_API_KEY"/"MINIMAX_MODEL")` 直接构造 `init_chat_model`，无校验/无脱敏日志护栏。 |
| 数据库 URL / DSN 明文 | 已确认 | `.env` 中 `ORACLE_DSN`、`MINERU_BASE_URL=http://10.11.0.110:6006`（内网 IP）明文；`.env.example` 复刻相同结构（占位）。 |
| `data/*.db` 含运行时数据 | 已确认（已缓解） | `backend/data/dsagents_*.db` 未被 git 跟踪（`.gitignore` 含 `backend/data/`）。**需确认**：`.db` 内是否含用户上传内容/模型回复（`input_message`、`reply`、raw chunk 均入库），若分享需先清理。 |
| 无鉴权 / 无用户隔离 | 已确认 | `api.py` 的 `create_app` 未注册任何 auth middleware；`/runs`、`/runs/{run_id}`、`/files` 全部匿名可调。 |
| CORS 配置声明但未生效 | 已确认 | `.env` 有 `CORS_ORIGINS`，但 `grep` 在 `api.py` 中无 `CORSMiddleware` / `add_middleware` —— 配置项是死配置，浏览器跨域实际不会被处理。 |

## 3. 仓库体积 / 入库风险

- **已确认**｜`backend/instantclient/`（Oracle instant client 19.31）**被 git 跟踪**：`git ls-files backend/instantclient/` 返回 37 个文件，含 `oci.dll`、`ocijdbc19.dll`、`adrci.exe`、`genezi.exe` 等二进制；`du -sh` 约 **109MB**。`.gitignore` 未排除该目录。
- **已确认**｜Oracle client 当前**未被代码使用**：`grep -rln "oracledb\|ORACLE_\|instantclient" backend/*.py` 无命中（仅 `.env`/`.env.example` 有 `ORACLE_*` 与 `ORACLE_CLIENT_LIB_DIR`）。入库 109MB 但无运行时依赖 → 纯粹的体积负担。
- **已确认**｜`__pycache__/` 与 `*.pyc` 已在 `.gitignore`，未入库（`git ls-files | grep __pycache__` 为空）。
- **需确认**｜是否应将 `instantclient/` 移出仓库（改 `.gitignore` + 文档说明运行环境自备），还是确有入库理由（如内网无外网下载）。

## 4. 测试覆盖不足

- **已确认**｜`backend/tests/` 为空（仅一个 `__pycache__/`，无 `*.py`）；旧 `tests/test_stream_typing.py` 在 commit 8890292 被删除。
- **已确认**｜实际验证依赖 `self_check.py`（430 行，非 pytest，主入口 `main()`，结尾打印 `self-check passed`），用 `_FakeBrain` 替代真实模型，覆盖：env 加载、parse_document env 守卫、resources/ledger、tool status middleware、harness、API（TestClient）、startup recovery、virtual artifacts。**未覆盖**：真实 provider 调用、`parse_document` 端到端 MinerU、并发/锁竞争、超大 raw chunk 落盘。
- **风险**：无 CI 可运行的自动化测试断言；回归靠人工跑 `python backend/self_check.py`。

## 5. 错误透传约定

- **已确认**｜`harness.py:147-154` 捕获异常后只把 `_error_text(exc)`（即 `str(exc)`，空则取类名）写入 run status `error` 字段，并将 `repr(exc)` 放进 `raw`。
- **已确认**｜`api.py:116-128` `_ensure_failed_run` 同样透传 `_error_text(exc)`；HTTP 层不包装、不脱敏。
- **风险**｜真实错误（含 provider 4xx/5xx body、MinerU 内网地址、文件路径）会原样落到 `runs.error` 与 `run_events.raw`，进而可能暴露给前端调用方；约定是"调用方自行处理"，但无护栏。
- **已确认**｜`_error_text` 同时定义在 `api.py:187` 与 `harness.py:278`（重复实现）。

## 6. 持久化边界（SQLite 多 db）

- **已确认**｜三个 db 职责明确（`resources.py:21-31`）：
  - `dsagents_runs.db` — run 与 run_events（`run_ledger.py`，自建表 `runs` / `run_events`，含 `idx_runs_session_created` / `idx_run_events_run_order`）。
  - `dsagents_store.db` — LangGraph `SqliteStore`（仅 `/memories/` 显式长期记忆路由，见 `resources.py:55-64`）。
  - `dsagents_checkpoints.db` — LangGraph `SqliteSaver` checkpointer（`thread_id=session_id`）。
- **已确认**｜`dsagents_sessions.db` 是**孤儿文件**（磁盘存在，代码零引用）—— 见 §1。
- **风险**｜三 db 各自独立连接、无跨库事务；`run_ledger` 每次操作都 `sqlite3.connect()` 短连接（`run_ledger.py:47/76/108/158/190/222`），高并发下锁竞争与写吞吐有限（SQLite 默认 WAL 未显式开启）。
- **需确认**｜是否需要在 `_setup` 中 `PRAGMA journal_mode=WAL` 以提升并发写。

## 7. provider 耦合（Anthropic / langchain / deepagents）

- **已确认**｜`harness.py:53-57` 把 MiniMax（OpenAI/Anthropic 兼容）密钥/模型硬编码塞进 `init_chat_model(f"anthropic:{model}", api_key=..., base_url=..., thinking={"type":"adaptive"})` —— 强耦合 Anthropic 客户端协议与 `thinking` 参数。
- **已确认**｜依赖下限在 `pyproject.toml`：`deepagents>=0.6.12`、`langchain>=1.3.11`、`langchain-anthropic>=1.4.8`、`langchain-core>=1.4.8`、`langgraph>=1.2.7` —— 均为 `>=` 无上限，`uv.lock`（311KB）锁具体版本，但任一主版本升级可能破坏 stream chunk 解析（`harness.py` 的 `_message_delta`/`_thinking_delta` 大量依赖 chunk 字段形状）。
- **已确认**｜`.env` 同时配置 `DEEPSEEK_*` 与 `MINIMAX_*`，但代码**只读 MINIMAX**（`grep DEEPSEEK backend/*.py` 无命中）—— `DEEPSEEK_*` 为死配置，易误导。
- **风险**｜stream chunk 形状（`chunk["type"]` ∈ `messages`/`custom`/`values`、`event` 后缀 `delta`、`thinking`/`reasoning`/`non_standard` block 类型）是 langchain/deepagents 内部约定，无版本契约保护。

## 8. 文档同步风险

- **已确认**｜文档分四层需手工保持一致：根 `AGENTS.md`/`ARCHITECTURE.md`/`INTERFACES.md` → `coding_maps/SYSTEM_MAP.md` → `docs/*.md` → `backend/.planning/codebase/*`。`AGENTS.md` 明确要求"改代码后先更新 `.planning/codebase/` 再回看上层"。
- **已确认（已漂移）**｜`docs/backend.md` 仍写 session 旧接口（见 §1）；`Study/` 全套以 session 为事实源（已被 gitignore，但仍属知识源漂移）。
- **风险**｜`MINERU_EFFORT` 见 §9 这类配置漂移靠人工核对，无自动化校验。

## 9. 配置漂移（已确认，高危）

- **已确认**｜`tools.py:45` `_required_env("MINERU_EFFORT")`，但 `backend/.env` **缺少 `MINERU_EFFORT`**（仅有 `MINERU_BASE_URL`/`MINERU_BACKEND`/`MINERU_TIMEOUT_SECONDS`）→ 一旦真实调用 `parse_document`，立即 `RuntimeError("Missing required environment variable: MINERU_EFFORT")`。`.env.example:13` 才有 `MINERU_EFFORT=high`。
- **已确认**｜`ORACLE_*`（含 `ORACLE_CLIENT_LIB_DIR` 指向入库的 instantclient）在 `.env` 配齐但代码零引用（见 §3）。
- **建议**：`.env` 补 `MINERU_EFFORT`；清理 `DEEPSEEK_*` 或在 harness 显式支持 provider 切换。

## 10. 并发 / 运行时边界

- **已确认**｜`api.py:42-44/131-147` 并发保护靠**进程内** `threading.Lock` + `dict[session_id, Lock]`（`app.state.session_locks`）。多 worker（如 `uvicorn --workers N`）部署时，同一 `session_id` 可在不同进程并发执行 run，锁失效。
- **已确认**｜run 在 daemon 线程跑（`api.py:65-69` `threading.Thread(..., daemon=True)`）：进程被强杀时，run 状态可能停在 `running`，靠下次启动 `fail_incomplete_runs` 兜底（`api.py:39` + `run_ledger.py:219`）。
- **已确认**｜`dsagents_runs.db` 与 `artifacts/run-events/` 只增不删，无 TTL/归档/压缩（`run_ledger.py` 无清理方法）；raw chunk 长期留存（见原 §2，调试有利但占空间且保留模型/错误细节）。

## 11. 程序内入口

- **已确认**｜旧的 `from session import run_session` 已不存在；如需库式调用，只能显式组合 `AgentResources` + `create_harness(resources)` + `harness.execute_run(...)`（见 `AGENTS.md`、`harness.py:166`）。非缺陷，仅作记录以防误用。
