# CONCERNS

> backend 风险、技术债、关注点。每条均带证据（文件 / 配置 / 行为）。状态分 **已确认**（代码或配置可证）与 **需确认**（推断，需人工核实）。
> 本轮刷新（2026-07-13）已核对当前工作树（`dsagents/` 包、`tests/`、`pyproject.toml`）。结论以源码为准。

## 一、技术债

### 1.1 定价常量硬编码（已确认）

`dsagents/api.py`：`PRICING_AS_OF`（当前 `"2026-07-12"`）、`_TIER_THRESHOLD_INPUT_TOKENS = 512 * 1024`、`_PRICING_TIERS`（standard / long_context 两档 CNY/M）、`_PRICEABLE_MODELS = {"MiniMax-M3"}` 全部硬编码，未做配置中心。MiniMax 调价或新增模型时需手动改代码并更新 `PRICING_AS_OF` 日期。任一调用不可计价 → 整 run 金额 `null`，不输出系统性偏低的部分金额，token 仍完整。

### 1.2 stream chunk 形状无版本契约（已确认，风险）

`dsagents/runtime/execution.py` 与 `dsagents/runtime/observability.py` 大量依赖 chunk 字段形状：

- `chunk["type"]` ∈ `messages` / `custom` / `updates`。
- `event` 后缀 `delta`；`thinking` / `reasoning` / `non_standard` block 类型。
- snapshot 中 `message.id` / `tool_call_id` / `tool_calls[]` 形状。
- `usage_metadata` / `input_token_details` / `ephemeral_5m_input_tokens` 形状。
- SubAgent 识别依赖 `lc_agent_name` metadata 与 `MAIN_AGENT_NAME` 常量。

依赖下限在 `pyproject.toml`：`deepagents>=0.6.12`、`langchain>=1.3.11`、`langchain-anthropic>=1.4.8`、`langgraph>=1.2.7`，均为 `>=` 无上限，`uv.lock` 锁具体版本。任一主版本升级可能破坏 stream chunk 解析，且这些形状是 langchain/deepagents 内部约定，无公开契约保护。

### 1.3 错误透传无护栏（已确认）

- `dsagents/runtime/execution.py` 捕获异常后把 `error` 写入 run status，`repr(exc)` 写入 `raw`。
- `dsagents/api.py` 的 `_ensure_failed_run` 同样透传错误文本，HTTP 层不包装、不脱敏。
- **风险**：真实错误（含 provider 4xx/5xx body、MinerU 内网地址、Oracle 连接串、文件路径）会原样落到 `runs.error` 与 `run_events.raw`，进而可能暴露给前端调用方；约定是「调用方自行处理」，但无护栏。

### 1.4 provider 耦合：MiniMax 强绑 Anthropic 客户端（已确认）

`dsagents/runtime/agent.py` 把 MiniMax（Anthropic 兼容）配置传入：

```python
init_chat_model(
    f"anthropic:{os.getenv('MINIMAX_MODEL')}",
    api_key=os.getenv("MINIMAX_API_KEY"),
    base_url=os.getenv("MINIMAX_BASE_URL"),
    thinking={"type": "adaptive"},
)
```

强耦合 Anthropic 客户端协议与 `thinking` 参数。`register_harness_profile("anthropic", ...)` 是进程级全局行为且只覆盖 `anthropic` profile。若将默认模型改成其它 provider，需重新核对是否会自动多出 general-purpose SubAgent；不要假设当前四个 extractor 配置自动跨 provider 等价。

### 1.5 持久化只增不删，无 TTL/归档（已确认）

- `dsagents_runs.db` 的 `runs` / `run_events` 表无清理方法（`dsagents/runtime/runs.py` 全文无 delete/truncate/vacuum）。
- `data/internal/run-events/` 的大 payload spill 文件（阈值 `max_inline_bytes=262_144`）只增不删。
- 业务 JSON/Excel artifact 落在 `data/artifacts/downloads/`，只增不改且无 registry/清理策略。
- 高频运行会持续增长磁盘占用，生命周期由部署方管理。raw chunk 长期留存（调试有利但占空间且保留模型/错误细节）。

### 1.6 无 CI 自动化测试断言（已确认）

