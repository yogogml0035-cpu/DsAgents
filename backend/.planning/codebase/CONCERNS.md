---
title: CONCERNS — backend 风险、脆弱点与改动陷阱
analysis_date: 2026-07-20
last_mapped_commit: 555bca7
focus: concerns
---

# CONCERNS — backend 可操作警告

> 基于 `backend/` 源码事实（commit `555bca7`）。区分**有意设计取舍**与**真实脆弱点**；勿把产品边界当 bug。
> 路径均相对仓库内 `backend/`，除非写明仓库根。

---

## 1. Tech Debt / 设计取舍（不是遗漏）

| 取舍 | 证据 | 含义 |
|------|------|------|
| **无 SSE / 无 session API** | `api.py` 仅 `POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel` | 客户端必须轮询；进度靠 `run_events` + `after_event_id`，不是推送流。 |
| **OMS 旁路索引 best-effort** | `api.py` 119–128：`append_run_created_log` 包在 `try/except: pass`；`oms_log.py` 文档写明不进 `run_events` | 写失败静默；**无查询 API**；运维靠 `log/oms_log.log` JSONL grep。 |
| **run 为唯一执行/查询单位** | `runs.py` 注释与 `SqliteRunLedger`；`session_id` 仅作 LangGraph `thread_id` + 进程内单飞 | 不要重新引入 session 列表/恢复 API。 |
| **静态 5 工具，无自动扫描** | `runtime/tools.py` `default_tool_catalog` | 新 Skill 必须手改 import + 注册 + 可能改 denylist。 |
| **HTTP workflow 仅 Philips 字面量** | `api.py` `RunRequest.workflow: Literal["philips_wgq_inbound_recognition"] \| None` | Tecan 走程序内/非该 Literal 的 harness；HTTP 不暴露 Tecan workflow。 |
| **workflow 与 session_id 互斥** | `RunRequest.reject_workflow_session_reuse` | workflow run 强制服务端新 `session_id`，避免 checkpoint 线程复用污染。 |
| **后台 daemon 线程执行 run** | `api.py` `_run_background` + `threading.Thread(..., daemon=True)` | 进程被杀时线程不保证收尾；依赖启动时 `fail_incomplete_runs`。 |
| **通用 SubAgent 关闭** | `agent.py` `register_harness_profile(..., general_purpose_subagent=...enabled=False)` | 仅两个显式 Tecan extractor；勿假设 deepagents 默认 GP 子代理可用。 |
| **费用估算仅 MiniMax-M3** | `api.py` `_PRICEABLE_MODELS` / `_usage_summary` | 未知 model → `estimated_*` 整 run 为 `null`（故意不全量低估）。 |
| **`Protocol` 仅 Brain / BrainFactory** | 项目约定 + `agent.py` | 工具用 callable + `ToolCatalog`，不要为资源/ledger 加 Protocol。 |

以上是边界，不是“待修债”；改 API 面或事件模型前先对齐 `INTERFACES.md` / `Agents.md`。

---

## 2. Known fragile areas（改动高风险）

### 2.1 `StructuredOutputRecovery` 与 ToolStrategy 死循环

**位置：** `runtime/middleware.py` `StructuredOutputRecovery`；装配 `runtime_middlewares` / `DeepAgentsBrainFactory`；消费 `runtime/execution.py`。

| 规则 | 原因 |
|------|------|
| `@hook_config(can_jump_to=["model", "end"])` **必须含 `"end"`** | 否则无法合法跳出。 |
| 重试耗尽时 **必须** `jump_to: "end"`，**禁止**只返回 `None` | 注释与实现（约 353–366 行）：ToolStrategy 在无 `structured_response` 时 model↔model 会无限再生成。 |
| 空 data 壳配对依赖 `ToolMessage.tool_call_id` | `_rejected_empty_structured_call`；勿改成“扫任意历史 JSON”。 |
| 空壳耗尽（Philips schema）→ all-null skeleton + `partial_success` | `_empty_shell_fallback_result`；**不编造业务字段**。 |
| 其它失败耗尽 → **无** `structured_response` | harness 对 Philips workflow 会 `ValueError("structured_response missing...")` → run `failed`。 |
| 正常路径优先 schema 工具；文本 JSON 仅后备 | `EMPTY_DATA_SHELL_HINT` / `PHILIPS_WORKFLOW_PROMPT`。 |

