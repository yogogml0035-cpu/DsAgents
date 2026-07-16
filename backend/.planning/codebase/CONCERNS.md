---
last_mapped_commit: 3a3a6e5c3f608a05ae5a076b99812723c097613e
analysis_date: 2026-07-16
scope: backend/
---

# Codebase Concerns

**Analysis Date:** 2026-07-16

> backend 技术债、风险与脆弱点。每条带代码证据（路径 / 行为）。状态分 **已确认**（源码可证）与 **需确认**（推断，需人工核实）。本轮核对：`api.py`、`runtime/`、`integrations/`、两个内置 Skill、`tests/`、`pyproject.toml`、`.env.example`。不读取 `.env` 内容，不记录任何密钥或连接串值。

## Tech Debt

### 1. 定价常量硬编码（已确认）

- **问题**：`api.py` 中 `PRICING_AS_OF`（`"2026-07-12"`）、`_TIER_THRESHOLD_INPUT_TOKENS = 512 * 1024`、`_PRICING_TIERS`（standard / long_context 两档 CNY/M）、`_PRICEABLE_MODELS = {"MiniMax-M3"}` 全部写死在源码。
- **影响**：MiniMax 调价、新增模型或改 tier 阈值时必须改代码并同步 `PRICING_AS_OF`。模型不在可计价集合 → 整 run 的 `estimated_cost_cny` / `estimated_savings_cny` 为 `null`（token 计数仍完整）。
- **修复方向**：外置定价配置（文件或 env JSON），保留「不可计价则金额 null」语义；测试继续覆盖 unpriceable / zero-input 分支。

### 2. stream chunk 形状无版本契约（已确认）

- **问题**：`runtime/execution.py` 与 `runtime/observability.py` 依赖 langchain / deepagents 内部 chunk 约定：
  - `chunk["type"]` ∈ `messages` / `custom` / `updates`
  - `thinking` / `reasoning` / `non_standard` block 与 `usage_metadata` / cache token 字段
  - SubAgent 识别靠 `lc_agent_name` 与 `MAIN_AGENT_NAME`
- **证据**：`pyproject.toml` 依赖均为 `>=` 无上限（如 `deepagents>=0.6.12`、`langchain>=1.3.11`、`langgraph>=1.2.7`），具体版本仅靠 `uv.lock`。
- **影响**：主/次版本升级可能静默破坏事件解析、usage 统计或 subagent 文本过滤。
- **修复方向**：升级前用 `tests/test_harness.py` + `FakeBrain` 回归；评估关键包上限或锁定策略。

### 3. MiniMax 强绑 Anthropic 客户端协议（已确认）

- **问题**：`runtime/agent.py` 的 `DeepAgentsBrainFactory` 使用 `init_chat_model(f"anthropic:{MINIMAX_MODEL}", api_key=..., base_url=..., thinking={"type":"adaptive"})`；`StructuredOutputCompatibility` 仅在 `ToolStrategy` 请求中关闭 thinking；模块导入时 `register_harness_profile("anthropic", ...)` 禁用 general-purpose SubAgent。
- **影响**：换 provider / 改 profile 名时可能自动多出 SubAgent，或 thinking / prompt-cache 中间件行为变化。
- **修复方向**：Brain 工厂参数化 provider profile；切换模型时显式回归 `workflow_subagents()`、`ToolStrategy` 与 cache 行为。

### 4. 持久化只增不删，无 TTL / 归档（已确认）

- **问题**：
  - `runtime/runs.py` 无 delete / truncate / vacuum；`runs` / `run_events` 只追加。
  - 大 payload spill（`max_inline_bytes=262_144`）落 `data/internal/run-events/`，只增不删。
  - 业务 JSON/Excel 与 MinerU 产物落 `data/artifacts/downloads/`，只增不改、无 registry。
- **影响**：高频运行磁盘持续增长；`raw` chunk 长期保留模型与错误细节。
- **修复方向**：部署侧定期归档/清理；可选按 `created_at` 的保留窗口与 spill GC。

### 5. fresh schema、无迁移（已确认）

- **问题**：`SqliteRunLedger._setup` 仅 `create table if not exists`，无 `pragma user_version` / `_migrate`。
- **影响**：schema 变更不能就地升级旧库；旧库直接复用可能导致读端解析失败。
- **修复方向**：部署切换时停服务并整体清空 `backend/data/`（三库 + artifacts + internal）；若需在线升级再引入显式迁移。

