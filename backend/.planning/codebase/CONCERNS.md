---
last_mapped_commit: 08413f4688e03e5a24fb8ac08270541d280aee5d
analysis_date: 2026-07-16
scope: backend/
---

# Codebase Concerns

**Analysis Date:** 2026-07-16

> backend 技术债、风险与脆弱点。每条均带代码证据（路径 / 行为）。状态分 **已确认**（源码可证）与 **需确认**（推断，需人工核实）。本轮核对工作树：`api.py`、`runtime/`、`integrations/`、两个内置 Skill、`tests/`、`pyproject.toml`。不读取 `.env`，不记录任何密钥或连接串值。

## Tech Debt

### 1. 定价常量硬编码（已确认）

- **问题**：`api.py` 中 `PRICING_AS_OF`（`"2026-07-12"`）、`_TIER_THRESHOLD_INPUT_TOKENS = 512 * 1024`、`_PRICING_TIERS`（standard / long_context 两档 CNY/M）、`_PRICEABLE_MODELS = {"MiniMax-M3"}` 全部写死在源码。
- **影响**：MiniMax 调价、新增模型或改 tier 阈值时必须改代码并同步 `PRICING_AS_OF`。任一调用模型不在可计价集合 → 整 run 的 `estimated_cost_cny` / `estimated_savings_cny` 为 `null`（token 计数仍完整）。
- **修复方向**：外置定价配置（文件或 env JSON），并保留「不可计价则金额 null」语义；测试继续覆盖 unpriceable / zero-input 分支。

### 2. stream chunk 形状无版本契约（已确认）

- **问题**：`runtime/execution.py` 与 `runtime/observability.py` 依赖 langchain / deepagents 内部 chunk 约定：
  - `chunk["type"]` ∈ `messages` / `custom` / `updates`
  - `thinking` / `reasoning` / `non_standard` block 与 `usage_metadata` / `input_token_details` / `ephemeral_*_input_tokens`
  - SubAgent 识别靠 `lc_agent_name` 与 `MAIN_AGENT_NAME`
- **证据**：`pyproject.toml` 依赖均为 `>=` 无上限（`deepagents>=0.6.12`、`langchain>=1.3.11`、`langgraph>=1.2.7` 等），具体版本仅靠 `uv.lock`。
- **影响**：主版本或次版本升级可能静默破坏事件解析、usage 统计或 subagent 文本过滤。
- **修复方向**：升级前用 `tests/test_harness.py` + `FakeBrain` 契约做回归；评估对关键包加上限或锁定策略；关键字段解析加防御性默认值。

### 3. MiniMax 强绑 Anthropic 客户端协议（已确认）

- **问题**：`runtime/agent.py` 的 `DeepAgentsBrainFactory` 使用 `init_chat_model(f"anthropic:{MINIMAX_MODEL}", api_key=..., base_url=..., thinking={"type":"adaptive"})`；`runtime/middleware.py` 的 `StructuredOutputCompatibility` 只在 `ToolStrategy` 请求中复制模型并关闭 thinking；模块导入时 `register_harness_profile("anthropic", ...)` 禁用 general-purpose SubAgent。
- **影响**：换 provider / 改 profile 名时，可能自动多出第五个 SubAgent，或 `thinking` / prompt-cache 中间件行为变化。
- **修复方向**：Brain 工厂参数化 provider profile；切换模型时显式回归 `workflow_subagents()` 数量、`ToolStrategy` 结构化结果与 cache 中间件行为。

### 4. 持久化只增不删，无 TTL / 归档（已确认）

- **问题**：
  - `runtime/runs.py` 无 delete / truncate / vacuum；`runs` / `run_events` 只追加。
  - 大 payload spill（`max_inline_bytes=262_144`）落 `data/internal/run-events/`，只增不删。
  - 业务 JSON/Excel 与 MinerU 产物落 `data/artifacts/downloads/`，只增不改、无 registry。