**验证：** `python -m tests.test_harness`（含 exhausted `jump_to end`、空壳 skeleton）。

**SubAgent 隐患：** `runtime_middlewares()` 无参构造 `StructuredOutputRecovery()`，**默认 schema = `PhilipsWgqRecognitionResult`**。Tecan SubAgent 的 `response_format` 是 `ExtractionReference`，但 recovery 仍按 Philips 校验文本 JSON。工具路径正常；**文本后备 recovery 对 Tecan 语义错位**。改 SubAgent 结构化输出时要么传入正确 schema，要么明确 SubAgent 禁用 recovery 的文本路径。

### 2.2 workflow 工具 denylist 误裁

**位置：** `runtime/agent.py` `_PHILIPS_EXCLUDED_TOOLS` + `kwargs["tools"]` 过滤。

- 正确模式：**denylist 排除其它业务工具**（当前仅 `save_tecan_extraction` / `generate_tecan_import`），**保留**共享 MinerU（`parse_documents` / `extract_archives`）与本业务 `lookup_philips_wgq_master_data`。
- **禁止**业务-only allowlist（会裁掉 ZIP 手册路径依赖的 `extract_archives`）。
- 新增业务工具时：更新 `default_tool_catalog` **且** 评估是否加入 `_PHILIPS_EXCLUDED_TOOLS`。

**验证：** `python -m tests.test_workflow_setup`（对照真实 catalog 名集合）。

### 2.3 Skill 双目录 + package-data 不同步

每个 Skill 两套目录：

| 资源（挂载 `/skills/`） | Python 包 |
|-------------------------|-----------|
| `skills/philips-wgq-inbound-recognition/` | `skills/philipswgqinboundrecognition/` |
| `skills/tecan-import/` | `skills/tecanimport/` |

- `ResourceConfig.skills_dir` → `FilesystemBackend` 虚拟 `/skills/`。
- `pyproject.toml` `[tool.setuptools.package-data]` 必须列出 kebab 资源（`SKILL.md`、references、assets）；漏配则 wheel 缺资源。
- 改业务规则时：**SKILL.md + Python 工具/schema 一起改**；`test_workflow_setup` 会读 SKILL 行数与关键文案，但不会证明 Python 逻辑与文档一致。

### 2.4 Oracle thick mode / 配置缺失

**位置：** `skills/philipswgqinboundrecognition/scripts/tools.py` `_oracle_data` / `_init_oracle_client`。

- 缺 `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` → **不抛**，返回 `problems`（配置缺失），Tracking 有值仍可用。
- `ORACLE_CLIENT_LIB_DIR` 有值才 `oracledb.init_oracle_client`；未初始化 thick 时某些环境连接失败 → 捕获后 `problems`「Oracle 查询失败」。
- 成功路径依赖 thick client 目录在部署机存在；**本地门禁 mock 连不上真实库**。

### 2.5 进程内 session 单飞锁（不跨进程）

**位置：** `api.py` `_acquire_session_run` / `_release_session_run`：`app.state.session_locks` + `threading.Lock` + `registry_lock`。

- 同进程同 `session_id` 并发 → HTTP 409「该会话正在运行」。
- **多 worker / 多进程 uvicorn 不共享锁** → 可双开同一 `session_id`，checkpoint `thread_id` 竞态。
- workflow 强制新 session 降低了 HTTP workflow 路径风险；**复用 `session_id` 的非 workflow run** 在水平扩展下仍脆弱。

### 2.6 Philips 终态强依赖 `structured_response`

**位置：** `execution.py` 120–125。

