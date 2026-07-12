# CONCERNS

> backend 风险、技术债、关注点。每条均带证据（文件/commit/配置）。状态分 **已确认**（代码或配置可证）与 **需确认**（推断，需人工核实）。
> 本轮刷新（2026-07-10）已核对当前工作树：新增 Skills/Subagents、业务 artifact 与 Oracle fallback 风险；既有 SQLite、错误透传和进程内锁关注点仍成立。行号以当前源码为准。

## 1. 架构迁移残留（run-first 重构，commit 8890292）

- **已确认**｜代码层已彻底去 session：`session.py` 已删除，`grep` 在 `backend/*.py`（非 `.venv`）中无 `run_session` / `sessions.db` / `session.py` 引用；`run_ledger.py` 接管原 session 职责。
- **已清理**｜旧 session 磁盘遗留 `data/dsagents_sessions.db` 与 `data/artifacts/session-events/` 已删除；代码只引用 `dsagents_runs.db` / `dsagents_store.db` / `dsagents_checkpoints.db`（见 `resources.py:23-31`）。
- **已确认**｜`pyproject.toml` 的 `py-modules` 已包含四个新增扁平模块，仍无已删除的 session/self-check 模块。
- **已确认（文档已同步）**｜`docs/backend.md` 的「已删除」节已压缩为指向 `INTERFACES.md` §1 的一句话引用；旧 session 端点的完整清单唯一权威出处为 `INTERFACES.md` §1。
- **已确认**｜长期事实以当前代码和本目录文档为准；被 `.gitignore` 排除的个人研究目录不作为项目事实来源。

## 2. 安全关注点

| 项 | 状态 | 证据 |
|---|---|---|
| `.env` 属于私有配置文件 | 已确认（已缓解） | `.gitignore` 排除 `backend/.env`，`git ls-files` 未跟踪该文件。长期文档只记录配置键与消费者，不读取、不抄录本地值；分享工作区前仍需检查私有配置和运行时数据。 |
| provider key 通过 `os.getenv` 直读 | 已确认 | `harness.py:81-89` 用 `os.getenv("MINIMAX_API_KEY"/"MINIMAX_MODEL"/"MINIMAX_BASE_URL")` 直接构造 `init_chat_model`，无校验/无脱敏日志护栏。 |
| 文档解析服务配置通过环境变量传入 | 已确认 | `tools.py` 读取 `MINERU_*` 键；开发文档只记录键名与用途，不写本地服务地址或连接串。 |
| Oracle 凭据通过环境变量传入 | 已确认 | `philips_wgq_import.py` 只读取 `ORACLE_*` 键且不记录值；配置缺失/查询失败返回人工校验。连接异常文本不会写入生成结果。 |
| `data/*.db` 含运行时数据 | 已确认（已缓解） | `backend/data/dsagents_*.db` 未被 git 跟踪（`.gitignore` 含 `backend/data/`）。从 `run_ledger.py` 可直接确认 `input_messages_json`、`reply`、`error` 与 `run_events.raw` 会入库；上传文件本体落在 `data/artifacts/uploads/`，大 payload spill 落在 `data/internal/run-events/`。分享前需同时清理数据库与运行时文件目录。 |
| 无鉴权 / 无用户隔离 | 已确认 | `api.py` 的 `create_app` 未注册任何 auth middleware；`/runs`、`/runs/{run_id}`、`/upload` 全部匿名可调。 |
| CORS 未实现 | 已确认 | `grep` 在 `api.py` 中无 `CORSMiddleware` / `add_middleware` —— 浏览器跨域实际不会被处理。 |

## 3. 仓库体积 / 入库风险

- **已清理**｜`backend/instantclient/`（Oracle instant client 19.31，约 109MB，37 个 git 跟踪文件）已删除；`.gitignore` 已加入 `backend/instantclient/` 防止重新入库。
- **已清理**｜`backend/dsagents.egg-info/` 已从 git 索引移除（`git ls-files | grep egg-info` 为空，commit `864470d`/`007bb57`），且 `.gitignore:17` 已加入 `backend/dsagents.egg-info/`。该目录仍会在本地由 setuptools 重新生成，但不再造成入库 churn。
- **已确认**｜`__pycache__/` 与 `*.pyc` 已在 `.gitignore`，未入库（`git ls-files | grep __pycache__` 为空）。

## 4. 测试覆盖不足