### 6. 无 CI 门禁与静态检查配置（已确认）

- **问题**：`pyproject.toml` 无 pytest / coverage / ruff / mypy / black 配置；回归靠 `python -m tests.test_xxx` 人工选择。
- **影响**：未强制的脚本易被漏跑；真实集成脚本与本地脚本同目录，误跑风险见下方 Testing Gaps。
- **修复方向**：CI 固定跑本地 assert 脚本；真实集成单独 job + env 开关。

### 7. 真实集成脚本与本地回归同目录（已确认）

- **问题**：
  - `tests/test_real_image_run.py` / `test_real_multi_pdf_run.py`：`run()` 需 `DSAGENTS_RUN_REAL_*_TEST=1`，但 `python -m ...` 走 `main()` 会立即打真实服务。
  - `tests/test_minimax_cache_baseline.py`：`__main__` 直接 `run()`，无 env 开关，会打真实 HTTP/MiniMax。
- **影响**：误跑产生费用与外部副作用；若引入 pytest 全收集更危险。
- **修复方向**：真实脚本移到 `tests/manual/` 或强制双重开关；CI 永不收集。

### 8. 无自动化聚合入口（已确认）

- **问题**：无 `self_check` / tox / CI workflow 绑定本地脚本套件。
- **影响**：局部改动易漏跑 `test_workflow_setup` 或业务 Skill 测试。
- **修复方向**：单命令跑本地套件（排除真实集成）。

## Known Risks / Bugs

### 1. 结构化输出恢复：必须显式 `jump_to: "end"`，否则 model↔model 无限循环（已确认，已缓解）

- **问题历史**：Philips workflow 使用 `ToolStrategy` 且业务图上无 tool 时，`create_agent` 在缺少 `structured_response` 时会走 model→model 边。若 `after_model` 在重试耗尽后只返回 `None`，图不会退出。
- **当前缓解**（`runtime/middleware.py` `StructuredOutputRecovery`）：
  - `@hook_config(can_jump_to=["model", "end"])` — **必须**同时声明 `"end"`，禁止只允许 `"model"`
  - 解析/校验失败或空文本：`jump_to: "model"`，计数 `structured_recovery_attempts`，默认 `max_retries=2`
  - 耗尽重试：**必须** `return {"jump_to": "end"}`（源码注释：`Returning None would infinite-loop; jump to end instead.`）
  - 空 `data: {}` / 缺 `shipment|header|items`：专用 `EMPTY_DATA_SHELL_HINT` + `philips_structured_output_error_message`（ToolStrategy `handle_errors`）；不编造业务字段
  - Skill / `PHILIPS_WORKFLOW_PROMPT` 硬约束禁止空壳提交
- **测试证据**：`tests/test_harness.py` 覆盖 exhausted → `jump_to == "end"`、graph 上 `initial + max_retries` 次调用后封顶、空 data 壳文本/ToolMessage 路径专用纠错。
- **残留风险**：若未来重构去掉 `can_jump_to` 中的 `"end"`、或改写 `_retry_or_give_up` 为返回 `None`，无限循环会回归。模型仍可能在专用提示后重复空壳，最终由 `NoProgressMiddleware` 或 recovery 耗尽结束。改 middleware 后必须跑 `cd backend && python -m tests.test_harness`。
- **关联**：耗尽后 harness 仍可能 `structured_response missing` → run `failed`（`runtime/execution.py`），属预期失败路径，不是挂死。

### 2. workflow 工具收窄必须用 denylist，禁止业务-only allowlist（已确认，已缓解）

- **正确模式**（`runtime/agent.py`）：
  - 静态全量目录 `default_tool_catalog()` 含 5 个工具：`parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`、`save_tecan_extraction`、`generate_tecan_import`（`runtime/tools.py`）。
  - Philips workflow 用 **denylist** `_PHILIPS_EXCLUDED_TOOLS = {"save_tecan_extraction", "generate_tecan_import"}` 只排除**其他业务**（帝肯）工具。
  - **共享 MinerU 工具** `parse_documents` / `extract_archives` **必须保留**，与 `/memories/AGENTS.md` ZIP 指引及 `skills/philipswgqinboundrecognition/SKILL.md` 固定流程一致。
