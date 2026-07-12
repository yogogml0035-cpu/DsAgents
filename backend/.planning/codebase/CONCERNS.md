# CONCERNS

> backend 风险、技术债、关注点。每条均带证据（文件:行 / commit / 配置）。状态分 **已确认**（代码或配置可证）与 **需确认**（推断，需人工核实）。
> 本轮刷新（2026-07-11）已核对当前工作树与近 20 次提交（HEAD `7126b83`）。行号以当前源码为准。

## 一、技术债

### 1.1 时区格式回退为裸本机时区（commit `c8cc563`，已确认）

`run_ledger.py` 已从 UTC ISO 切换为本机时区秒级文本：

- `TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"`（`run_ledger.py:14`），无时区后缀。
- `_now_text()`（`run_ledger.py:517-518`）= `datetime.now().astimezone().strftime(TIMESTAMP_FORMAT)`，`astimezone()` 无显式 tz 时取系统本地时区。
- 三处隐患：
  1. **时区依赖宿主**：部署到不同时区的机器会让同一数据库内 `created_at`/`updated_at` 混用多套时区文本，且无 tz 后缀（裸 `YYYY-MM-DD HH:MM:SS`），下游无法可靠还原 UTC。
  2. **一次性迁移假设**：`_migrate`（`run_ledger.py:426-430`）仅在 `pragma user_version < 1` 时跑 `_normalize_existing_timestamps(assume_naive_utc=True)`（`run_ledger.py:432-480`），把历史无 tz 时间戳**一律当作 UTC** 转本机时区。若历史数据实际已混入本机时区或其它来源的裸文本，迁移会引入偏移；迁移完成后 `user_version=1`，不会再次执行（幂等且单向，不可回滚）。
  3. **schema 版本机制单薄**：`RUN_LEDGER_SCHEMA_VERSION=1`（`run_ledger.py:15`）是唯一版本号，未来表结构变更需复用同一 `user_version` 递增；目前没有迁移失败回滚或版本不匹配的告警。

### 1.2 `_error_text` 三处重复实现（已确认）

`_error_text` 在三处独立定义，逻辑完全一致（`str(exc).strip() or exc.__class__.__name__`）：

- `api.py:255-257`
- `harness.py:656-658`
- `tools.py:502`（按 grep 结果）

三处都用裸 `str(exc)` 透传错误文本，无脱敏、无字段裁剪。建议下沉到公共模块（如 `run_ledger.py` 或新建 `errors.py`）单一来源。

### 1.3 错误透传无护栏（已确认）

- `harness.py:216-222` 捕获异常后把 `_error_text(exc)` 写入 run status `error`，`repr(exc)` 写入 `raw`。
- `api.py:191-203` 的 `_ensure_failed_run` 同样透传 `_error_text(exc)`，HTTP 层不包装、不脱敏。
- **风险**：真实错误（含 provider 4xx/5xx body、MinerU 内网地址、Oracle 连接串、文件路径）会原样落到 `runs.error` 与 `run_events.raw`，进而可能暴露给前端调用方；约定是"调用方自行处理"，但无护栏。

### 1.4 provider 耦合：MiniMax 强绑 Anthropic 客户端（已确认）

`harness.py:86-91` 把 MiniMax（OpenAI/Anthropic 兼容）配置传入：

```python
init_chat_model(
    f"anthropic:{os.getenv('MINIMAX_MODEL')}",
    api_key=os.getenv("MINIMAX_API_KEY"),
    base_url=os.getenv("MINIMAX_BASE_URL"),
    thinking={"type": "adaptive"},
)
```

强耦合 Anthropic 客户端协议与 `thinking` 参数。`register_harness_profile("anthropic", ...)`（`harness.py:50-55`）是进程级全局行为且只覆盖 `anthropic`。若将默认模型改成其它 provider，需重新核对是否会自动多出 general-purpose subagent；不要假设当前四个 extractor 配置自动跨 provider 等价。

### 1.5 stream chunk 形状无版本契约（已确认，风险）

