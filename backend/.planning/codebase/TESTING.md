# TESTING — backend 测试体系事实

> last_mapped_commit: 79f97d239243d0513de93f10224eef470fffd83c
> Analysis Date: 2026-07-25。测试是可直接执行的 assert 脚本，**不使用 pytest 收集器**。以 `backend/tests/` 源码为准。

## 1. 运行方式

```powershell
cd backend
uv sync
python -m tests.<module_name>
```

硬性约定：

- 必须用 `python -m tests.<name>`，使包导入（`from runtime...` / `from api...` / `from tests.test_support...`）成立。
- **不要** `python tests/test_xxx.py`（顶层导入会失败）。
- **不要** `pytest` / `unittest discover` 作为默认门禁。
- **不要**用 `pip install -e .` 绕过 `uv.lock`。
- 入口形态：各模块定义 `run() -> None`，末尾 `if __name__ == "__main__": run()`（真实 HTTP 脚本另有 `main()` + argparse 的变体）。
- 断言使用内置 `assert`；失败即 traceback 非零退出。成功通常无冗长输出（真实集成脚本会打印进度）。
- `tests/` 为普通包（含 `__init__.py`）；无 pytest.ini / conftest / 参数化装饰器。

## 2. 本地回归 vs 外部依赖（分层）

| 层级 | 特征 | 是否默认门禁 |
|------|------|----------------|
| **本地回归** | `FakeBrain` / `unittest.mock.patch` / 临时目录 SQLite / 无真实模型·MinerU·Oracle·外网 | **是** |
| **真实集成** | 需已启动服务、样例文件、真实 `MINIMAX_*` / MinerU / 可选 Oracle；环境变量 opt-in | **否** |
| **诊断基线** | 如 MiniMax cache；直接 `-m` 可跑但对发布不强制 | **否** |

本地回归**不**证明模型识别质量；真实样例才验证同票归集、冲突裁决与主数据唯一匹配。

## 3. 测试模块全表

### 3.1 本地回归（七脚本，默认门禁）

建议按序全量执行：

```powershell
cd backend
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
```

| 模块 | 职责摘要 | 典型 mock / 夹具 |
|------|----------|------------------|
| `tests.test_tools` | 五工具静态目录顺序与名称；MinerU `parse_documents` / `extract_archives` 表单、轮询、zip/json 落盘、进度事件、缺 env 错误 | `patch` HTTP / sleep / 时间戳；临时 artifacts |
| `tests.test_run_ledger` | `SqliteRunLedger` 状态机、append-only 事件、查询游标、大 payload 外置、UTC+8 时间戳、usage 聚合；`AgentResources` 三库与 FS 路由；`/memories/AGENTS.md` baseline | `tempfile` + `ResourceConfig(data_dir=...)` |
| `tests.test_harness` | stream 归一化（7 类事件）、cancel/`GraphDrained`、`NoProgress`、ToolTelemetry、MemoryMiddleware、**StructuredOutputRecovery**（两 schema、重试封顶、`jump_to: "end"`、空壳 all-null `input_problems` / 其它失败耗尽无 structured_response）、WGQ/DK 与普通 Tecan finalizer 投影 | `FakeBrainFactory`、`create_agent` 小图、`patch` env；临时 resources |
| `tests.test_api` | 四 HTTP 端点、`WGQ` / `DK` workflow-session 422、session 单飞 409、轮询 `result`/events/usage 计价、cancel 404/409/202、启动 `fail_incomplete_runs`、OMS `run_created` JSONL best-effort | `TestClient` + 注入 `FakeBrainFactory` 的 `harness_factory`；`patch` OMS 路径 |
| `tests.test_workflow_setup` | Skill.md / 货代版式参考关键句；WGQ / DK **同一 denylist** 后工具名集合；`subagents=[]`；两个 `ToolStrategy`（schema 不同）、两个 Recovery、无 workflow 无 `response_format`；`/skills/` 挂载 | `patch create_deep_agent`；临时 `AgentResources` |
| `tests.test_philips_wgq_inbound_recognition` | Philips schema 合同（24 字段 items、日期、outcome）；`normalize_product_id`；Tracking XLSX；共享 Oracle 路径、Windows bundled client 回退 mock 与降级 | `openpyxl` 夹具；`patch` artifacts 根与 Oracle 连接 |
| `tests.test_tecan_import` | `inspect_supply_chain_workbooks` 只读与 JSON artifact；`finalize_tecan_overseas_recognition` 终态；24 字段、空白→`null`、数值格式、outcome/`input_problems` | 临时 uploads；`patch artifacts_root` |