- **错误模式**：若改成「只 allowlist Philips 业务工具」或「只 allowlist `lookup_philips_wgq_master_data`」，模型工具表会丢失手册里的通用解析/解压能力，导致：
  - 模型无法按 SKILL 调用 `parse_documents` / 对 MinerU `archive_path` 调用 `extract_archives`；
  - handbook 与工具表不一致，行为漂移难排查。
- **测试门禁**：`tests/test_workflow_setup.py` 用真实 catalog 断言 Philips 工具名集合 **含** `extract_archives` / `parse_documents`，**不含**帝肯工具；注释写明 denylist drift 检测。验证命令：`cd backend && python -m tests.test_workflow_setup`。
- **新增 Skill 时**：在 `default_tool_catalog()` 追加静态注册；其他 workflow 收窄时继续 **denylist 排除他业务工具**，勿 allowlist 收窄到业务-only。

### 3. 单飞锁仅进程内有效（已确认）

- **问题**：`api.py` `_acquire_session_run` 用 `app.state.session_locks: dict[str, threading.Lock]` + `registry_lock` 实现同 `session_id` 串行。
- **影响**：`uvicorn --workers N` 或多实例时，同一 `session_id` 可跨进程并发；LangGraph `thread_id=session_id`（`runtime/execution.py`）的 checkpointer 可能交错写入。
- **修复方向**：单 worker 部署写死运维约束；多实例需分布式锁或 session 粘性路由。

### 4. `session_locks` 字典只增不删（已确认）

- **问题**：`setdefault(session_id, threading.Lock())` 永不清理；`active_runs` 在 `_release_session_run` 中 pop。
- **影响**：Lock 对象通常很小；若 `session_id` 每次随机 UUID，字典无限增长。真正风险是释放逻辑 bug 导致 `active_runs` 残留 → 同 session 永久 `409`。
- **修复方向**：可选 LRU/TTL 清理；释放路径加断言/指标。

### 5. 取消是协作 drain，非强杀（已确认）

- **问题**：`POST /runs/{run_id}/cancel` → `RunControl.request_drain`；`execute_run` 在 chunk 间检查 `control.drain_requested` 并捕获 `GraphDrained` → `cancelled`。工具/网络阻塞期间可能长时间不回到检查点。
- **证据**：`runtime/execution.py` `request_cancel` / `GraphDrained`；`api.py` cancel 在无 `RunControl` 时直接标 `cancelled`。
- **影响**：卡死工具（如 MinerU 长轮询）下取消延迟；取消不回滚已写 `downloads/` 文件。
- **修复方向**：工具层尊重超时；文档明确「取消不回滚 artifacts」。

### 6. daemon 线程 + 启动兜底（已确认）

