# CONCERNS — backend 技术债、风险与脆弱点

> Analysis Date: 2026-07-25。`last_mapped_commit: 79f97d239243d0513de93f10224eef470fffd83c`。
> 仅记录源码可证的边界与失败模式；不记录密钥、`.env` 值或连接串。
> 范围：`backend/` 产品源码（`api.py`、`runtime/`、`integrations/`、`skills/`、`tests/`、`pyproject.toml`）；忽略 `build/`、`data/`、`log/`、`.venv`、发行产物。本分析不读取 `.env`。

## Tech debt

### `httpx2` 声明依赖但产品代码未使用

- **位置**：`pyproject.toml` `dependencies` 含 `httpx2>=2.5.0`。
- **事实**：产品源码中无任何 `import httpx` / `httpx2`；外部 HTTP 仅 `integrations/mineru.py` 使用 `requests`，真实集成测试也用 `requests` / `urllib`。
- **债**：锁文件多装无用依赖；读者易误以为 MinerU/模型走 httpx。
- **方向**：确认无传递方强制后从 `pyproject.toml` / `uv.lock` 移除，或统一迁移到单一 HTTP 客户端。

### 双重 `load_dotenv(backend/.env)`

- **位置**：`runtime/agent.py`（模块 import 时）、`integrations/mineru.py`（模块 import 时）；路径均为 `Path(__file__).resolve().parents[1] / ".env"`（`BACKEND_ENV_PATH`）。
- **事实**：两次对同一 `backend/.env` 调用 `load_dotenv`（默认不 override 已存在环境变量）。
- **债**：加载顺序依赖 import 图；测试若需注入 env 须在 import 前设置或 `override=True`（见 `tests/test_harness.py`）。无集中配置入口。
- **方向**：单点 bootstrap（如 `resources` / API lifespan）加载一次。

### 进程内会话互斥与 cancel 状态不可水平扩展

- **位置**：`api.py` 的 `app.state.session_locks` / `app.state.active_runs` / `app.state.registry_lock`；`runtime/execution.py` 的 `HarnessRuntime.run_controls`。
- **事实**：`_acquire_session_run` 用进程内 `threading.Lock` 做同 `session_id` 单飞；`request_cancel` 只在本进程字典里查找 `RunControl` 并 `request_drain`。
- **债**：多 worker（多进程 uvicorn/gunicorn）时，跨进程无法互斥同 session，也无法 cancel 另一 worker 上的 run。当前设计隐含单进程部署。
- **验证**：`python -m tests.test_api`（单进程 TestClient 路径）。

### 会话锁字典只增不减

- **位置**：`api.py` `_acquire_session_run` / `_release_session_run`。
- **事实**：`session_locks.setdefault(session_id, threading.Lock())` 在释放时只 `pop(active_runs)` 并 `lock.release()`，**不**删除 `session_locks` 条目。
- **债**：长生命周期服务上，每次新 `session_id`（含 workflow 强制新 session）都会留下一个 `Lock` 对象，内存缓慢增长。
- **方向**：释放时在无等待者时 `pop(session_locks)`，或改用有界会话注册表。

### `artifacts_root()` 与 API 注入的 `ResourceConfig` 脱节

- **位置**：`integrations/artifacts.py` 的 `artifacts_root()` 固定 `ResourceConfig().artifacts_dir`；`api.py` 上传用 lifespan 注入的 `config.artifacts_dir`；`integrations/mineru.py` 的 `_resolve_document_path` 调用 `artifacts_root()`。
- **事实**：自定义 `ResourceConfig(data_dir=...)` 时，HTTP 上传与默认 MinerU/工具写盘路径可能指向不同根目录（测试里普遍靠 `patch("...artifacts_root")` 对齐）。
- **债**：生产若只改 API 侧 `data_dir` 而不改默认 backend 路径假设，会出现「上传成功、工具找不到文件」或写到错误目录。
- **方向**：工具层从 `AgentResources` / 显式 root 注入，禁止模块级默认 `ResourceConfig()`。

### workflow 工具收窄依赖手写 denylist