### 3.2 共享支持（非独立门禁）

| 模块 | 职责 |
|------|------|
| `tests.test_support` | `FakeBrain` / `FakeBrainFactory` / `StreamControl`、消息构造、`wait_for_run`、Philips/Tecan 夹具结果；被 `test_api` / `test_harness` 等导入 |
| `tests.__init__` | 包标记，无测试逻辑 |

### 3.3 真实集成与诊断（opt-in，非默认门禁）

| 模块 | 开关 / 入口 | 依赖 |
|------|-------------|------|
| `tests.test_real_image_run` | `DSAGENTS_RUN_REAL_IMAGE_TEST=1`；可选 `DSAGENTS_API_BASE_URL`、`DSAGENTS_IMAGE_PATH`、超时/轮询 env；支持 `main()` argparse | 已启动 HTTP + 真实模型 |
| `tests.test_real_multi_pdf_run` | `DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1`；`--pdf-dir` / `DSAGENTS_PDF_DIR` 等 | 已启动 HTTP + MinerU + 模型 |
| `tests.test_real_philips_wgq_inbound_recognition` | `DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1`；样例根 `DSAGENTS_PHILIPS_WGQ_SAMPLE_ROOT` | 多承运商样例 + Tracking + 模型/MinerU；Oracle 按部署 |
| `tests.test_real_philips_wgq_ups` | 直接 `-m`（脚本内路径/env）；可选 `DSAGENTS_PHILIPS_WGQ_UPS_CASE_DIR` | UPS 双 PDF 验收；流式打印事件 |
| `tests.test_minimax_cache_baseline` | 无门禁开关；`DSAGENTS_API_BASE_URL`（兼容 `DSAGENTS_BASE_URL`）指向 live 服务 | 真实 MiniMax；同 session 两轮观察 cache_read；打印实际目标地址，任一 run 非 `succeeded` 时以终态原始事件失败；**非**发布门禁 |

未设置 opt-in 开关时，真实脚本打印 skip 说明后 `return`（不失败）。

示例：

```powershell
$env:DSAGENTS_RUN_REAL_IMAGE_TEST="1"
python -m tests.test_real_image_run

$env:DSAGENTS_RUN_REAL_MULTI_PDF_TEST="1"
python -m tests.test_real_multi_pdf_run --pdf-dir <dir>

$env:DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST="1"
python -m tests.test_real_philips_wgq_inbound_recognition

python -m tests.test_real_philips_wgq_ups
python -m tests.test_minimax_cache_baseline
```

真实脚本通过 HTTP 客户端（`requests` / `urllib`）打四端点，**不**替代本地七脚本。

## 4. 关键门禁与按改动复跑

| 改动面 | 至少运行 |
|--------|----------|
| `runtime/middleware.py` / recovery / `jump_to` | `test_harness` |
| 工具注册 / WGQ-DK denylist / Skill 装配 | `test_workflow_setup` + `test_tools` |
| ledger / 事件 / 时区 | `test_run_ledger` |
| HTTP / cancel / OMS 写点 | `test_api` |
| Philips schema / Tracking / 共享 12NC Oracle 工具 | `test_philips_wgq_inbound_recognition` |
| Tecan finalizer / XLSX | `test_tecan_import` |
| 渠道 JSON 合同共享层 | Philips + Tecan 两脚本 |

发布或大改后跑满七脚本，并在仓库根执行 `git diff --check`（文档变更）。