- **影响**：高频运行磁盘持续增长；`raw` chunk 长期保留模型与错误细节。
- **修复方向**：部署侧定期归档/清理策略；可选按 `created_at` 的保留窗口与 spill 文件 GC。

### 5. fresh schema、无迁移（已确认）

- **问题**：`SqliteRunLedger._setup` 仅 `create table if not exists`，无 `pragma user_version` / `_migrate`。
- **影响**：schema 变更不能就地升级旧库；旧扁平架构时期的库直接复用会导致读端解析失败。
- **修复方向**：部署切换时停服务并整体清空 `backend/data/`（三库 + artifacts + internal）；若需在线升级，再引入显式迁移。

### 6. 无 CI 门禁与静态检查配置（已确认）

- **问题**：`pyproject.toml` 无 `[tool.pytest...]` / coverage / ruff / mypy / black；回归靠 `python -m tests.test_xxx` 人工选择。
- **影响**：未强制的脚本易被漏跑；真实集成脚本与本地脚本同目录，误用风险见 Testing Gaps。
- **修复方向**：CI 固定跑本地 7 个 assert 脚本；真实集成单独 job + env 开关。

## Known Bugs / Fragile Behavior

### 1. 单飞锁仅进程内有效（已确认）

- **问题**：`api.py` `_acquire_session_run` 用 `app.state.session_locks: dict[str, threading.Lock]` + `registry_lock` 实现同 `session_id` 串行。
- **影响**：`uvicorn --workers N` 或多实例时，同一 `session_id` 可跨进程并发；LangGraph `thread_id=session_id` 的 checkpointer 可能交错写入。
- **修复方向**：单 worker 部署写死运维约束；多实例需分布式锁（或按 session 粘性路由）保护 `thread_id` 写。

### 2. `session_locks` 字典只增不删（已确认）

- **问题**：`setdefault(session_id, threading.Lock())` 永不清理；`active_runs` 在 `_release_session_run` 中 pop。
- **影响**：内存占用通常可忽略（Lock 对象很小）；若 `session_id` 每次随机 UUID，字典无限增长。真正风险是释放逻辑 bug 导致 `active_runs` 残留 → 同 session 永久 `409`。
- **修复方向**：可选 LRU/TTL 清理 `session_locks`；释放路径加断言/指标。

### 3. 取消是协作 drain，非强杀（已确认）

- **问题**：`POST /runs/{run_id}/cancel` → `RunControl.request_drain`；`execute_run` 在 chunk 间检查 `control.drain_requested` 并捕获 `GraphDrained` → `cancelled`。工具/网络阻塞期间可能长时间不回到检查点。
- **证据**：`runtime/execution.py` `request_cancel` / `GraphDrained`；`api.py` cancel 在无 `RunControl` 时直接标 `cancelled`。
- **影响**：卡死工具（如 MinerU 长轮询）下取消延迟；取消不回滚已写 `downloads/` 文件。
- **修复方向**：工具层尊重超时并尽量短事务；文档明确「取消不回滚 artifacts」；可选 cancel 超时后标记 failed。

### 4. daemon 线程 + 启动兜底（已确认）