`harness.execute_run`（`harness.py:152-215`）大量依赖 chunk 字段形状：

- `chunk["type"]` ∈ `messages`/`custom`/`values`（`harness.py:160,190,197`）
- `event` 后缀 `delta`（`harness.py:331,392`）
- `thinking`/`reasoning`/`non_standard` block 类型（`harness.py:634-643`）
- snapshot 中 `message.id` / `tool_call_id` / `tool_calls[]` 形状（`harness.py:442-454`）
- `usage_metadata` / `input_token_details` / `ephemeral_5m_input_tokens` 形状（`harness.py:365-381`）

依赖下限在 `pyproject.toml`：`deepagents>=0.6.12`、`langchain>=1.3.11`、`langchain-anthropic>=1.4.8`、`langchain-core>=1.4.8`、`langgraph>=1.2.7`，均为 `>=` 无上限，`uv.lock` 锁具体版本。任一主版本升级可能破坏 stream chunk 解析，且这些形状是 langchain/deepagents 内部约定，无公开契约保护。

### 1.6 定价常量硬编码（已确认）

`api.py:30-38`：`PRICING_AS_OF`、`_TIER_THRESHOLD_INPUT_TOKENS`、`_PRICING_TIERS`（standard/long_context 两档 CNY/M）、`_PRICEABLE_MODELS = {"MiniMax-M3"}` 全部硬编码在 `api.py`，未做配置中心。MiniMax 调价或新增模型时需手动改代码并更新 `PRICING_AS_OF` 日期。任一调用不可计价 → 整 run 金额 `null`（`api.py:280-281`），不输出系统性偏低的部分金额，token 仍完整。

### 1.7 持久化只增不删，无 TTL/归档（已确认）

- `dsagents_runs.db` 的 `runs` / `run_events` 表无清理方法（`run_ledger.py` 全文无 delete/truncate/vacuum）。
- `data/internal/run-events/` 的大 payload spill 文件（`run_ledger.py:355-368` `_store_blob`，阈值 `max_inline_bytes=262_144`）只增不删。
- 业务 JSON/Excel artifact 落在 `data/artifacts/downloads/`，只增不改且无 registry/清理策略。
- 高频运行会持续增长磁盘占用，生命周期由部署方管理。raw chunk 长期留存（调试有利但占空间且保留模型/错误细节）。

### 1.8 无 CI 自动化测试断言（已确认）

- 测试脚本：`test_api.py`、`test_harness.py`、`test_run_ledger.py`、`test_tools.py`、`test_workflow_setup.py`、`test_philips_wgq_import.py`、`test_tecan_import.py`，加两个手动真实集成脚本 `test_real_image_run.py`、`test_real_multi_pdf_run.py`。
- **风险一**：`test_real_image_run.py` 与 `test_real_multi_pdf_run.py` 是手动真实 HTTP / 模型集成脚本，无 `pytest.mark.skipif` / `@pytest.mark.manual` 等隔离标记（grep 确认 tests/ 下无 `pytest.mark` 装饰器）。若直接 `pytest tests/` 全跑，会触发真实 provider 调用与 MinerU/Oracle 外部依赖，混入回归套件。
- **风险二**：无 CI 可运行的自动化测试断言；回归靠人工选择并运行对应测试脚本（`cd backend && python -m tests.test_xxx`）。

## 二、并发与一致性

### 2.1 单飞锁是进程内 `threading.Lock`（已确认）

`api.py:206-212` 的 `_acquire_session_run` 靠**进程内** `threading.Lock` + `dict[session_id, Lock]`（`app.state.session_locks`，初始化于 `api.py:82`）：

- 多 worker 部署（如 `uvicorn --workers N`）时，同一 `session_id` 可在不同进程并发执行 run，锁完全失效。
- `session_id` 在本架构中只作两个用途：LangGraph `thread_id`（`harness.py:154` `config={"configurable": {"thread_id": session_id}}`）和单飞锁键。

### 2.2 `session_locks` 字典只增不删（已确认）