根级 `AGENTS.md` 明确的关键门禁集：

- `test_tools`
- `test_run_ledger`
- `test_harness`
- `test_api`
- `test_workflow_setup`
- `test_philips_wgq_inbound_recognition`
- `test_tecan_import`

## 5. 共享测试支持：`tests/test_support.py`

- **`FakeBrain`**：脚本化 v2 stream（`messages` / `custom` / `updates`），覆盖 thinking、subagent usage（文本过滤）、text_delta、tool_execution、tool_progress、assistant_message、model_usage；workflow 按 schema 产出 `structured_response`，普通 Tecan finalizer 路径单独模拟。
- **`FakeBrainFactory`**：记录 `created_workflows` 与 `received_payloads`；实现 `create(...)` 形状以注入 harness。
- **`StreamControl`**：`started` / `release` Event，配合 cancel 与 hold run。
- 消息构造：`text_block` / `artifact_block` / `user_message` / `messages_json`。
- **`wait_for_run(client, run_id, expected_status)`**：短超时轮询 GET。
- 夹具结果：`_recognition_result` / `_tecan_recognition_result`（含 `input_problems` 分支）。

说明：`FakeBrain` 仍可发出 subagent 元数据以锻炼过滤与 usage 聚合；**不**表示生产 `DeepAgentsBrainFactory` 会创建业务 SubAgent。

## 6. Mock 策略（本地回归）

| 依赖 | 策略 |
|------|------|
| LLM / DeepAgents 图 | `FakeBrain` 或 `patch("runtime.agent.create_deep_agent")`；recovery 单测可用 `create_agent` + 可控 model（`_StructuredOutputModel`） |
| MinerU HTTP | `unittest.mock.patch` 替换 `requests` 会话方法；固定 task/result 字节；`_FakeJsonResponse` / `_FakeZipResponse` |
| Oracle | `patch` 连接/查询（`_FakeConnection` / `_FakeCursor`）；或只测 env 缺失降级 |
| 时钟 / sleep | patch 时间戳命名与 `time.sleep`，避免慢测 |
| 文件系统 | `tempfile.TemporaryDirectory`；`ResourceConfig(data_dir=...)`；`patch integrations.artifacts.artifacts_root` / `integrations.mineru.artifacts_root` |
| OMS 日志路径 | 指向临时目录，断言写/不写条件 |
| dotenv | harness 测用临时 `.env` + `load_dotenv(override=True)`；**不**读取开发者真实 `.env` 密钥做断言 |
| HTTP API | FastAPI `TestClient` + `create_app(harness_factory=..., resource_config=...)` |

原则：

- 本地回归**零**外网、**零**真实 API key 依赖。
- mock 边界贴近集成点（HTTP client、DB 路径、factory），避免过度 mock 导致合同漂移。
- 渠道合同以 **Pydantic `model_validate` / `finalize_channel_result`** 为准，不以模糊字符串匹配代替字段集合。
- `patch.dict(os.environ, ..., clear=True)` 用于隔离 MinerU/Oracle/模型 env。

## 7. 渠道供应链合同在测试中的覆盖

- Philips / Tecan `items[]` 均须完整 **24** 字段；未知为 `null`，不是空字符串。
- 数量、金额、重量：JSON 中为不带科学计数法的十进制字符串；日期 `YYYY-MM-DD`。
- `input_problems`：完整 `data.header` + 已证实 items（可 `[]`）+ 至少一条 `{source, location, issue, action}`；run 仍可 `succeeded`。
- Philips / Tecan：各自 ToolStrategy / `structured_response` → 共享 runtime finalizer（harness + schema 脚本）。
- 普通 Tecan：`finalize_tecan_overseas_recognition` 兼容投影（harness 事件链 + tecan 脚本）。
- `test_workflow_setup` 锁定 denylist：WGQ / DK 工具集均含共享 MinerU + XLSX + 12NC lookup，且均**不含** Tecan finalizer。
- SKILL.md 行数 ≤100；Philips 关键业务句与 Tecan「不生成 Excel」等由 `test_workflow_setup` 字符串断言。