- **问题**：`api.py` 用 `threading.Thread(..., daemon=True)` 跑 run；进程被强杀时状态可能停在 `queued`/`running`/`cancelling`。
- **缓解**：lifespan 启动调用 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` 标 `failed`。
- **影响**：强杀后需重启才纠正投影；中断瞬间前端可能看到中间态。
- **修复方向**：保持 lifespan 兜底；运维避免 SIGKILL；可选健康检查暴露 incomplete 计数。

### 5. `NoProgressMiddleware` 仅为启发式（已确认）

- **问题**：`NO_PROGRESS_WINDOW = 3`；`runtime/middleware.py` 仅检测最近 HumanMessage 之后，同一 `tool + 归一化 args` 连续 3 次，并跳过工具结果消息；它是从既有 message state 派生的启发式，不是业务进度证明。
- **影响**：A/B 交替失败调用不会触发；args 微调可绕过；阈值不可配置。
- **修复方向**：可配置窗口；可选总 tool-call 上限；跨工具振荡检测；保持判断基于消息状态，避免并发 run 共享实例级计数。

### 6. 声明式 SubAgent 不继承主 Agent middleware（已确认）

- **问题**：`workflow_subagents()` 经 `_extractor` 显式注入 `runtime_middlewares()`（无 handbook：`ToolTelemetry` + `NoProgressMiddleware` + `StructuredOutputCompatibility`）。主 Agent 另传 `memory_backend` 追加 `MemoryMiddleware`。
- **影响**：只在主 Agent 装配处新增 middleware 而忘记 `runtime_middlewares()` → SubAgent 静默缺少 no-progress / 遥测；若误给 SubAgent 传 `memory_backend` 会重复注入手册。
- **修复方向**：共享 middleware 只改 `runtime_middlewares()`；主 Agent 在 `execution.py` 传 `memory_backend`；`tests/test_workflow_setup.py` 继续断言 SubAgent 无 `MemoryMiddleware`。

### 7. Oracle 初始化与整批查询异常合并为一个问题（已确认）

- **问题**：`skills/philipswgqinboundrecognition/scripts/tools.py` 的 `_oracle_data` 用一个宽 `except Exception` 包住 client 初始化、连接与全部 12NC 查询；任一步失败都返回单条「Oracle 查询失败」problem。配置缺失与逐料号未命中能单独区分。
- **影响**：业务可保留 PDF/Tracking 数据并形成 `partial_success`，但调用方不能仅凭 problem 区分 Instant Client、网络、SQL 或某个料号查询导致的整批失败。
- **修复方向**：如运维确需定位，再把初始化/连接与逐料号查询分开记录；保持不抛弃已有识别结果，不新增重试框架。

### 8. cancel 与 execute_run 启动竞态（需确认）

- **问题**：`cancel_run` 先投影 `cancelling`，再 `request_cancel`；若返回 `False`（尚无 `RunControl`）则直接 `cancelled`。同时 `_run_background` 可能刚进入 `execute_run` 注册 control。
- **影响**：极端时序下可能出现「已 cancelled 投影」与随后 `running`/`succeeded` 事件交错（取决于 ledger 写顺序与线程调度）。
- **修复方向**：用 ledger 状态机约束终态不可再前进；cancel 与 register control 共用同一把 per-run 锁。

## Security

### 1. HTTP API 无鉴权 / 无用户隔离（已确认）

- **问题**：`api.py` `create_app` 未注册 auth middleware；`/upload`、`/runs`、`GET /runs/{run_id}`、`/cancel` 全部匿名。
- **影响**：任何能访问端口的客户端可上传文件、创建 run、读取任意 `run_id` 的事件与 reply/error。
- **修复方向**：网关鉴权、mTLS 或应用层 token；run/session 与主体绑定。

### 2. 无 CORS 配置（已确认）

- **问题**：`api.py` 无 `CORSMiddleware`。
- **影响**：浏览器跨域调用不会按应用策略放行（默认浏览器限制）；若前置网关另配 CORS，需与匿名 API 风险一并评估。
- **修复方向**：若需浏览器直连，显式白名单 origin；否则保持无 CORS 并由同源网关代理。

### 3. `/upload` 无大小 / 类型 / 数量限制（已确认）

- **问题**：`post_upload` / `_store_upload` 直接 `shutil.copyfileobj`，无 max size、MIME 白名单、单批数量上限。
- **影响**：匿名可写下磁盘直至占满卷。文件名经 `clean_filename` / `make_timestamped_name` 处理，路径穿越风险较低。
- **修复方向**：限制单文件与总批大小、扩展名白名单、单请求文件数；磁盘配额监控。

### 4. 错误与 raw 未脱敏落库并可能回传（已确认）

- **问题**：`runtime/execution.py` / `api.py` 将 `str(exc)` 写入 `error`，`repr(exc)` 写入 `raw`；`GET /runs/{run_id}` 返回事件与 run 投影。`runtime/runs.py` `_safe` 对未知对象走 `repr(value)`。
- **影响**：provider/MinerU/Oracle/路径类错误细节可能暴露给调用方；调试有利但生产面过大。
- **修复方向**：对外错误码 + 简短消息；完整 traceback 仅服务端日志；raw 默认不返回或按角色裁剪。

### 5. provider / 集成密钥经 `os.getenv` 直读（已确认）

- **问题**：`runtime/agent.py` 读 `MINIMAX_*`；`integrations/mineru.py` 读 `MINERU_*`（缺必需键 fail-fast）；Philips Oracle 读 `ORACLE_*` 且不记录值。
- **影响**：密钥不进仓库（`.env` 应被 gitignore），但进程环境与错误文本仍可能泄漏。
- **修复方向**：禁止把 env 值写入事件；日志只打键名。

### 6. `parse_documents` 允许本地绝对路径（已确认）

- **问题**：`integrations/mineru.py` `_resolve_document_path` 调用 `resolve_artifact_path(..., allow_local=True)`；业务 Skill 的 generator 默认 `allow_local=False`。
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

- **问题**：`integrations/mineru.py` 默认 `MINERU_POLL_INTERVAL_SECONDS = 30.0`，总超时 `MINERU_TIMEOUT_SECONDS`；在工具调用栈内同步 `requests` + sleep 轮询。
- **影响**：占用 daemon 工作线程直至完成/超时；取消依赖协作点，轮询中可能延迟响应 cancel。
- **修复方向**：轮询循环检查 drain 标志；缩短 interval 上限可配置；避免同进程过多并行长解析。

## Operational Risks

### 1. Oracle thick mode 外部 Instant Client（已确认）

- **问题**：Philips Skill `_oracle_data` 可按 `ORACLE_CLIENT_LIB_DIR` 初始化本机 Instant Client；client **不在仓库**。键：`ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS`（默认 30）。
- **行为**：
  - 三凭证不齐 → 跳过查询，`problems` 含「Oracle 配置缺失」。
  - client 初始化或查询异常 → `problems` 含「Oracle 查询失败」。
  - 单个 12NC 无记录 → 对应「Oracle 未命中」problem；所有稳定字段仍保持已有值或 `null`。
  - Tecan Skill **不**消费 Oracle。
- **影响**：生产 Oracle 不可用时 run 不崩，但中文品名、规格型号、原产国、海关编码与计量单位等缺失字段无法补齐，结果为 `partial_success`。
- **修复方向**：需要 thick mode 的部署清单校验 client 目录与连通性；用结果 `problems` 观测降级。

### 2. MinerU 必需环境变量 fail-fast（已确认）

- **问题**：`MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 缺失即 `RuntimeError`；`MINERU_EFFORT` 可为 `""` 并原样提交。
- **影响**：未配 MinerU 时文档解析工具直接失败，整 run 可能 `failed`。
- **修复方向**：部署健康检查验证三键；文档标明 `MINERU_EFFORT` 空值语义。