- `workflow == philips_wgq_inbound_recognition` 且 stream 结束仍无 `structured_response` → 异常 → `failed`。
- `input_problems` / `partial_success` 只要 schema 合法仍可 `succeeded`（业务问题在 `run.result`，不是 HTTP 错误）。

### 2.7 取消路径协作式 drain

**位置：** `api.py` `cancel_run`；`execution.py` `request_cancel` / `RunControl` / `GraphDrained`。

- 仅当 `run_controls[run_id]` 存在（已进入 `execute_run`）才能 drain；queued 未入 harness 则直接 `cancelled`。
- 取消是协作式：长阻塞在 MinerU `time.sleep` 轮询或 Oracle/HTTP 时，**可能延迟到当前阻塞返回后**才看到 `drain_requested`。
- 进程重启：`fail_incomplete_runs` 把 `queued|running|cancelling` 标 `failed`（文案 `INTERRUPTED_RUN_ERROR`），不是 `cancelled`。

### 2.8 SQLite ledger 连接模型

**位置：** `runtime/runs.py` 每次操作 `sqlite3.connect` + commit。

- 无长连接池；高并发写依赖 SQLite 默认锁。
- 大 payload：`max_inline_bytes` 默认 262_144，超限落到 `data/internal/run-events/*.json`；删库不删 artifact 会留下孤儿文件，删文件不修 DB 会读失败。

---

## 3. Security

### 3.1 HTTP 面

- **无认证 / 无授权 / 无 CORS 白名单**（`api.py` 裸 FastAPI）。默认假设受信网络；勿对公网裸奔。
- **`POST /upload`**：无文件大小上限、无类型白名单；`clean_filename` 去掉路径段，时间戳落盘 `data/artifacts/uploads/`。可被刷盘。
- 返回虚拟路径 `/artifacts/uploads/<stored>`，不暴露主机绝对路径。

### 3.2 Artifact 路径边界

| API | 行为 |
|-----|------|
| `resolve_artifact_path`（默认） | 仅 `/artifacts/...`；拒绝 `..`；`resolve` + `relative_to` 防逃逸。 |
| `parse_documents` / `extract_archives` | `_resolve_document_path(..., allow_local=True)` → **可解析主机绝对/本地路径**（模型或调用方若传入非虚拟路径则读盘）。 |
| 业务工具 Philips/Tecan | 多数 `resolve_artifact_path` 默认 **不允许** local。 |

**风险：** Agent 被诱导传入本地路径时，MinerU 工具可读任意可读文件并上传到外部 MinerU。部署上应限制进程用户权限，并假设 prompt 注入场景。

### 3.3 ZIP 解压

**位置：** `integrations/mineru.py` `_extract_zip`：`ZipFile.extract(member, output_dir)` **未**规范化成员路径。

- 恶意 ZIP 可含 `../` 成员 → zip-slip 写出 `downloads/<stem>/` 之外（取决于 Python/平台与目标布局）。
- 仅应解压受信 MinerU 产物；不要对不可信上传 ZIP 直接 `extract_archives` 而不加固。

### 3.4 密钥与配置

| 变量名（勿提交值） | 用途 |
|--------------------|------|
| `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` / `MINIMAX_MODEL` | 主模型（`agent.py` `load_dotenv(backend/.env)`） |
| `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` / `MINERU_EFFORT` | 文档解析 |
| `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_TIMEOUT_SECONDS` / `ORACLE_CLIENT_LIB_DIR` | 主数据 |

- **不要提交** `backend/.env`、真实连接串、密钥。
- `load_dotenv` 在 `agent.py` 与 `mineru.py` 导入时执行；测试里有 `clear=True` 的 `patch.dict` 场景，改 env 加载顺序易碎。

### 3.5 工具与文件系统权限

- 主 Agent：`FilesystemPermission` **deny write** `/skills/**`（防改 SKILL 资源）。
- Tecan SubAgent：`write` deny `/**`（只读盘 + 专用 save 工具写 downloads JSON）。
- Memory：`/memories/AGENTS.md` 允许模型在工具失败后 **追加**误用笔记；提示禁止写密钥/业务数据，但**无强制审计过滤**。