`_release_session_run`（`api.py:215-222`）只 `pop` `active_runs`，不清理 `session_locks`：

```python
app.state.active_runs.pop(session_id, None)
if lock.locked():
    lock.release()
```

每个新 `session_id` 都会在 `_acquire_session_run` 的 `setdefault`（`api.py:208`）永久残留一个 Lock 对象。**严重性低**：每个 `threading.Lock` 约几十字节，即便上千 `session_id` 也只占几十 KB。真正的风险不在内存，而在 `active_runs` 字典里残留的 `run_id` 可能因清理逻辑 bug 让同 session 的新 run 误判旧 run 仍活跃（见 §4.3）。长期运行仍建议定期清理 `session_locks`。

### 2.3 SQLite 三库各立连接，无跨库事务（已确认）

`resources.py:22-31` 三个 db 职责明确：

- `dsagents_runs.db` — run 与 run_events（`run_ledger.py`，自建表 `runs`/`run_events`，含 `idx_runs_session_created`/`idx_run_events_run_order`）。
- `dsagents_store.db` — LangGraph `SqliteStore`（仅 `/memories/` 显式长期记忆路由，见 `resources.py:63-74`）。
- `dsagents_checkpoints.db` — LangGraph `SqliteSaver` checkpointer（`thread_id=session_id`）。

三 db 各自独立连接、无跨库事务。`run_ledger` 每次数据库操作都开短连接（`closing(sqlite3.connect(...))` 后在 `with`/`try-finally` 内关闭，见 `run_ledger.py:49,78,110,151,182,226,258,289,382`），高并发下锁竞争与写吞吐有限。

### 2.4 `runs.db` 无 WAL / 无 busy_timeout（已确认）

`journal_mode` 各库不一致（`run_ledger.py:380-424` `_setup` 仅设 `pragma user_version`，grep 确认无 `journal_mode`/`busy_timeout` PRAGMA）：

- `dsagents_runs.db` 为默认 `delete` 模式。
- `dsagents_checkpoints.db` 由 LangGraph `SqliteSaver` 开启 WAL（磁盘存在 `dsagents_checkpoints.db-wal`/`-shm`，4MB+ 未 checkpoint，生命周期与 checkpoint 频率由 LangGraph 管理）。
- `dsagents_store.db` 同走 LangGraph `SqliteStore`。

**风险**：`runs.db` 的 `delete` 模式下，`emit_run_event`（`run_ledger.py:215-237`）在 run 执行期间高频短连接写（每个 stream chunk 一条），与读端 `get_run_events`（`run_ledger.py:108-147`）轮询并发时易触发 `database is locked`（SQLite 默认 5s busy timeout，本仓未显式设置 `busy_timeout`）。

### 2.5 run 在 daemon 线程跑，靠启动兜底（已确认）