- 本地测试脚本：`test_api` / `test_harness` / `test_run_ledger` / `test_tools` / `test_workflow_setup` / `test_philips_wgq_import` / `test_tecan_import`，加三个手动真实集成脚本 `test_real_image_run` / `test_real_multi_pdf_run` / `test_minimax_cache_baseline`。
- **风险一**：真实集成脚本是手动真实 HTTP / 模型 / MinerU 调用，靠 env 守卫（`DSAGENTS_RUN_REAL_*_TEST=1`）默认关闭，但无 `pytest.mark.skipif` 等隔离标记（`pyproject.toml` 也未配置 pytest）。若误用 `pytest tests/` 全跑，会触发真实 provider 调用与外部依赖。
- **风险二**：无 CI 可运行的自动化测试断言；回归靠人工选择并运行对应测试脚本（`cd backend && python -m tests.test_xxx`）。

## 二、并发与一致性

### 2.1 单飞锁是进程内 `threading.Lock`（已确认）

`dsagents/api.py` 的 `_acquire_session_run` 靠**进程内** `threading.Lock` + `dict[session_id, Lock]`（`app.state.session_locks`）：

- 多 worker 部署（如 `uvicorn --workers N`）时，同一 `session_id` 可在不同进程并发执行 run，锁完全失效。
- `session_id` 在本架构中只作两个用途：LangGraph `thread_id` 与单飞锁键。

### 2.2 `session_locks` 字典只增不删（已确认）

每个新 `session_id` 都会在 `_acquire_session_run` 的 `setdefault` 永久残留一个 Lock 对象。**严重性低**：每个 `threading.Lock` 约几十字节，即便上千 `session_id` 也只占几十 KB。真正的风险不在内存，而在 `active_runs` 字典里残留的 `run_id` 可能因清理逻辑 bug 让同 session 的新 run 误判旧 run 仍活跃。

### 2.3 SQLite 三库各立连接，无跨库事务（已确认）

`dsagents/runtime/resources.py` 三个 db 职责明确：

- `dsagents_runs.db` — run 与 run_events（`dsagents/runtime/runs.py`，fresh schema 建表 `runs`/`run_events` + 索引）。
- `dsagents_store.db` — LangGraph `SqliteStore`（`/memories/` 显式长期记忆路由，`namespace=("dsagents",)`）。
- `dsagents_checkpoints.db` — LangGraph `SqliteSaver` checkpointer（`thread_id=session_id`）。

三 db 各自独立连接、无跨库事务。`SqliteRunLedger` 每次数据库操作都开短连接，高并发下锁竞争与写吞吐有限。

### 2.4 `runs.db` 无 WAL / 无 busy_timeout（已确认）

`dsagents/runtime/runs.py _setup` 仅 `create table if not exists` + 建索引，grep 确认无 `journal_mode` / `busy_timeout` PRAGMA，也无任何 `_migrate` 迁移代码（fresh schema）：

- `dsagents_runs.db` 为默认 `delete` 模式。
- `dsagents_checkpoints.db` 由 LangGraph `SqliteSaver` 开启 WAL（磁盘存在 `-wal`/`-shm`，生命周期由 LangGraph 管理）。
- `dsagents_store.db` 同走 LangGraph `SqliteStore`。

**风险**：`runs.db` 的 `delete` 模式下，`emit_run_event` 在 run 执行期间高频短连接写（每个 stream chunk 一条），与读端 `get_run_events` 轮询并发时易触发 `database is locked`（SQLite 默认 5s busy timeout，本仓未显式设置 `busy_timeout`）。

### 2.5 取消是协作 drain，非强杀（已确认）

`POST /runs/{run_id}/cancel` 通过 LangGraph `RunControl` 协作式 drain，依赖 LangGraph 在自己的事件循环检查点检查 `RunControl`。`GraphDrained` 投影为 `cancelled`。若 Brain 长时间不回到检查点（例如某工具卡死），取消可能延迟生效；进程被强杀时 run 可能停在 `cancelling`，靠 lifespan 启动 `fail_incomplete_runs` 兜底（把 `queued`/`running`/`cancelling` 标 `failed`）。

### 2.6 run 在 daemon 线程跑，靠启动兜底（已确认）