- **已确认**｜当前新增 `test_workflow_setup.py`、`test_philips_wgq_import.py`、`test_tecan_import.py`；原有本地与两个 `test_real_*` 脚本保留。
- **已确认**｜没有总控自检脚本；实际验证按影响范围直接运行 `cd backend && python -m tests.test_xxx`。普通本地脚本仍用 `FakeBrain` 替代真实模型，并 patch MinerU；覆盖：env 加载、`parse_documents` env 守卫与批量提交流程、resources/ledger、tool status middleware、harness、API（TestClient）、startup recovery、virtual artifacts。`test_real_image_run.py` 与 `test_real_multi_pdf_run.py` 是手动真实 HTTP / 模型集成脚本。
- **风险**：无 CI 可运行的自动化测试断言；回归靠人工选择并运行对应测试脚本。

## 5. 错误透传约定

- **已确认**｜`harness.py:203-210` 捕获异常后只把 `_error_text(exc)`（即 `str(exc)`，空则取类名）写入 run status `error` 字段，并将 `repr(exc)` 放进 `raw`。
- **已确认**｜`api.py` 的 `_ensure_failed_run` 同样透传 `_error_text(exc)`；HTTP 层不包装、不脱敏（行号 `api.py:175-188` 仅为辅助，以函数名为准）。
- **风险**｜真实错误（含 provider 4xx/5xx body、MinerU 内网地址、文件路径）会原样落到 `runs.error` 与 `run_events.raw`，进而可能暴露给前端调用方；约定是"调用方自行处理"，但无护栏。
- **已确认**｜`_error_text` 在三处独立实现（`api.py` / `harness.py` / `tools.py` 的 `_error_text` 函数），逻辑完全一致（`str(exc).strip() or exc.__class__.__name__`）。三处都用裸 `str(exc)` 透传错误文本。可用 `grep -rn "_error_text" backend/*.py` 复现（参考行号 `api.py:239`、`harness.py:599`、`tools.py:502` 仅为辅助定位，以提交 `8890292` 为准）。

## 6. 持久化边界（SQLite 多 db）

- **已确认**｜三个 db 职责明确（`resources.py:23-31`）：
  - `dsagents_runs.db` — run 与 run_events（`run_ledger.py`，自建表 `runs` / `run_events`，含 `idx_runs_session_created` / `idx_run_events_run_order`）。
  - `dsagents_store.db` — LangGraph `SqliteStore`（仅 `/memories/` 显式长期记忆路由，见 `resources.py:55-66`）。
  - `dsagents_checkpoints.db` — LangGraph `SqliteSaver` checkpointer（`thread_id=session_id`）。
- **风险**｜三 db 各自独立连接、无跨库事务；`run_ledger` 每次数据库操作都开短连接（`sqlite3.connect()` 后在 `with`/`try-finally` 内关闭），高并发下锁竞争与写吞吐有限。可用 `grep -n "connect" run_ledger.py` 复现全部调用点（行号随提交漂移，以定性描述为准）。
- **已确认**｜journal_mode 各库不一致：`dsagents_runs.db` 为 `delete`（`run_ledger._setup` 未设 PRAGMA），而 `dsagents_checkpoints.db` 由 LangGraph `SqliteSaver` 开启 WAL（磁盘上存在 `dsagents_checkpoints.db-wal` / `-shm`，4MB+ 未 checkpoint）。`dsagents_store.db` 同走 LangGraph `SqliteStore`。WAL 的生命周期与 checkpoint 频率由 LangGraph 管理，非本仓代码控制。
- **风险**｜`runs.db` 的 `delete` 模式下，`emit_run_event` 在 run 执行期间高频短连接写（每个 stream chunk 一条），与读端 `get_run_events` 轮询并发时易触发 `database is locked`（SQLite 默认 5s busy timeout，本仓未显式设置 `busy_timeout`）。
- **需确认**｜是否需要在 `_setup` 或连接级为 `runs.db` 设置 `PRAGMA journal_mode=WAL` 与 `busy_timeout` 以缓解写锁竞争；以及是否需要对 checkpoints.db 的 WAL 做 checkpoint/归档。
- **风险（时区与数据迁移，commit `c8cc563`）｜**`run_ledger.py` 已从 UTC ISO（`_utcnow()`/`datetime.now(timezone.utc).isoformat()`）切换为本机时区秒级文本（`_now_text()` = `datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"`，`run_ledger.py:454-455`）。三处隐患：
  1. **时区依赖宿主**：`astimezone()` 无显式 tz 时取系统本地时区，部署到不同时区的机器会让同一数据库内的 `created_at`/`updated_at` 混用多套时区文本，且无 tz 后缀（裸 `YYYY-MM-DD HH:MM:SS`），下游无法可靠还原 UTC。
  2. **一次性迁移假设**：`_migrate`（`run_ledger.py:385-389`）仅在 `pragma user_version < 1` 时跑 `_normalize_existing_timestamps(assume_naive_utc=True)`，把历史无 tz 时间戳**一律当作 UTC** 转本机时区。若历史数据实际已混入本机时区或其它来源的裸文本，迁移会引入偏移；迁移完成后 `user_version=1`，不会再次执行（幂等且单向，不可回滚）。
  3. **schema 版本机制单薄**：`RUN_LEDGER_SCHEMA_VERSION=1` 是唯一版本号，未来表结构变更需复用同一 `user_version` 递增；目前没有迁移失败回滚或版本不匹配的告警。