`api.py:110-114` 用 `threading.Thread(..., daemon=True)`。进程被强杀时，run 状态可能停在 `running`，靠 lifespan 启动调用 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)`（`api.py:79`）兜底，对应 `run_ledger.py:287-307` 的 `fail_incomplete_runs`。

## 三、安全边界

### 3.1 `.env` 私有配置（已确认，已缓解）

`.gitignore` 排除 `backend/.env`，`git ls-files` 未跟踪该文件。长期文档只记录配置键与消费者，不读取、不抄录本地值；分享工作区前仍需检查私有配置和运行时数据。

### 3.2 provider key 通过 `os.getenv` 直读（已确认）

`harness.py:87-91` 用 `os.getenv("MINIMAX_API_KEY"/"MINIMAX_MODEL"/"MINIMAX_BASE_URL")` 直接构造 `init_chat_model`，无校验、无脱敏日志护栏。`tools.py:117-120` 读取 `MINERU_*` 键（`_required_env` fail-fast 见 `tools.py:321-325`）。`philips_wgq_import.py` 只读取 `ORACLE_*` 键且不记录值。

### 3.3 `data/*.db` 含运行时数据（已确认，已缓解）

`backend/data/dsagents_*.db` 未被 git 跟踪（`.gitignore` 含 `backend/data/`）。从 `run_ledger.py` 可直接确认 `input_messages_json`、`reply`、`error` 与 `run_events.raw` 会入库；上传文件本体落在 `data/artifacts/uploads/`（`api.py:144,160`），大 payload spill 落在 `data/internal/run-events/`（`run_ledger.py:355-368`）。分享前需同时清理数据库与运行时文件目录。

### 3.4 无鉴权 / 无用户隔离（已确认）

`api.py:68-90` 的 `create_app` 未注册任何 auth middleware；`/runs`、`/runs/{run_id}`、`/upload` 全部匿名可调（grep 确认无 `Depends`/`Authorization`/`Authentication`）。

### 3.5 CORS 未实现（已确认）

grep 在 `api.py` 中无 `CORSMiddleware`/`add_middleware` —— 浏览器跨域实际不会被处理。

### 3.6 `/upload` 无大小/类型/数量限制（已确认）

`api.py:140-173` 的 `post_upload`/`_store_upload` 直接 `shutil.copyfileobj(file.file, handle)`（`api.py:164`），无文件大小上限、无 MIME 白名单、无单批数量上限。恶意或误操作可写满磁盘。文件名经 `clean_filename`/`make_timestamped_name` 处理（`api.py:158-159`），路径穿越风险较低，但体积与类型无护栏。

## 四、易踩坑

### 4.1 时区混用陷阱（已确认）

由于 §1.1，跨时区部署或迁移前后切换时区，同一数据库内的时间戳会混用多套时区文本。**下游消费者（前端、报表、导出）不要假设 `created_at` 是 UTC**，也不要做带 tz 的 fromisoformat 解析（`_normalize_timestamp_text` 用 `fromisoformat` 能解析带 Z/带偏移的旧值，但新写入的都是裸本机时区）。

### 4.2 `_safe` 把任意对象 `repr()` 落库（已确认）

`run_ledger.py:483-492` 的 `_safe`：非 dict/list/标量/None 的对象一律走 `repr(value)`。这意味着传入 `emit_run_event`/`emit_run_status` 的 `raw` 若含未实现 `model_dump` 的自定义对象（如异常对象、连接对象），其 `repr`（可能含内存地址、内部字段）会原样落库。`raw=chunk`（`harness.py:169,179,188,196,214`）传入的是 langchain chunk，已走 `model_dump(mode="json")` 分支，安全；但自定义传参时需注意。

### 4.3 `_release_session_run` 的锁释放条件（已确认）

`api.py:221-222`：

```python
if lock.locked():
    lock.release()
```

`threading.Lock.locked()` 返回 True 时才 release。若 run 异常退出路径中 `_release_session_run` 被调两次（如 `_run_background` 的 `except` + `finally` 都触发），第二次 `locked()` 已为 False，不会重复 release —— 这是安全的。但若某次 release 被跳过（异常吞掉），同 session 的下一个 run 会永远拿不到锁，返回 409。排查此类问题时先看 `active_runs` 与 `session_locks` 是否一致。

### 4.4 run-first 重构后的 session 残留（已确认）

- **代码层已彻底去 session**：`session.py` 已删除（commit `8890292`/`dc6b9a8`），grep 在 `backend/*.py` 中无 `run_session`/`sessions.db`/`session.py` 引用。
- **旧 session 磁盘遗留**：`data/dsagents_sessions.db` 与 `data/artifacts/session-events/` 已删除；代码只引用 `dsagents_runs.db`/`dsagents_store.db`/`dsagents_checkpoints.db`（`resources.py:22-31`）。
- **session_id 语义已变**：现在只作 LangGraph `thread_id`（`harness.py:154`）和单飞锁键（`api.py:97,206`）。`RunRequest.session_id` 可缺省（`api.py:43,94` 缺省时 `uuid.uuid4().hex`）。旧的 `from session import run_session` 已不存在；如需库式调用，只能显式组合 `AgentResources` + `create_harness(resources)` + `harness.execute_run(...)`（见 `harness.py:235-241`）。非缺陷，仅作记录以防误用。
- **文档残留**：`docs/backend.md`、`docs/commands.md`、`docs/conventions.md`、`docs/project-overview.md` 仍含 "session" 字样（grep 确认），需人工核实是否为过时残留。

### 4.5 Oracle instant client 不在仓库（已确认）

`philips_wgq_import.py` 的 `_oracle_units` 通过 `oracledb` thick mode 连接 Oracle，依赖 `ORACLE_CLIENT_LIB_DIR` 环境变量指向 Oracle instant client 目录。该 instant client 曾从仓库删除（`backend/instantclient/`，约 109MB，37 个 git 跟踪文件），`.gitignore` 已加入 `backend/instantclient/`。**生产部署必须由外部提供该目录**（容器镜像挂载、主机预装等），不能依赖仓库存放。缺失时 `_oracle_units` 优雅降级（跳过法定单位查询，不抛错），但生成的核注清单将缺少法定单位字段（业务影响，非崩溃）。

### 4.6 Skills/模板的打包边界（需确认）

`backend/skills/` 是源码目录路由（`resources.py:42-43,65`，`harness.py:110` `skills=[SKILLS_SOURCE]`）。当前 `uv sync` 的 editable 开发运行可用；`pyproject.toml:31` 的 `py-modules` 只列 Python 扁平模块，不包含 `skills/`。若未来改为仅分发 wheel，非 Python Skill/模板是否被打包需另行确认。

## 五、待确认事项

### 5.1 `runs.db` 是否需要 WAL + busy_timeout（需确认）

是否需要在 `_setup`（`run_ledger.py:380-424`）或连接级为 `runs.db` 设置 `PRAGMA journal_mode=WAL` 与 `PRAGMA busy_timeout=...`，以缓解写锁竞争（§2.4）？以及是否需要对 `checkpoints.db` 的 WAL 做 checkpoint/归档（目前 4MB+ 未 checkpoint）？

### 5.2 真实集成测试的隔离策略（需确认）

`test_real_image_run.py` 与 `test_real_multi_pdf_run.py` 是否需要加 `pytest.mark.manual`/`skipif` 标记或移出 `tests/` 目录，避免误跑触发真实 provider 计费与外部依赖（§1.8）？

### 5.3 MiniMax 端点是否回传 cache token（需确认）

库语义（usage 仅在终态 `message_delta` 一次出现）已通过 `harness.py:355-381` 的注释确认，但真实 MiniMax 端点是否回传 `cache_read_input_tokens`/`cache_creation_input_tokens` 需 `test_minimax_cache_baseline.py` 真实跑一轮确认——缺失时这些字段为 0，不影响 input/output token，但会让 `cache_hit_rate`（`api.py:273,306,315`）恒为 None 或 0。

### 5.4 `session_locks` 的长期清理策略（需确认）

是否需要为 `app.state.session_locks`（`api.py:82`）加 LRU 或定期清理，避免长期运行下字典无限增长（§2.2）？当前严重性低，但若 `session_id` 由外部不可控来源生成（如前端随机 UUID 每次新建），增长会加速。

### 5.5 `/upload` 是否需要体积/类型护栏（需确认）

是否需要为 `/upload`（`api.py:140-173`）加最大文件大小、MIME 白名单、单批数量上限（§3.6）？取决于部署环境是否可信（当前无鉴权，默认不可信）。

### 5.6 MINERU_EFFORT 空值提交（已确认，建议）

`tools.py:119` `effort = os.getenv("MINERU_EFFORT") or ""`，可缺省或留空，并会原样以空字符串提交到 MinerU（`tools.py:348`）。建议维护本地或部署环境时按 `.env.example` 的键名补齐配置；长期文档不记录本地 `.env` 的实际值。

### 5.7 文档 session 残留人工核实（需确认）

`docs/*.md` 中仍含 "session" 字样（grep 确认 4 个文件命中），需人工核实是否为 run-first 重构后的过时残留，还是指当前合法的 `session_id`（thread_id）概念。