- **位置**：`runtime/agent.py` `_WORKFLOW_EXCLUDED_TOOLS`；`runtime/tools.py` `default_tool_catalog()`。
- **事实**：WGQ / DK 都排除 `finalize_tecan_overseas_recognition`，保留共享 MinerU、12NC 主数据与 XLSX 检查器；Tecan finalizer 只保留给无 workflow 的明确请求。生产 `subagents=[]`。
- **债**：新增跨业务工具时必须同步 denylist；若误改成业务-only allowlist，会破坏 `/memories/AGENTS.md` 中 ZIP/`extract_archives` 指引。
- **验证**：`python -m tests.test_workflow_setup`。

### 定价与 usage 展示耦合 MiniMax-M3 常量

- **位置**：`api.py` `_PRICEABLE_MODELS` / `_PRICING_TIERS` / `PRICING_AS_OF`（`2026-07-12`）。
- **事实**：非 `MiniMax-M3` 的 model_usage 会令整 run 的 `estimated_cost_cny` / `estimated_savings_cny` 为 `null`（token 仍汇总）。
- **债**：换模型后 usage API 仍可用但金额永远为空，易被误读为「无用量」。

### 静态五工具注册、无自动发现

- **位置**：`runtime/tools.py` `default_tool_catalog()`；`pyproject.toml` `[tool.setuptools.package-data]`。
- **事实**：Skill 工具靠静态 import + 元组注册（当前 5 个：`parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`、`inspect_supply_chain_workbooks`、`finalize_tecan_overseas_recognition`）；各下划线 Skill 包的 `SKILL.md` / `references/*.md` 须手工列入 package-data。
- **债**：新增 Skill 漏改 catalog 或 package-data 时，运行时无工具或 wheel 缺资源文件。
- **方向**：新增时同步 `tools.py`、`pyproject.toml`、denylist、对应 `tests`。

---

## Known risks

### Oracle thick mode / `ORACLE_CLIENT_LIB_DIR` 降级

- **位置**：`skills/philips_wgq_inbound_recognition/scripts/tools.py` 的 `_oracle_data`、`_init_oracle_client`、`_bundled_oracle_client_dir`。
- **行为**：
  - 缺 `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` → 返回空映射 + `problems`（配置缺失），不抛到 HTTP。
  - 优先使用 `ORACLE_CLIENT_LIB_DIR`；Windows 未设置时自动使用随仓库 `backend/.oracle/instantclient/instantclient_19_31`（检测 `oci.dll`），再进程内调用 `oracledb.init_oracle_client(lib_dir=...)`；成功后 `_ORACLE_CLIENT_INITIALIZED = True`；客户端不存在才走 thin 默认路径。
  - 连接/查询/初始化任意 `Exception` → 空映射 + `problems`（查询失败），**不**使 lookup 工具本身崩溃。
  - `ORACLE_TIMEOUT_SECONDS` 默认 `"30"`（`tcp_connect_timeout` + `call_timeout` 毫秒换算）。
- **风险**：thick 库路径错误时 lookup 静默降级为 problems，业务字段靠 Tracking/模型补全，易出现「静默缺主数据」的 `partial_success` / 大量 null。`init` 失败时标志位不置位，后续会重试 init。
- **部署**：Windows checkout 可直接使用随仓库客户端；其它平台或要覆盖版本时设置 `ORACLE_CLIENT_LIB_DIR`；不要把连接串写进仓库或文档。

### MinerU 外部 HTTP 与长阻塞工具调用