## 7. provider 耦合（Anthropic / langchain / deepagents）

- **已确认**｜`harness.py:81-89` 把 MiniMax（OpenAI/Anthropic 兼容）配置传入 `init_chat_model(f"anthropic:{model}", api_key=..., base_url=..., thinking={"type":"adaptive"})` —— 强耦合 Anthropic 客户端协议与 `thinking` 参数。
- **已确认**｜依赖下限在 `pyproject.toml`：`deepagents>=0.6.12`、`langchain>=1.3.11`、`langchain-anthropic>=1.4.8`、`langchain-core>=1.4.8`、`langgraph>=1.2.7` —— 均为 `>=` 无上限，`uv.lock`（311KB）锁具体版本，但任一主版本升级可能破坏 stream chunk 解析（`harness.py` 的 `_message_delta`/`_thinking_delta` 大量依赖 chunk 字段形状）。
- **风险**｜stream chunk 形状（`chunk["type"]` ∈ `messages`/`custom`/`values`、`event` 后缀 `delta`、`thinking`/`reasoning`/`non_standard` block 类型、snapshot 中 `message.id` / `tool_call_id` / `tool_calls[]` 形状）是 langchain/deepagents 内部约定，无版本契约保护。
- **已确认**｜官方新文档展示的 `harness_profile` 参数不在锁定 `deepagents==0.6.12` 的 `create_deep_agent` 签名中；当前使用公开的 provider profile 注册 API 禁用默认 general-purpose subagent。
- **风险**｜provider profile 注册是进程级全局行为且当前只覆盖 `anthropic`。若将默认模型改成其它 provider，需重新核对是否会自动多出 general-purpose subagent；不要假设当前四个 extractor 配置自动跨 provider 等价。

## 8. Oracle instant client 部署依赖

- **已确认**｜`philips_wgq_import.py` 的 `_oracle_units` 通过 `oracledb` thick mode 连接 Oracle，依赖 `ORACLE_CLIENT_LIB_DIR` 环境变量指向 Oracle instant client 目录（`init_oracle_client(lib_dir=...)`）。
- **风险**｜该 instant client 曾从仓库删除（`backend/instantclient/`，见 §3，约 109MB，commit 历史留痕），意味着**生产部署必须由外部提供该目录**（容器镜像挂载、主机预装等），不能依赖仓库存放。
- **已确认（降级行为）**｜若 `ORACLE_CLIENT_LIB_DIR` 缺失或配置错误导致 thick mode 初始化失败，`_oracle_units` 会优雅降级（跳过法定单位查询），不抛错；但生成的核注清单将**缺少法定单位字段**（业务影响，非崩溃）。
- **验证步骤**｜部署时设置好 `ORACLE_CLIENT_LIB_DIR` 后，运行涉及 Oracle 的业务流程（`philips_wgq_import.py` 的 `_oracle_units` 调用链），确认无 Oracle 初始化报错且生成的核注清单含法定单位字段。

## 9. 文档同步风险