### 3.6 可观测性泄露

- `run_events` / `raw` 可能含模型片段、工具 args；`ToolTelemetry` 结果截断 200 字符仍可能含路径。
- OMS JSONL 含 `run_id`、`session_id`、文件名；按运维敏感级别保管 `log/oms_log.log`。

---

## 4. Performance

| 区域 | 行为 | 影响 |
|------|------|------|
| **MinerU** | 同步 `requests` + 默认 **30s** 轮询（`MINERU_POLL_INTERVAL_SECONDS`）；超时 `MINERU_TIMEOUT_SECONDS` | 占满执行线程；大 PDF / 多文件批次长时间 `running`。 |
| **ZIP 模式** | `return_md`/`return_images`/… 会归一完整 ZIP 下载 | 磁盘与后续 `extract_archives` + 多轮 `read_file` 放大上下文。 |
| **模型** | recovery 最多 `DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES`（2）次 jump；空壳/校验失败重复调用 | token 与时延上升；费用估算见 `usage`。 |
| **NoProgressMiddleware** | 同 tool+args 连续 3 次 → `NoProgressLoop` → `failed` | 防死循环，但合法“重试同工具”也可能被误杀。 |
| **Ledger** | 每事件写 SQLite；大 raw 落盘 | 长 run 事件多 → DB 与 `run-events` 目录膨胀；`GET /runs` 聚合 `model_usage` 扫全表事件。 |
| **Checkpoint / Store** | 独立 SQLite：`dsagents_checkpoints.db`、`dsagents_store.db` | 与 runs 库并列增长；无内置 GC。 |
| **轮询 API** | 无 SSE | 客户端高频 GET 增加 SQLite 读压力。 |

`time.strftime` 用于 upload/MinerU 文件名（本地时区），与 ledger 的 UTC+8 文本时钟是不同通道，勿混用于对账。

---

## 5. Operational risks

### 5.1 时区硬编码 UTC+8

- `runs.py` `_CHINA_TZ`、`oms_log.py` 相同：写入 `YYYY-MM-DD HH:MM:SS` **无显式 offset 后缀**。
- 跨时区部署或夏令时地区对照日志时，**一律按中国标准时间解读**，不要当 UTC。

### 5.2 外部依赖降级矩阵

| 依赖 | 缺失/失败表现 |
|------|----------------|
| MiniMax | 模型调用失败 → run `failed` |
| MinerU env | `parse_documents` 立即 `RuntimeError: Missing required environment variable: ...` |
| MinerU 任务失败/超时 | 工具异常 → 通常导致 agent 失败或业务 problems（视模型是否恢复） |
| Oracle 配置 | problems，不中断工具返回结构 |
| Oracle 运行时错误 | problems 包装错误文本 |
| OMS 写盘失败 | **静默**；run 仍创建 |

### 5.3 进程生命周期

- 启动 lifespan：`fail_incomplete_runs("执行已中断，请重试")` 清理半截 run。
- daemon worker：主进程退出不 join 后台 run。
- `data/`、`log/` 锚定 `backend/`（`Path(__file__)`），与 CWD 无关——但**多实例共盘**会抢同一 SQLite/artifacts。

### 5.4 磁盘

- uploads / downloads / run-events / SQLite 默认都在 `backend/data/`（OMS 在 `backend/log/`）。
- 无自动清理策略；真实联调产物易堆积（仓库中已有示例 downloads）。

---

## 6. Testing gaps（本地门禁 vs 真实集成）

### 6.1 本地 assert 脚本（非 pytest）

常规门禁（`cd backend && python -m tests.<name>`）：

- `test_tools`、`test_run_ledger`、`test_harness`、`test_api`、`test_workflow_setup`、`test_philips_wgq_inbound_recognition`、`test_tecan_import`

特点：Mock MinerU/Oracle/模型；**不证明**真实 PDF 识别率、Oracle 权限、MiniMax 工具调用形态。

### 6.2 真实 / 可选测试（默认 skip）