## 8. StructuredOutputRecovery 测试要点（`test_harness`）

实现与测试共同锁定：

- 重试次数封顶（约 `1 + max_retries` 次模型调用量级；`DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES=2`）。
- 耗尽时 **`jump_to: "end"`**，禁止只返回 `None`。
- 空壳：`tool_call_id` 精确匹配同回合 AI 文本 JSON；否则按完整 header / items 纠错。
- **空壳耗尽** → 两 schema 的 all-null nested `input_problems`（可 `succeeded`）。
- **其它失败耗尽** → 无 `structured_response`（可 `failed`）。
- WGQ / DK 都传入对应 `structured_schema`；普通 run 为 `None`，不走 recovery。
- 另覆盖：ToolTelemetry 起止事件、Memory 源路径、NoProgress 同 tool 连击、model_usage helper、WGQ / DK 缺 structured 失败、普通 Tecan finalizer 成功投影。

## 9. 事件与 API 契约断言（摘录）

`test_api` / `test_harness` 锁定的事件类型集合与顺序形态（示例，非唯一合法序列）包括：

- `status` → … → `thinking` / `model_usage` / `text_delta` / `tool_execution` / `tool_progress` / `assistant_message` → 终态 `status`
- `tool_progress` payload 含 `name` ∈ {`parse_documents`, `extract_archives`}
- GET 响应含 `run` / `workflow` / `result` / `events` / `latest_content_event` / `usage`
- workflow + 客户端 `session_id` → 422；同 session 并发 → 409

## 10. `test_workflow_setup` 门禁要点

该脚本是 **workflow 装配合同** 的集中门禁，锁定：

| 断言面 | 期望 |
|--------|------|
| SKILL.md 行数 | Philips / Tecan 均 ≤100 |
| 业务关键句 | 如「相同 12NC 默认不合并」「不生成 Excel」「input_problems」等 |
| Skill `name` | `philips-wgq-inbound-recognition` / `tecan-import` 与虚拟路径一致 |
| workflow 常量 | `WAG_WORKFLOW == "WGQ"`、`DK_WORKFLOW == "DK"` |
| middleware | WGQ / DK 列表首位为各自 schema 的 `StructuredOutputRecovery`；`structured_schema=None` 时无 Recovery；Memory 源为 `RUNTIME_AGENTS_PATH` |
| 工具目录 | 五工具静态集合 |
| WGQ denylist | 含共享 MinerU + XLSX + 12NC lookup，**不含** `finalize_tecan_overseas_recognition`；有 `ToolStrategy` |
| DK denylist | 含共享工具、**不含** finalizer；有 `ToolStrategy(TecanOverseasRecognitionResult)` |
| 普通 run | 无 `response_format`；`subagents=[]` |
| `/skills/` 挂载 | 可读 SKILL / references |

改 denylist、Skill 资源、`DeepAgentsBrainFactory.create` 或 middleware 装配后**必须**复跑本脚本。

## 11. 残余空白（测试不保证的）

- 模型对复杂多票 PDF/XLSX 的角色识别与同票归集质量。
- Windows 随仓库 Instant Client 默认回退与 `ORACLE_CLIENT_LIB_DIR` 覆盖在真实库上的连通性（本地只测 mock）。
- MinerU 服务端正确性与计费侧最终账单（API usage 仅为趋势估算）。
- 生产并发与多进程部署下的 session 锁（当前锁为**进程内**）。
- 未覆盖的未来 Skill / 新事件类型（新增须扩测试与文档）。

## 12. 文档与代码同步

- backend 行为变更：先更新 `backend/.planning/codebase/`（含本文与 `CONVENTIONS.md`），再按影响更新根级架构/接口/系统地图。
- 文档检查：

```powershell
# 仓库根目录
git diff --check
```

- 命令总表与启动方式见根级 `docs/commands.md`。