`dsagents/api.py` 用 `threading.Thread(..., daemon=True)`。进程被强杀时，run 状态可能停在 `running`/`cancelling`，靠 lifespan 启动调用 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` 兜底。

## 三、安全边界

### 3.1 `.env` 私有配置（已确认，已缓解）

`.gitignore` 排除 `backend/.env`。长期文档只记录配置键与消费者，不读取、不抄录本地值；分享工作区前仍需检查私有配置和运行时数据。

### 3.2 provider key 通过 `os.getenv` 直读（已确认）

`dsagents/runtime/agent.py` 用 `os.getenv("MINIMAX_API_KEY"/"MINIMAX_MODEL"/"MINIMAX_BASE_URL")` 直接构造 `init_chat_model`，无校验、无脱敏日志护栏。`dsagents/integrations/mineru.py` 读取 `MINERU_*` 键（fail-fast）。Skill 的 Oracle 读取只取键且不记录值。

### 3.3 `data/*.db` 含运行时数据（已确认，已缓解）

`backend/data/dsagents_*.db` 未被 git 跟踪（`.gitignore` 含 `backend/data/`）。`input_messages_json`、`reply`、`error` 与 `run_events.raw` 会入库；上传文件本体落在 `data/artifacts/uploads/`，大 payload spill 落在 `data/internal/run-events/`。分享前需同时清理数据库与运行时文件目录。

### 3.4 无鉴权 / 无用户隔离（已确认）

`dsagents/api.py` 的 `create_app` 未注册任何 auth middleware；`/runs`、`/runs/{run_id}`、`/runs/{run_id}/cancel`、`/upload` 全部匿名可调（grep 确认无 `Depends`/`Authorization`/`Authentication`）。

### 3.5 CORS 未实现（已确认）

grep 在 `dsagents/api.py` 中无 `CORSMiddleware`/`add_middleware` —— 浏览器跨域实际不会被处理。

### 3.6 `/upload` 无大小/类型/数量限制（已确认）

`dsagents/api.py` 的 `post_upload`/`_store_upload` 直接 `shutil.copyfileobj(file.file, handle)`，无文件大小上限、无 MIME 白名单、无单批数量上限。恶意或误操作可写满磁盘。文件名经 `clean_filename`/`make_timestamped_name` 处理，路径穿越风险较低，但体积与类型无护栏。

## 四、易踩坑

### 4.1 声明式 SubAgent 不继承主 Agent middleware（已确认）

`workflow_subagents()`（`dsagents/runtime/agent.py`）通过 `_extractor(...)` 给每个 SubAgent **显式注入** `runtime_middlewares()`（`ToolTelemetry` + `NoProgressMiddleware`）。若未来新增 middleware 只加在主 Agent 装配处而忘了同步 `runtime_middlewares()`，SubAgent 将静默缺少该 middleware（例如缺少 no-progress 检测会让 SubAgent 死循环）。改动 middleware 列表必须同步 `runtime_middlewares()` 与所有 SubAgent。

### 4.2 `NoProgressMiddleware` 是启发式（已确认）

`NoProgressMiddleware` 仅检测「自最近一条 `HumanMessage` 之后，同一 `tool + 归一化 args` 连续出现 `NO_PROGRESS_WINDOW=3` 次」。它的局限：

- 只看最近 3 次连续相同调用；若 Agent 在两个不同失败调用间反复横跳（A 失败 → B 失败 → A 失败 ...），不会触发。
- 归一化 args 可能被模型微调后绕过（args 字段顺序/多余空格不同即视为不同 token）。
- 阈值是硬编码常量，不可配置。

它是兜底保险，不是完备的死循环检测。

### 4.3 fresh-schema 部署需整体清空 `data/`（已确认）

`dsagents/runtime/runs.py` 是 fresh schema，**无任何迁移代码**（无 `pragma user_version`、无 `_migrate`）。部署切换的正确做法是：停服务 + 整体清空 `backend/data/`（`dsagents_runs.db` / `run_events` / `dsagents_checkpoints.db` / `dsagents_store.db` / `artifacts/uploads` / `artifacts/downloads` / `internal/run-events` 全清），重启后由 `_setup` 与 LangGraph `.setup()` 重建。**不要**把旧库（尤其是旧扁平架构时期的库）直接拷贝过来——表结构/时间戳格式/事件类型均已变更，旧数据不会被迁移，只会导致读端解析失败。

### 4.4 `_safe` 把任意对象 `repr()` 落库（已确认）

`dsagents/runtime/runs.py` 的 `_safe`：非 dict/list/标量/None 的对象一律走 `repr(value)`。若传入 `emit_run_event`/`emit_run_status` 的 `raw` 含未实现 `model_dump` 的自定义对象（如异常对象、连接对象），其 `repr`（可能含内存地址、内部字段）会原样落库。`raw=chunk` 传入的是 langchain chunk，已走 `model_dump(mode="json")` 分支，安全；但自定义传参时需注意。

### 4.5 取消不回滚已生成文件（已确认）

`POST /runs/{run_id}/cancel` 只协作 drain LangGraph 图，**不回滚**已落到 `data/artifacts/downloads/` 的业务 JSON/Excel。取消后这些孤儿文件仍留在磁盘，由部署方清理。

## 五、§8 Oracle thick mode 外部依赖（已确认）

`dsagents/skills/philipswgqimport/scripts/tools.py` 的 Oracle 单位查询通过 `oracledb` thick mode 连接 Oracle，依赖 `ORACLE_CLIENT_LIB_DIR` 环境变量指向 Oracle instant client 目录。该 instant client **不在仓库**（`.gitignore` 排除 `backend/instantclient/`）。**生产部署必须由外部提供该目录**（容器镜像挂载、主机预装等），不能依赖仓库存放。

行为：

- `ORACLE_CLIENT_LIB_DIR` 缺失或 `oracledb.init_oracle_client(lib_dir=...)` 失败 → `_init_oracle_client` 优雅降级，不抛错，跳过法定单位查询。
- `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` 三者齐备且 client 初始化成功才发起查询；否则跳过。
- 查询异常也只追加人工校验项（单元格写「需确认」值）并继续生成，业务影响是核注清单缺少法定单位字段，**非崩溃**。
- `ORACLE_TIMEOUT_SECONDS` 控制连接/调用超时，默认 30 秒。

Tecan Skill 不消费任何 Oracle 键。

## 六、待确认事项

### 6.1 `runs.db` 是否需要 WAL + busy_timeout（需确认）

是否需要在 `_setup`（`dsagents/runtime/runs.py`）或连接级为 `runs.db` 设置 `PRAGMA journal_mode=WAL` 与 `PRAGMA busy_timeout=...`，以缓解写锁竞争（§2.4）？以及是否需要对 `checkpoints.db` 的 WAL 做 checkpoint/归档？

### 6.2 真实集成测试的隔离策略（需确认）

`test_real_image_run.py` / `test_real_multi_pdf_run.py` / `test_minimax_cache_baseline.py` 是否需要加 `pytest.mark.manual`/`skipif` 标记或移出 `tests/` 目录，避免误用 `pytest tests/` 全跑触发真实 provider 计费与外部依赖（§1.6）？当前靠 env 守卫（`DSAGENTS_RUN_REAL_*_TEST=1`）默认关闭，但 `pyproject.toml` 未配置 pytest。

### 6.3 MiniMax 端点是否回传 cache token（需确认）

库语义（usage 仅在终态 `message_delta` 一次出现）已在代码注释中说明，但真实 MiniMax 端点是否回传 `cache_read_input_tokens`/`cache_creation_input_tokens` 需 `test_minimax_cache_baseline.py` 真实跑一轮确认——缺失时这些字段为 0，不影响 input/output token，但会让 `cache_hit_rate` 恒为 `None` 或 0。

### 6.4 `session_locks` 的长期清理策略（需确认）

是否需要为 `app.state.session_locks` 加 LRU 或定期清理，避免长期运行下字典无限增长（§2.2）？当前严重性低，但若 `session_id` 由外部不可控来源生成（如前端随机 UUID 每次新建），增长会加速。

### 6.5 `/upload` 是否需要体积/类型护栏（需确认）

是否需要为 `/upload`（§3.6）加最大文件大小、MIME 白名单、单批数量上限？取决于部署环境是否可信（当前无鉴权，默认不可信）。

### 6.6 `MINERU_EFFORT` 空值提交（已确认，建议）

`dsagents/integrations/mineru.py` 中 `effort = os.getenv("MINERU_EFFORT") or ""`，可缺省或留空，并会原样以空字符串提交到 MinerU。建议维护本地或部署环境时按 `.env.example` 的键名补齐配置；长期文档不记录本地 `.env` 的实际值。