- **位置**：`integrations/mineru.py`（`MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 必填；`MINERU_POLL_INTERVAL_SECONDS = 30.0`；可选 `MINERU_EFFORT`）。
- **行为**：`parse_documents` 提交任务后 `requests` 轮询直到完成或超时；失败 raise，由工具/Agent 层处理。
- **风险**：
  - 服务不可用、超时或结果非 JSON → 文档事实缺失，Philips/Tecan 只能 `problems`/null，不应伪造字段。
  - 工具调用占用 Agent 图一步，长时间阻塞该 run 的 stream 线程；cancel 为协作式 drain，**不能**中止已发出的 `requests` 轮询。
- **验证**：本地 fake 于 `python -m tests.test_tools`；真机需 opt-in 实样脚本。

### workflow `StructuredOutputRecovery` 耗尽路径

- **位置**：`runtime/middleware.py` `StructuredOutputRecovery`；由 `HarnessRuntime` 以 WGQ / DK 对应 schema 装配。
- **关键约束**（改坏会卡死或假成功）：
  1. `@hook_config(can_jump_to=["model", "end"])` 必须含 `"end"`。
  2. 重试耗尽（默认 `DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES = 2`）时必须 `jump_to: "end"`，禁止只返回 `None`（注释明确：ToolStrategy 无 tools 时 model↔model 无限循环）。
  3. 空 `data` 壳：同回合 `ToolMessage.tool_call_id` 绑定恢复；耗尽 → 当前 schema 的 all-null data + `input_problems` + runtime problem（**不编造业务值**）。
  4. 其它解析/校验失败耗尽 → 无 `structured_response`；`HarnessRuntime` 对任一 workflow 抛 `structured_response missing for <workflow>` → run `failed`。
  5. 普通 run：`structured_schema=None`，不装 recovery；Tecan finalizer 只作普通请求兼容投影。
- **风险**：把 empty-shell fallback 当成业务正常终态模板。
- **验证**：`python -m tests.test_harness`。

### 通用 / 非 workflow 路径可不产出业务 `result`

- **位置**：`runtime/execution.py`：WGQ / DK 都强制 `structured_response`；普通 run 否则可 `result=None` 仍 `succeeded`。
- **风险**：客户端若只认 HTTP 200/`succeeded` 而不检查 `result`，会把「闲聊式成功」当业务完成。

### OMS 索引 best-effort、可静默丢失

- **位置**：`api.py` `post_run` 在 `create_run` 成功后 `try/except Exception: pass` 调用 `runtime/oms_log.append_run_created_log`。
- **事实**：写 `backend/log/oms_log.log` JSONL；非 `run_events`、无查询 API；失败不改变 queued 响应。
- **风险**：磁盘满/权限错误时运维索引缺口，业务 run 仍正常；排障时不能假设 OMS 与 ledger 一一对应。

### 启动时中断 run 一律 `failed`

- **位置**：`api.py` lifespan → `runs.fail_incomplete_runs(INTERRUPTED_RUN_ERROR)`；`runtime/runs.py` 将 `queued|running|cancelling` 标为 `failed`（`reason: startup_interrupted`）。
- **风险**：进程崩溃或滚动重启后，进行中的业务 run 不会自动续跑；客户端必须用新 run 重试。与 daemon 后台线程叠加时，硬杀进程会留下需启动清理的中间态。

### 后台 daemon 线程执行 run

- **位置**：`api.py` `threading.Thread(..., daemon=True)` → `_run_background` → `harness.execute_run`。
- **风险**：主进程退出不等待 worker；进行中的 LLM/MinerU 调用可能被硬中断。依赖下次启动 `fail_incomplete_runs` 收敛投影，而非优雅 drain。

### 取消为协作式、非强杀

- **位置**：`api.py` `cancel_run` → `HarnessRuntime.request_cancel` → `RunControl.request_drain`；`execute_run` 在 chunk 间检查 `control.drain_requested` 并 `raise GraphDrained`。
- **事实**：无法中断正在阻塞的 MinerU HTTP 轮询、Oracle 查询或模型 HTTP 调用；仅在控制流回到 harness 循环后才收敛为 `cancelled`。
- **风险**：客户端看到 `cancelling` 后可能长时间无终态，直至当前阻塞返回。

---

## Security

### 无认证的四 HTTP 端点

- **位置**：`api.py` 仅 FastAPI 路由（`POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`），无鉴权中间件。
- **风险**：网络可达即上传与跑 Agent（消耗模型/MinerU/Oracle）。默认假设受信内网；公网暴露为部署事故。
- **方向**：网关鉴权 / mTLS / 内网 ACL（应用层当前不实现）。

### 敏感配置经环境变量 / `.env` 加载

- **位置**：`runtime/agent.py`、`integrations/mineru.py` 的 `load_dotenv`；读取 `MINIMAX_*`、`MINERU_*`、`ORACLE_*`。
- **约束**：仓库与文档不得提交密钥或私有 DSN；本分析不读取 `.env`。
- **风险**：日志/事件 `raw` 若回传工具 args 或异常 repr，可能间接带出路径信息；`ToolTelemetry` 将 `str(result)[:200]` 写入 stream（工具结果摘要）。OMS JSONL 含 `run_id` / `session_id` / 文件名路径。

### artifact 路径穿越防护 vs `allow_local`

- **位置**：`integrations/artifacts.py` `resolve_artifact_path`：`/artifacts/` 禁止 `..` 并 `relative_to` 校验；默认 `allow_local=False`。
- **例外**：`integrations/mineru.py` `_resolve_document_path(..., allow_local=True)`，允许非虚拟本地绝对/相对路径解析。
- **风险**：Agent 若被诱导传入本机路径，MinerU 工具可读上传根之外的文件（取决于进程 OS 权限）。HTTP 上传与 XLSX/Philips `resolve_artifact_path` 默认仍限制在 `/artifacts/`。
- **缓解现状**：业务提示要求「仅本轮显式 artifact」；Skills 挂载写保护 `FilesystemPermission(deny write /skills/**)`。

### `extract_archives` 未净化 ZIP 成员路径（ZipSlip）

- **位置**：`integrations/mineru.py` `_extract_zip`：`archive.extract(member, output_dir)`，成员名直接拼接虚拟路径列表。
- **事实**：未校验 `member` 是否含 `..` 或绝对路径；恶意 ZIP 可能写出 `output_dir` 之外。
- **风险**：若解析结果 ZIP 或用户可控 archive 含路径穿越条目，可写任意进程可写位置。
- **方向**：解压前用 `Path(member).parts` 拒绝 `..` / 绝对路径，或 `extractall` 后校验 `resolve().relative_to(output_dir)`。

### 上传端点无显式大小/类型配额

- **位置**：`api.py` `POST /upload`：`shutil.copyfileobj` 写盘，无 content-length 上限或扩展名白名单；`clean_filename` 仅剥路径分量。
- **风险**：恶意或误传大文件占满 `data/artifacts/uploads/`；需依赖反向代理或运维层限额。

---

## Performance

### SQLite 三库 + run ledger 每操作短连接

- **位置**：`runtime/runs.py` 每次 `sqlite3.connect(self.db_path)` 无显式 `timeout=` / WAL PRAGMA；`runtime/resources.py` 另开 `SqliteStore` / `SqliteSaver` 长连接（`dsagents_store.db` / `dsagents_checkpoints.db`）。
- **事实**：run 执行线程高频 `emit_run_event`（text_delta、thinking、tool_execution、model_usage）与轮询 `GET /runs/{run_id}` 并发读。
- **风险**：默认 SQLite 锁竞争下可能出现短暂 `database is locked`；大 payload 溢出到 `data/internal/run-events/*.json`（`max_inline_bytes=262_144`）增加磁盘 I/O。
- **未证伪**：当前测试未压测多并发 run 写同一 `dsagents_runs.db`。

### 无 SSE：轮询放大读路径

- **位置**：`api.py` `GET /runs/{run_id}` 每次聚合 events + `aggregate_model_usage` + 可选 `after_event_id`。
- **风险**：客户端短间隔全量拉 events 会重复扫描 `run_events`；长 run 的 usage 聚合随 model_usage 行数线性增长。客户端应使用 `after_event_id` 增量拉取。

### MinerU 30s 轮询粒度与超时

- **位置**：`MINERU_POLL_INTERVAL_SECONDS = 30.0`；超时来自 `MINERU_TIMEOUT_SECONDS`。
- **风险**：短任务也至少一轮间隔；超时设过大时单工具占用 run 线程过久，拖高同进程并发上限。PDF/Office 解析本身受外部 MinerU 吞吐限制。

### Memory / store 与 checkpointer 随 session 增长

- **位置**：`AgentResources` 的 store（`/memories/`）与 checkpointer（`thread_id=session_id`）。
- **风险**：复用 `session_id` 的普通对话使 checkpoint 变大；workflow 强制新 session（`RunRequest.reject_workflow_session_reuse`）可缓解 WGQ/DK 路径，但 store 中 `AGENTS.md` 跨 run 共享且 Agent 可 `edit_file` 追加误用笔记。

### daemon 线程与进程内并发上限

- **位置**：每个 `POST /runs` 启动一个 daemon `Thread`，无线程池/信号量。
- **风险**：突发大量 run 时线程数与并发 LLM/MinerU 连接无界增长，直至 OS/SQLite/外部 API 限流。

---

## Fragile areas

### `StructuredOutputRecovery` + ToolStrategy 图边

见 [Known risks](#philips-structuredoutputrecovery-耗尽路径)。任何「为了省跳转而返回 None」的重构都可能让生产图挂死；测试覆盖在 `tests/test_harness.py`。

### workflow tools denylist 误配

见 [Tech debt](#workflow-工具收窄依赖手写-denylist)。排除共享 `parse_documents`/`extract_archives` 会与 runtime handbook 矛盾；漏排会把对方渠道业务工具暴露给当前 workflow。

### 渠道共享 `OrderItem` 24 字段合同

- **位置**：`skills/channel_contract.py` 及 Philips/Tecan schema、recovery `_empty_shell_fallback_result`、`tests/test_*`。
- **脆弱点**：增删字段需同步两渠道、recovery fallback、Skill 文案与全部 schema 测试；数值/日期规范化规则变更会同时影响两侧 outcome。未知值必须 `null`；不输出 `shipment`、Excel、候选噪声。

### Skill package-data 与 import 路径

- **位置**：`pyproject.toml` package-data；Skill 为下划线可 import 包。
- **脆弱点**：wheel 安装缺 `SKILL.md` / references 时 Agent 读不到手册但 catalog 工具仍在；源码目录改名还须同步静态连字符挂载别名。

### cancel 竞态：queued vs 已进入 `execute_run`

- **位置**：`api.py` `cancel_run`：`request_cancel` 返回 False 时直接投影 `cancelled`；True 时仅 `cancelling`，由 stream 侧 `GraphDrained` 收尾。
- **脆弱点**：run 刚从 queued 进入 `execute_run` 注册 `run_controls` 的窗口依赖协作 drain；MinerU/Oracle 进行中时状态可能长时间停在 `cancelling` 直至当前阻塞返回。

### `NoProgressMiddleware` 与重复工具

- **位置**：`runtime/middleware.py`，窗口 `NO_PROGRESS_WINDOW = 3`。
- **脆弱点**：合法的「同参重试」会被 `NoProgressLoop` 打成 run `failed`；状态从消息历史派生（避免实例可变状态跨线程泄漏），但阈值全局写死。

### 子 Agent 文本过滤 vs usage

- **位置**：`runtime/execution.py`：subagent message 跳过 text，但仍记 `model_usage`。
- **脆弱点**：生产 `subagents=[]` 且 harness profile 关闭 general-purpose subagent；若未来重新启用子代理，事件语义与测试 `FakeBrain` 路径需重新对齐。

---

## Operational concerns

### 客户端轮询合同（无 SSE）

- **必须**：用 `POST /runs` 返回的 `run_id` 轮询 `GET /runs/{run_id}`，直到 `status ∈ {succeeded, failed, cancelled}`。
- **建议**：传 `after_event_id` 做增量；终态读 `result`（业务 JSON）而非仅 `reply`。
- **冲突**：同 `session_id` 并发第二跑返回 HTTP 409 + `active_run_id`（进程内锁）。
- **workflow**：`workflow` 与 `session_id` 不能同传（强制新 session）。

### 三 SQLite 与本地 artifacts

| 路径 | 内容 |
|---|---|
| `backend/data/dsagents_runs.db` | run 投影 + events（ledger） |
| `backend/data/dsagents_checkpoints.db` | LangGraph checkpoints |
| `backend/data/dsagents_store.db` | memory/store |
| `backend/data/artifacts/uploads|downloads/` | 上传与 MinerU/工具输出 |
| `backend/data/internal/run-events/` | 超大 event blob 外置 JSON |
| `backend/log/oms_log.log` | OMS run_created JSONL（best-effort） |

- **运维**：需外部保留/清理策略；代码不内置 GC。仓库内 `data/` 样例产物不应当作权威源码。
- **OMS**：旁路索引、不阻塞已创建 run；不可作为唯一审计源。
- **时间戳**：ledger 与 OMS 统一为 UTC+8 本地 `YYYY-MM-DD HH:MM:SS`。

### 部署与依赖清单

| 依赖 | 缺失/失败时 |
|---|---|
| MiniMax（`MINIMAX_MODEL` / `API_KEY` / `BASE_URL`） | Brain 创建或 stream 失败 → run `failed` |
| MinerU HTTP | `parse_documents` raise / 材料不足 |
| Oracle + 可选 thick lib（`ORACLE_CLIENT_LIB_DIR` 或 Windows 随仓 Instant Client） | lookup `problems`，业务可 partial |
| `openpyxl` 读 XLSX | inspection / Tracking `problems`（密码/损坏/非 xlsx） |

### 单实例假设

- session 单飞锁、`run_controls` cancel、daemon 线程均进程本地。
- 多副本前须外置互斥与 cancel 总线，并评估 SQLite 文件共享不可行性（应迁 Postgres 等或 sticky session + 单写者）。

---

## Testing gaps

| 缺口 | 说明 |
|---|---|
| 非 pytest | 门禁为 `python -m tests.<name>` assert 脚本；无收集/标记体系 |
| 真模型 / 真 MinerU / 真 Oracle | `tests/test_real_*`、`test_minimax_cache_baseline` 等 opt-in（如 `DSAGENTS_RUN_REAL_*=1`），**不**进默认 CI |
| 语义准确率 | 默认脚本覆盖 schema、ledger、API 投影、recovery、denylist、fake MinerU；**不**证明复杂真实 PDF/XLSX 识别质量 |
| 多 worker / 高压 SQLite | 无自动化证明 |
| 上传配额 / ZipSlip / OMS 磁盘失败 | 无专项测试 |
| `httpx2` 死依赖 | 无引用检查 |
| 水平扩展 cancel | 仅单进程 TestClient |

推荐本地回归（见 `Agents.md` / `docs/commands.md`）：

```powershell
cd backend
uv sync
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
```

---

## 修改前速查

1. **Recovery / ToolStrategy**：保持 `can_jump_to` 含 `end`、耗尽显式 `jump_to`、空壳 `tool_call_id` 绑定；跑 `python -m tests.test_harness`。
2. **工具表 / denylist / package-data**：跑 `python -m tests.test_tools` 与 `python -m tests.test_workflow_setup`；禁止业务-only allowlist；同步 `pyproject.toml` package-data。
3. **HTTP / 锁 / cancel**：不引入 SSE/session API；跑 `python -m tests.test_api`、`python -m tests.test_run_ledger`。
4. **Oracle / MinerU / 路径**：只改降级与虚拟路径语义时同步本文；勿写入密钥；注意 `allow_local` 与 ZipSlip。
5. **共享 JSON 合同**：同时改 `channel_contract`、两渠道 schema、Skill、recovery skeleton 与两侧测试。

---

## 设计取舍（非缺陷，但限制演进）

| 取舍 | 含义 |
|---|---|
| 单进程会话锁 + 协作 cancel | 简单正确于单实例；多实例需外置锁与 cancel 总线；cancel 非强杀 |
| 无 SSE | 实现简单；客户端承担轮询与退避 |
| OMS 旁路 best-effort | 不阻塞业务；索引不可作为唯一审计源 |
| 双 ToolStrategy / 通用路径灵活 | workflow 保证 `result`；非 workflow 靠 Skill 纪律与客户端校验 |
| 空壳 all-null technical fallback | 保证两渠道 workflow 可返回合法 `input_problems`；不得当业务模板 |
| denylist 而非 allowlist | 保护共享 MinerU 工具与 handbook；新增业务工具必须显式排除 |
| 静态 ToolCatalog | 可审计、无扫描惊喜；扩展靠手工注册 |
| Windows 随仓 Instant Client | 降低 thick 部署摩擦；非 Windows / 无 `oci.dll` 时走 thin 或显式 `ORACLE_CLIENT_LIB_DIR` |

出现跨 run 续办、可恢复队列或强制多 worker 时，需先定义唯一持久化归属再改架构，而不是叠加第二套状态表。