| 模块 | 门控 env（名） |
|------|----------------|
| `test_real_philips_wgq_inbound_recognition` | `DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1`；样本根 `DSAGENTS_PHILIPS_WGQ_SAMPLE_ROOT` 等 |
| `test_real_philips_wgq_ups` | 同类真实开关 |
| `test_real_image_run` | `DSAGENTS_RUN_REAL_IMAGE_TEST=1` |
| `test_real_multi_pdf_run` | 真实多 PDF |
| `test_minimax_cache_baseline` | 显式对活服务；注释标明非默认门禁 |

样本路径常写开发者本机目录；CI 无样本即 skip → **渠道 PDF 回归盲区**。

### 6.3 覆盖薄弱点

- 多进程 / 多 worker 下 session 锁与 checkpoint 竞态。
- cancel 在 MinerU 长轮询中的时序。
- zip-slip / 超大 upload。
- OMS 失败静默路径（仅 `test_api` 可测成功写日志）。
- SubAgent 默认 Philips recovery schema 与 `ExtractionReference` 不一致。
- package-data 漏文件（需装 wheel 后检查，本地 editable 不易发现）。

---

## 7. 改动时必读陷阱清单（给后续 agent）

1. **动 `StructuredOutputRecovery` / `after_model`：** 保持 `can_jump_to` 含 `end`；耗尽必须 `jump_to: "end"`；空壳与非空壳分支不要合并错；跑 `python -m tests.test_harness`。
2. **动 Philips 工具表：** denylist 排除**其它业务**工具，保留 MinerU + 本业务主数据工具；跑 `python -m tests.test_workflow_setup`。
3. **新增 Skill：** kebab 资源目录 + importable 包 + `tools.py` 静态注册 + `package-data`；考虑是否进 Philips denylist。
4. **动 HTTP 契约：** 不要加 SSE/session 列表；workflow 字面量与 `RunRequest` validator 同步；OMS 保持 best-effort。
5. **动 artifact 解析：** 虚拟路径继续防 `..`；慎用 `allow_local=True`；加固 ZIP 成员路径若处理不可信输入。
6. **动 Oracle：** 保持配置缺失与连接失败 → `problems` 而非抛崩整个 run（除非产品明确要求 fail-hard）。
7. **动时间戳：** ledger/OMS 统一 UTC+8 本地格式；不要改成 UTC 却不改对账文档。
8. **动 session 锁：** 清楚仅进程内；水平扩展需外部分布式锁（当前未实现）。
9. **动 cancel：** 区分 queued 直接 cancelled vs running drain；更新 `run_controls` 生命周期。
10. **密钥：** 只引用 env **名**；不读、不写、不提交 `.env` 值。
11. **文档同步：** 改 backend 行为后更新 `backend/.planning/codebase/*` 与受影响的根级 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`；`git diff --check`。
12. **测试形态：** 保持可执行 `python -m tests.*` assert 脚本；真实集成继续 env 门控，勿默认真连外网。

---

## 8. 关键文件索引

| 主题 | 路径 |
|------|------|
| HTTP / 锁 / OMS 调用 / upload | `api.py` |
| 执行 / cancel / structured_response | `runtime/execution.py` |
| Recovery / NoProgress / middleware 栈 | `runtime/middleware.py` |
| Brain / denylist / SubAgent | `runtime/agent.py` |
| 工具目录 | `runtime/tools.py` |
| Ledger | `runtime/runs.py` |
| OMS JSONL | `runtime/oms_log.py` |
| 资源与 `/skills` `/artifacts` | `runtime/resources.py` |
| 路径安全 | `integrations/artifacts.py` |
| MinerU / ZIP | `integrations/mineru.py` |
| Oracle / Tracking | `skills/philipswgqinboundrecognition/scripts/tools.py` |
| Tecan 工具 | `skills/tecanimport/scripts/tools.py` |
| 打包资源 | `pyproject.toml` |

---

*Analysis Date: 2026-07-20 · last_mapped_commit: 555bca7*