### 3. MiniMax 三键缺失时模型装配失败（已确认）

- **问题**：`DeepAgentsBrainFactory` 无启动期校验，`os.getenv` 可能得到 `None` 直至首次 `create`/调用。
- **影响**：run 进入 `running` 后才失败并透传错误。
- **修复方向**：lifespan 或工厂构造时校验 `MINIMAX_*` 非空。

### 4. 部署数据目录生命周期（已确认）

- **问题**：`ResourceConfig` 将数据固定在 `backend/data/`（与 CWD 无关）；含三 SQLite、uploads/downloads、run-events spill。
- **影响**：备份/迁移必须整目录一致；混用旧库危险（见 Tech Debt §5）。
- **修复方向**：运维 runbook：停服 → 备份/清空 → 启服；监控磁盘。

### 5. 取消与中断后的孤儿文件（已确认）

- **问题**：cancel / fail 不删除已生成 downloads；MinerU 部分产物与 Tecan 业务 Excel 可能残留。
- **影响**：磁盘泄漏与「幽灵」文件被后续 run 误引用（若调用方仍持有旧路径）。
- **修复方向**：按 run_id 前缀命名或 registry；定期 GC 无引用文件。

## Testing Gaps

### 1. 真实集成脚本与本地回归同目录（已确认）