- **问题**：`api.py` 用 `threading.Thread(..., daemon=True)` 跑 run；进程被强杀时状态可能停在 `queued`/`running`/`cancelling`。
- **缓解**：lifespan 启动调用 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` 标 `failed`。
- **影响**：强杀后需重启才纠正投影；中断瞬间前端可能看到中间态。
- **修复方向**：保持 lifespan 兜底；运维避免 SIGKILL。

### 7. `NoProgressMiddleware` 仅为启发式（已确认）

- **问题**：`NO_PROGRESS_WINDOW = 3`；`runtime/middleware.py` 仅检测最近 HumanMessage 之后，同一 `tool + 归一化 args` 连续 3 次；从 message state 派生，非业务进度证明。
- **影响**：A/B 交替失败调用不会触发；args 微调可绕过；窗口不可配置。
- **修复方向**：可配置窗口；可选总 tool-call 上限；跨工具振荡检测。

### 8. 声明式 SubAgent 不继承主 Agent middleware（已确认）

- **问题**：`workflow_subagents()` 经 extractor 显式注入 `runtime_middlewares()`（无 handbook）。主 Agent 另传 `memory_backend` 追加 `MemoryMiddleware`。
- **影响**：只在主 Agent 装配处新增 middleware 而忘记 `runtime_middlewares()` → SubAgent 静默缺少 no-progress / 遥测；若误给 SubAgent 传 `memory_backend` 会重复注入手册。
- **修复方向**：共享 middleware 只改 `runtime_middlewares()`；`tests/test_workflow_setup.py` 继续断言 SubAgent 无 `MemoryMiddleware`。

### 9. Oracle 初始化与整批查询异常合并为一个问题（已确认）

- **问题**：`skills/philipswgqinboundrecognition/scripts/tools.py` 的 `_oracle_data` 用一个宽 `except Exception` 包住 client 初始化、连接与全部 12NC 查询；任一步失败都返回单条「Oracle 查询失败」problem。配置缺失与逐料号未命中能单独区分。
- **影响**：业务可保留 PDF/Tracking 数据并形成 `partial_success`，但调用方不能仅凭 problem 区分 Instant Client、网络、SQL 或整批失败。
- **修复方向**：如运维确需定位，再把初始化/连接与逐料号查询分开记录；保持不抛弃已有识别结果。

### 10. cancel 与 execute_run 启动竞态（需确认）

- **问题**：`cancel_run` 先投影 `cancelling`，再 `request_cancel`；若返回 `False`（尚无 `RunControl`）则直接 `cancelled`。同时 `_run_background` 可能刚进入 `execute_run` 注册 control。
- **影响**：极端时序下可能出现「已 cancelled 投影」与随后 `running`/`succeeded` 事件交错（取决于 ledger 写顺序与线程调度）。
- **修复方向**：用 ledger 状态机约束终态不可再前进；cancel 与 register control 共用同一把 per-run 锁。

### 11. 并发 / 多 worker / SQLite 锁压力未覆盖（已确认）

- **问题**：本地测试用 `TestClient` + 单进程；无多 worker、无 `runs.db` 写锁压力测试。
- **影响**：单飞锁跨进程与 SQLite 写争用只能在部署暴露。
- **修复方向**：可选压测：同 session 冲突、跨 session 并行 emit、busy timeout。

### 12. Oracle 普通回归仅使用替身（已确认）

- **问题**：`tests/test_philips_wgq_inbound_recognition.py` 用 fake connection 覆盖命中、配置缺失、查询失败和未命中；普通回归无真实 DB 连通测试。
- **影响**：SQL、Instant Client 与真实超时行为不在默认门禁内。
- **修复方向**：发布前 Oracle 探针保持 opt-in，与普通回归隔离。

### 13. MiniMax cache 字段端到端需人工（需确认）

- **问题**：库侧 usage 规范化已有单测；真实端点是否回传 `cache_read_input_tokens` / `cache_creation_input_tokens` 依赖 `test_minimax_cache_baseline.py` 人工跑。
- **影响**：缺失时字段为 0，`cache_hit_rate` 失真，计价 savings 偏低。
- **修复方向**：定期跑基线；端点变更时重验。

## Security

### 1. HTTP API 无鉴权 / 无用户隔离（已确认）

- **问题**：`api.py` `create_app` 未注册 auth middleware；`/upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel` 全部匿名。
- **影响**：任何能访问端口的客户端可上传文件、创建 run、读取任意 `run_id` 的事件与 reply/error。
- **修复方向**：网关鉴权、mTLS 或应用层 token；run/session 与主体绑定。

### 2. 无 CORS 配置（已确认）

- **问题**：`api.py` 无 `CORSMiddleware`。
- **影响**：浏览器跨域调用不会按应用策略放行；若前置网关另配 CORS，需与匿名 API 风险一并评估。
- **修复方向**：若需浏览器直连，显式白名单 origin；否则保持无 CORS 并由同源网关代理。

### 3. `/upload` 无大小 / 类型 / 数量限制（已确认）

- **问题**：`post_upload` / `_store_upload` 直接 `shutil.copyfileobj`，无 max size、MIME 白名单、单批数量上限。
- **影响**：匿名可写下磁盘直至占满卷。文件名经 `clean_filename` / `make_timestamped_name` 处理，路径穿越风险较低。
- **修复方向**：限制单文件与总批大小、扩展名白名单、单请求文件数；磁盘配额监控。

### 4. 错误与 raw 未脱敏落库并可能回传（已确认）

- **问题**：`runtime/execution.py` / `api.py` 将 `str(exc)` 写入 `error`，`repr(exc)` 写入 `raw`；`GET /runs/{run_id}` 返回事件与 run 投影。`runtime/runs.py` 对未知对象可走 `repr`。
- **影响**：provider/MinerU/Oracle/路径类错误细节可能暴露给调用方；调试有利但生产面过大。
- **修复方向**：对外错误码 + 简短消息；完整 traceback 仅服务端日志；raw 默认不返回或按角色裁剪。

### 5. 密钥经 `os.getenv` / `.env` 注入，禁止入仓（已确认）

- **问题**：
  - `runtime/agent.py`：`load_dotenv(BACKEND_ENV_PATH)` 后读 `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` / `MINIMAX_MODEL`
  - `integrations/mineru.py`：同样 `load_dotenv`，读 `MINERU_*`
  - Philips Oracle：`ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS`
  - 模板：`backend/.env.example`；真实密钥应在 `backend/.env`（根 `.gitignore` 忽略 `backend/.env` 与 `backend/.oracle/`）
- **影响**：密钥不进仓库前提下仍可能经进程环境、异常文本或日志泄漏；任何把 `.env` 提交或打印环境的操作都是事故面。
- **修复方向**：禁止把 env **值**写入事件/日志（只打键名）；部署用密钥管理；代码评审禁止提交 `.env`。

### 6. `parse_documents` 允许本地绝对路径（已确认）

- **问题**：`integrations/mineru.py` `_resolve_document_path` 调用 `resolve_artifact_path(..., allow_local=True)`；业务 Skill 的路径解析默认 `allow_local=False`（`integrations/artifacts.py`）。
- **影响**：模型若被诱导传入任意本地路径，可把主机可读文件提交给 MinerU（依赖运行用户权限）。
- **修复方向**：生产默认关闭 `allow_local`；仅测试/程序内入口显式开启。

### 7. 主 Agent 可写 `/artifacts/**`（已确认）

- **问题**：主 Agent 仅 deny 写 `/skills/**`（`runtime/agent.py` permissions）；SubAgent 全路径 write deny。
- **影响**：主 Agent 工具链可改写 artifacts 区文件（业务产物设计为 exclusive create，但通用文件工具仍可能覆盖/写入意外路径）。
- **修复方向**：收紧主 Agent 写权限到 `/artifacts/downloads/**` 或仅工具 API 写盘。

## Performance

### 1. `runs.db` 无 WAL / 无 busy_timeout（已确认）

- **问题**：`SqliteRunLedger` 每次操作 `sqlite3.connect(self.db_path)`，`_setup` 未设 `PRAGMA journal_mode=WAL` 或 `busy_timeout`。
- **影响**：run 执行期间高频 `emit_run_event` 与轮询 `get_run_events` 并发时，默认 delete journal + 默认 busy 超时易出现 `database is locked`。
- **修复方向**：连接时 `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000`（或更高）；评估长连接或写队列。

### 2. 三库独立连接、无跨库事务（已确认）

- **问题**：`dsagents_runs.db` / `dsagents_checkpoints.db` / `dsagents_store.db` 分属 ledger / SqliteSaver / SqliteStore（`runtime/resources.py`）。
- **影响**：run 事件与 checkpointer 状态无法原子一致；崩溃窗口靠 `fail_incomplete_runs` 与协作 cancel 语义兜底。
- **修复方向**：接受最终一致并文档化；避免假设「事件 succeeded ⇔ checkpoint 终态」强一致。

### 3. 事件流写放大（已确认）

- **问题**：每个 stream chunk 可写 `thinking` / `text_delta` / `tool_*` / `model_usage` 等事件，且常带 `raw=chunk`；超 `max_inline_bytes` 外溢到磁盘。
- **影响**：长思考 / 多工具 run 导致 SQLite 与 spill 目录膨胀，轮询响应变大。
- **修复方向**：raw 采样或仅 failed 保留；合并连续 text_delta；客户端善用 `after_event_id`。

### 4. MinerU 同步轮询阻塞工具线程（已确认）

- **问题**：`integrations/mineru.py` 默认 `MINERU_POLL_INTERVAL_SECONDS = 30.0`，总超时 `MINERU_TIMEOUT_SECONDS`（`.env.example` 示例 `7200`）；在工具调用栈内同步 `requests` + sleep 轮询。
- **影响**：占用 daemon 工作线程直至完成/超时；取消依赖协作点，轮询中可能延迟响应 cancel。
- **修复方向**：轮询循环检查 drain 标志；缩短 interval 可配置；避免同进程过多并行长解析。

### 5. Oracle 按 12NC 串行查询（已确认）

- **问题**：`_oracle_data` 在同一连接上对每个 `product_id` 循环 `cursor.execute`（`skills/philipswgqinboundrecognition/scripts/tools.py`）。
- **影响**：大批量料号时延迟线性增加；受 `ORACLE_TIMEOUT_SECONDS`（默认 30）与 `call_timeout` 约束。
- **修复方向**：如业务需要，可评估批量 IN 查询或连接池；保持超时与 problem 降级语义。

## Fragile Areas

### 1. 事件类型与 `GET /runs` 契约

- 事件类型固定由 `execute_run` 写入：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`。
- `latest_content_event` 排除 `status` 与 `model_usage`；改过滤条件会破坏前端轮询语义。
- 触达：`runtime/execution.py`、`runtime/runs.py`、`api.py`、`tests/test_api.py`、`tests/test_harness.py`。

### 2. run 状态机与 cancel

- 合法状态：`RUN_STATUSES` in `runtime/runs.py`（`queued` / `running` / `succeeded` / `failed` / `cancelled` / `cancelling`）。
- 路径：`queued→running→succeeded|failed`；`queued→cancelled`；`running→cancelling→cancelled`。
- 改 cancel 必须同步 `request_cancel`、`GraphDrained`、`fail_incomplete_runs` 与 `tests/test_api.py` cancel 覆盖。

### 3. middleware 与 SubAgent 装配（含 StructuredOutputRecovery / jump_to）

- 只在 `runtime/middleware.py` 实现并通过 `runtime_middlewares()` 增删共享 middleware；主 Agent 手册加载在 `execution.py` 用 `memory_backend=` 打开，勿同时使用 `create_deep_agent(memory=...)`。
- Philips workflow 在 `DeepAgentsBrainFactory.create` 中 **缺则补齐** `StructuredOutputCompatibility`（append）与 `StructuredOutputRecovery`（insert(0)），已有实例不重复；无 SubAgent。Tecan extractor 各自装配无 memory 的 middleware。
- **改 `after_model` / `jump_to` / `can_jump_to` 时**（Agents.md 硬性约定）：
  1. `can_jump_to` 必须含 `"model"` **与** `"end"`；
  2. `max_retries` 耗尽或无法产出 `structured_response` 时显式 `jump_to: "end"`；
  3. 禁止只返回 `None` 依赖默认边退出——在仅有 `ToolStrategy`、无业务 tool 的图上会触发 model↔model 无限循环；
  4. 用 `cd backend && python -m tests.test_harness` 验证重试次数封顶。
- `register_harness_profile("anthropic", ...)` 为进程级全局副作用；测试或二次 import 需注意。
- 触达：`runtime/middleware.py`、`runtime/execution.py`、`runtime/agent.py`、`tests/test_harness.py`、`tests/test_workflow_setup.py`。

### 4. workflow 工具 denylist 与共享 MinerU 工具表

- 收窄 `tools` 时必须 denylist **其他业务**工具，**保留** `parse_documents` / `extract_archives`。
- 证据：`runtime/agent.py` `_PHILIPS_EXCLUDED_TOOLS` + 过滤列表推导；`tests/test_workflow_setup.py` 断言集合。
- 触达：`runtime/agent.py`、`runtime/tools.py`、`runtime/resources.py`（handbook ZIP 指引）、Philips/Tecan `SKILL.md`、`tests/test_workflow_setup.py`。
- 回归：`python -m tests.test_workflow_setup`。

### 5. artifact 路径安全

- 虚拟路径解析与 `..` 拒绝：`integrations/artifacts.py` `resolve_artifact_path`。
- 业务写盘用 `unique_download_path` / `write_json_artifact`（不可覆盖）。
- 改 `allow_local` 默认值会影响 MinerU 与测试夹具路径。

### 6. Skill 业务工具契约

- Philips 只使用 `lookup_philips_wgq_master_data`，最终合同是 `PhilipsWgqRecognitionResult`；结构化响应缺失/非法令 run `failed`，业务 `input_problems` 仍令 run `succeeded`。
- Philips schema/Tracking/Oracle：`skills/philipswgqinboundrecognition/`；Tecan：`save_tecan_extraction` + `generate_tecan_import`，逻辑在 `skills/tecanimport/`。
- 改 Philips 字段或主数据规则必须同步 `SKILL.md`、schema、业务测试和真实验收；改 Tecan 字段/模板同步 references、assets 与 `test_tecan_import.py`。

### 7. 依赖升级清单

- 升级 `deepagents` / `langgraph` / `langchain-anthropic` 前：
  1. `uv lock` 后跑全部本地脚本；
  2. 核对 `FakeBrain` 契约（`stream_mode` / `subgraphs` / `version=v2` / `RunControl`）；
  3. 核对 harness profile API 是否仍用 `register_harness_profile`；
  4. 抽样真实 run 验证 usage 与 cancel。

### 8. 存储与部署一致性

- 不要单独替换三库之一；schema 变更默认整清 `data/`。
- 多 worker 前先解决 session 单飞与 SQLite 写模型。
- 生产若启用 Philips 单位查询，必须外部提供 Oracle Instant Client；MinerU 必须可达。

### 9. 外部依赖矩阵

| 依赖 | 证据 | 风险 | 降级 / 备注 |
|------|------|------|-------------|
| MiniMax via Anthropic 兼容客户端（LLM provider） | `runtime/agent.py` `init_chat_model("anthropic:...")` + middleware ToolStrategy 兼容 | 协议/thinking/cache/tool_choice 变更；无启动期三键校验 | 无本地降级；run 进入 running 后 `failed` |
| deepagents / langgraph / langchain | `pyproject.toml` `>=` + `uv.lock` | stream 形状、SubAgent、RunControl | 锁文件 + FakeBrain 回归 |
| MinerU HTTP | `integrations/mineru.py` `requests`；键 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` / `MINERU_EFFORT` | 服务不可用/超时/状态枚举变化；同步轮询最长可达超时上限 | 缺必填键 fail-fast；工具失败 → 可能整 run failed |
| Oracle + Instant Client（thick mode） | `skills/philipswgqinboundrecognition/scripts/tools.py`；键见下节 | thick client 缺失、网络、SQL、串行 12NC | 写入 `problems`，保留 PDF/Tracking；run 不崩 |
| openpyxl | Philips `scripts/tools.py`、Tecan `documents.py` | Tracking 表头/sheet 或模板变化 | Philips 返回 problem；Tecan 由业务回归锁单元格 |
| SQLite 三库 | `runtime/resources.py` / `runs.py` | 锁、磁盘、无迁移、无 WAL | checkpoints/store 由 LangGraph 管理 |
| FastAPI / uvicorn / python-multipart | `api.py` | 上传无限制、无鉴权 | 依赖部署面防护 |

## Operational Prerequisites

### 1. Oracle thick mode 与 `ORACLE_CLIENT_LIB_DIR`（已确认）

- **前提**：Philips 主数据补齐 **可选** 依赖 Oracle。Instant Client **不在仓库**；`.env.example` 说明放置于 `backend/.oracle/instantclient/instantclient_19_31`（或等价路径）并将 `ORACLE_CLIENT_LIB_DIR` 设为 **绝对路径**；`.oracle/` 被 gitignore。
- **代码路径**（`skills/philipswgqinboundrecognition/scripts/tools.py`）：
  1. `_oracle_data` 读取 `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD`；
  2. 三凭证齐全时 `_init_oracle_client(os.getenv("ORACLE_CLIENT_LIB_DIR"))` → 若 `lib_dir` 非空且进程内尚未初始化，则 `oracledb.init_oracle_client(lib_dir=lib_dir)`（全局 `_ORACLE_CLIENT_INITIALIZED` 只一次）；
  3. `oracledb.connect(...)` + 按 12NC 串行 `cursor.execute(_ORACLE_SQL, ...)`。
- **配置键**（仅键名，无值）：`ORACLE_DSN`、`ORACLE_USERNAME`、`ORACLE_PASSWORD`、`ORACLE_CLIENT_LIB_DIR`、`ORACLE_TIMEOUT_SECONDS`（默认 30）。
- **行为 / 优雅降级**：
  - 三凭证任一缺失 → **不抛**，跳过查询，`problems` 含「Oracle 配置缺失」；
  - `ORACLE_CLIENT_LIB_DIR` 为空 → **不**调用 `init_oracle_client`（依赖 thin/默认路径行为；需 thick 的部署必须显式配置 lib 目录）；
  - client 初始化或查询异常 → `problems` 含「Oracle 查询失败」（不抛穿工具，不崩 run）；
  - 单个 12NC 无记录 → 对应「Oracle 未命中」problem；
  - Tracking `.xlsx` 已有值优先，Oracle **仅补齐**仍为 `null` 的 `ORACLE_FIELDS`；
  - Tecan Skill **不**消费 Oracle。
- **运维清单**：需要 thick mode 的环境校验 Instant Client 目录存在、架构匹配、`ORACLE_CLIENT_LIB_DIR` 指向正确、DSN/账号可达；用结果 `problems` 观测降级。生产 Oracle 不可用时 run 不崩，但中文品名、规格型号、原产国、海关编码与计量单位等缺失字段无法补齐。

### 2. MinerU 必需环境变量 fail-fast（已确认）

- **前提**：`MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 缺失即 `RuntimeError`（`_required_env`）；`MINERU_EFFORT` 可为 `""` 并原样提交。
- **协议假设**：`POST {base}/tasks` → 轮询 `status_url` → 下载 `result_url`（JSON 或 ZIP）；状态枚举 `pending` / `processing` / `completed` / `failed`。
- **影响**：未配 MinerU 时文档解析工具直接失败，整 run 可能 `failed`；默认长超时（示例 7200s）占用工作线程。
- **运维清单**：部署健康检查验证三键与服务可达；文档标明 `MINERU_EFFORT` 空值语义；注意与 cancel 协作点的延迟。

### 3. MiniMax / LLM provider 三键（已确认）

- **前提**：`MINIMAX_API_KEY` / `MINIMAX_BASE_URL` / `MINIMAX_MODEL`（见 `.env.example`；示例 base `https://api.minimaxi.com/anthropic`，model `MiniMax-M3`）。
- **问题**：`DeepAgentsBrainFactory` 无启动期校验，`os.getenv` 可能得到 `None` 直至首次 `create`/调用。
- **影响**：run 进入 `running` 后才失败并透传错误；无本地模型降级路径。
- **运维清单**：lifespan 或工厂构造时校验非空；密钥轮换后重启进程以刷新 `load_dotenv` 已加载环境（取决于 dotenv 与进程生命周期）。

### 4. 部署数据目录生命周期（已确认）

- **前提**：`ResourceConfig` 将数据固定在 `backend/data/`（与 CWD 无关）；含三 SQLite、uploads/downloads、run-events spill。
- **影响**：备份/迁移必须整目录一致；混用旧库危险（见 Tech Debt §5）。
- **运维清单**：停服 → 备份/清空 → 启服；监控磁盘；不要单独替换三库之一。

### 5. 进程模型与单飞约束（已确认）

- **前提**：session 单飞锁与 daemon 工作线程均在单进程内有效。
- **运维清单**：默认单 worker；多 worker/多副本前先设计分布式锁或粘性路由；强杀后依赖 lifespan `fail_incomplete_runs` 纠正投影。

### 6. 取消与中断后的孤儿文件（已确认）

- **问题**：cancel / fail 不删除已生成 downloads；MinerU 部分产物与 Tecan 业务 Excel 可能残留。
- **影响**：磁盘泄漏与「幽灵」文件被后续 run 误引用（若调用方仍持有旧路径）。
- **运维清单**：定期 GC 无引用文件；可选按 run_id 前缀命名或 registry。

### 7. 包与运行时入口（已确认）

- **前提**：包管理用 `uv`（`cd backend && uv sync`），勿用 `pip install -e .` 绕过 `uv.lock`。
- **入口**：HTTP `POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`；程序内 `AgentResources` + `create_harness(...).execute_run(...)`。
- **运维清单**：变更 backend 后先同步 `backend/.planning/codebase/`，再按影响更新根级系统文档；真实模型 / MinerU / Oracle 测试与本地回归分开。

### 8. 关键回归命令（改 fragile 区域后）

| 场景 | 命令 |
|------|------|
| middleware `jump_to` / 重试封顶 | `cd backend && python -m tests.test_harness` |
| Philips 工具 denylist / 共享 MinerU | `cd backend && python -m tests.test_workflow_setup` |
| API / cancel / input_problems 投影 | `cd backend && python -m tests.test_api` |
| ledger / spill | `cd backend && python -m tests.test_run_ledger` |
| 工具与 MinerU 客户端（可 mock） | `cd backend && python -m tests.test_tools` |

---

*Concerns analysis: 2026-07-16*