- **已确认**｜文档分四层需手工保持一致：根 `AGENTS.md`/`ARCHITECTURE.md`/`INTERFACES.md` → `coding_maps/SYSTEM_MAP.md` → `docs/*.md` → `backend/.planning/codebase/*`。`AGENTS.md` 明确要求"改代码后先更新 `.planning/codebase/` 再回看上层"。
- **风险**｜`MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 这类必需配置缺失只能在运行时 fail-fast 暴露，目前无自动化配置完整性校验。

## 10. 配置完整性风险（已确认）

- **已确认**｜`tools.py` 对 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 走 `_required_env(...)`；`MINERU_EFFORT` 走 `os.getenv(... ) or ""`，可缺省或留空，并会原样以空字符串提交到 MinerU。
- **建议**：维护本地或部署环境时按 `.env.example` 的键名补齐配置；长期文档不记录本地 `.env` 的实际值。

## 11. 并发 / 运行时边界

- **已确认**｜`api.py` 的并发保护（`_acquire_session_run` / `_release_session_run` 等会话级锁函数，行号 `66-68`/`175-191` 仅为辅助，以函数名为准）靠**进程内** `threading.Lock` + `dict[session_id, Lock]`（`app.state.session_locks`）。多 worker（如 `uvicorn --workers N`）部署时，同一 `session_id` 可在不同进程并发执行 run，锁失效。
- **风险（量化）**｜`app.state.session_locks`（`dict[session_id, threading.Lock]`）只增不删：`_release_session_run` 只 `pop` `active_runs`，不清理 `session_locks`，每个新 `session_id` 都会 `setdefault` 一个永久残留的 Lock 对象。**严重性低**：每个 `threading.Lock` 对象体积很小（约几十字节），即便上千 `session_id` 也只占几十 KB；真正的风险不在内存，而在 `active_runs` 字典里残留的 `run_id` 可能因清理逻辑 bug 让同 session 的新 run 误判旧 run 仍活跃（需确认 `active_runs` 的清理时机）。长期运行仍建议定期清理 `session_locks`。
- **已确认**｜run 在 daemon 线程跑（`api.py` 的 run 启动逻辑用 `threading.Thread(..., daemon=True)`）：进程被强杀时，run 状态可能停在 `running`，靠下次启动 `fail_incomplete_runs` 兜底（lifespan 启动调用 + `run_ledger.py` 的 `fail_incomplete_runs` 函数）。
- **已确认**｜`dsagents_runs.db` 与 `data/internal/run-events/` 只增不删，无 TTL/归档/压缩（`run_ledger.py` 无清理方法）；raw chunk 长期留存（见原 §2，调试有利但占空间且保留模型/错误细节）。

## 12. 程序内入口

- **已确认**｜旧的 `from session import run_session` 已不存在；如需库式调用，只能显式组合 `AgentResources` + `create_harness(resources)` + `harness.execute_run(...)`（见 `AGENTS.md`、`harness.py:134`）。非缺陷，仅作记录以防误用。

## 13. Skills / 业务 artifact 风险

- **已确认**｜本地 assert 脚本覆盖装配、投票、裁决、严格合同与工作簿关键单元格，但不调用真实模型/MinerU/Oracle。真实 extractor 对 PDF 的准确性、同一回合并行 task 以及 Oracle 命中率仍需独立真实集成验证。
- **风险**｜Philips/Tecan 的 Excel 模板是二进制资产，格式或固定行位置变化不会产生可读 diff；更新模板时必须重跑对应工作簿断言并人工抽查渲染结果。
- **风险**｜`backend/skills/` 是源码目录路由，当前 `uv sync` 的 editable 开发运行可用；若未来改为仅分发 wheel，非 Python Skill/模板是否被打包需另行确认。
- **已确认**｜业务 JSON/Excel 只增不改且无 registry/清理策略；高频运行会持续增长 `data/artifacts/downloads/`，生命周期由部署方管理。

## 14. prompt-cache 观测与成本估算（MiniMax-M3）

- **已确认**｜`model_usage` 事件 + `GET /runs/{run_id}` 顶层 `usage` 已落库。usage 在 `harness.execute_run` 的 `messages` 分支、subagent 文本过滤之前提取终态 `AIMessageChunk.usage_metadata`，每个模型调用仅在非空时写一次；`model` 固定为常量 `"MiniMax-M3"`（第一阶段只有这一个模型，不读 env，避免观测链路引入 env 耦合）。token 计数是原始事实，`estimated_cost_cny` / `estimated_savings_cny` 是**趋势估算**，最终账单以 MiniMax 为准。
- **已确认**｜`AnthropicPromptCachingMiddleware` 由 `create_deep_agent` 尾栈自动挂，仓库不新增 cache middleware。固定前缀 = `DEFAULT_SYSTEM_PROMPT` + tool schema + SDK 默认 prompt，未注入动态内容，保持被动 prompt cache 友好。
- **风险**｜定价常量（标准/长上下文两档，CNY/M）硬编码在 `api.py`（`_PRICING_TIERS` / `_PRICEABLE_MODELS` / `pricing_as_of`），未做配置中心；MiniMax 调价或新增模型时需手动改代码并更新 `pricing_as_of` 日期。任一调用不可计价 → 整 run 金额 `null`（不输出系统性偏低的部分金额），token 仍完整。
- **风险**｜cache hit 取决于 provider 5m ephemeral cache 是否命中，跨会话重复文档只记录数据、不承诺命中；第一阶段不设命中率门禁。库语义（usage 仅在终态 `message_delta` 一次出现）已通过库源码确认，但真实 MiniMax 端点是否回传 `cache_read_input_tokens` / `cache_creation_input_tokens` 需 `test_minimax_cache_baseline.py` 真实跑一轮确认——缺失时这些字段为 0，不影响 input/output token。