- **问题**：
  - `tests/test_real_image_run.py` / `test_real_multi_pdf_run.py`：`run()` 需 `DSAGENTS_RUN_REAL_*_TEST=1`，但 `python -m ...` 走 `main()` 会立即打真实服务。
  - `tests/test_minimax_cache_baseline.py`：`__main__` 直接 `run()`，无 env 开关，会打真实 HTTP/MiniMax。
- **影响**：误跑产生费用与外部副作用；`pyproject.toml` 未配置 pytest，但若引入 pytest 全收集更危险。
- **修复方向**：真实脚本移到 `tests/manual/` 或强制双重开关；CI 永不收集。

### 2. 无自动化聚合入口（已确认）

- **问题**：无 `self_check` / tox / CI workflow 绑定 7 个本地脚本。
- **影响**：局部改动易漏跑 `test_workflow_setup` 或业务 Skill 测试。
- **修复方向**：单命令跑本地套件（非真实集成）。

### 3. 并发 / 多 worker / SQLite 锁压力未覆盖（已确认）

- **问题**：本地测试用 `TestClient` + 单进程；无多 worker、无 `runs.db` 写锁压力测试。
- **影响**：§Performance 与 §单飞锁问题只能在部署暴露。
- **修复方向**：可选压测脚本：同 session 冲突、跨 session 并行 emit、busy timeout。

### 4. Oracle 普通回归仅使用替身（已确认）

- **问题**：`tests/test_philips_wgq_inbound_recognition.py` 用 `_FakeConnection` 覆盖命中、配置缺失、查询失败和未命中；普通回归无真实 DB 连通测试。真实 HTTP 验收会经过部署环境，但不隔离验证每个 Oracle 字段来源。
- **影响**：SQL、Instant Client 与真实超时行为不在默认门禁内。
- **修复方向**：如需要发布前 Oracle 探针，保持为 opt-in 真实集成，与普通回归隔离。

### 5. MiniMax cache 字段端到端需人工（需确认）

- **问题**：库侧 usage 规范化已有单测；真实端点是否回传 `cache_read_input_tokens` / `cache_creation_input_tokens` 依赖 `test_minimax_cache_baseline.py` 人工跑。
- **影响**：缺失时字段为 0，`cache_hit_rate` 失真，计价 savings 偏低。
- **修复方向**：定期跑基线并记录结果；端点变更时重验。

## External Dependency Risks

| 依赖 | 证据 | 风险 | 降级 / 备注 |
|------|------|------|-------------|
| MiniMax via Anthropic 兼容客户端 | `runtime/agent.py` `init_chat_model("anthropic:...")` + `runtime/middleware.py` ToolStrategy 兼容 hook | 协议/thinking/cache/tool_choice 变更 | 无本地降级；run `failed` |
| deepagents / langgraph / langchain | `pyproject.toml` `>=` + `uv.lock` | stream 形状、SubAgent、RunControl | 锁文件 + FakeBrain 回归 |
| MinerU HTTP | `integrations/mineru.py` `requests` | 服务不可用/超时/状态枚举变化 | 工具失败 → 可能整 run failed |
| Oracle + Instant Client | `skills/philipswgqinboundrecognition/scripts/tools.py` | thick client 缺失、网络、SQL | 写入 `problems`，保留 PDF/Tracking 数据 |
| openpyxl | Philips `scripts/tools.py`、Tecan `documents.py` | Tracking 表头/sheet 或模板变化 | Philips 返回 problem；Tecan 由业务回归锁关键单元格 |
| SQLite 三库 | `runtime/resources.py` / `runs.py` | 锁、磁盘、无迁移 | checkpoints/store 由 LangGraph 管理 |
| FastAPI / uvicorn / python-multipart | `api.py` | 上传无限制、无鉴权 | 依赖部署面防护 |

## Areas Requiring Care When Changing

### 1. 事件类型与 `GET /runs` 契约

- 事件类型固定由 `execute_run` 写入：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`。
- `latest_content_event` 排除 `status` 与 `model_usage`；改过滤条件会破坏前端轮询语义。
- 触达文件：`runtime/execution.py`、`runtime/runs.py`、`api.py`、`tests/test_api.py`、`tests/test_harness.py`。

### 2. run 状态机与 cancel

- 合法状态：`RUN_STATUSES` in `runtime/runs.py`。
- 路径：`queued→running→succeeded|failed`；`queued→cancelled`；`running→cancelling→cancelled`。
- 改 cancel 必须同步 `request_cancel`、`GraphDrained`、`fail_incomplete_runs` 与 `tests/test_api.py` cancel 覆盖。

### 3. middleware 与 SubAgent 装配

- 只在 `runtime/middleware.py` 实现并通过 `runtime_middlewares()` 增删 middleware；主 Agent 手册加载在 `execution.py` 用 `memory_backend=` 打开，勿同时使用 `create_deep_agent(memory=...)`。
- `runtime/agent.py` 只做导入和 workflow/SubAgent 装配；`workflow_subagents()` 的两个 Tecan extractor 各自装配无 memory 的 middleware；Philips workflow 必须保持无 SubAgent。
- `register_harness_profile("anthropic", ...)` 为进程级全局副作用；测试或二次 import 需注意。
- 触达：`runtime/middleware.py`、`runtime/execution.py`、`runtime/resources.py`、`runtime/agent.py`、`tests/test_harness.py`、`tests/test_workflow_setup.py`、`tests/test_run_ledger.py`。

### 4. artifact 路径安全

- 虚拟路径解析与 `..` 拒绝：`integrations/artifacts.py` `resolve_artifact_path`。
- 业务写盘用 `unique_download_path` / `write_json_artifact`（不可覆盖）。
- 改 `allow_local` 默认值会影响 MinerU 与测试夹具路径。

### 5. Skill 业务工具契约

- Philips 只使用 `lookup_philips_wgq_master_data`，最终合同是 `PhilipsWgqRecognitionResult`；结构化响应缺失/非法令 run `failed`，业务 `input_problems` 仍令 run `succeeded`。
- Philips schema/Tracking/Oracle 位于 `skills/philipswgqinboundrecognition/`；Tecan 继续使用 `save_tecan_extraction` + `generate_tecan_import`，join 与模板逻辑在 `skills/tecanimport/`。
- 改 Philips 字段或主数据规则必须同步 `SKILL.md`、schema、业务测试和真实验收；改 Tecan 字段/模板同步 references、assets 与 `test_tecan_import.py`。

### 6. 依赖升级清单

- 升级 `deepagents` / `langgraph` / `langchain-anthropic` 前：
  1. `uv lock` 后跑全部本地脚本；
  2. 核对 `FakeBrain` 契约（`stream_mode` / `subgraphs` / `version=v2` / `RunControl`）；
  3. 核对 harness profile API 是否仍用 `register_harness_profile`；
  4. 抽样真实 run 验证 usage 与 cancel。

### 7. 存储与部署

- 不要单独替换三库之一；schema 变更默认整清 `data/`。
- 多 worker 前先解决 session 单飞与 SQLite 写模型。
- 生产必须外部提供 Oracle Instant Client（若启用 Philips 单位查询）与可达的 MinerU。

---

*Concerns analysis: 2026-07-16*
