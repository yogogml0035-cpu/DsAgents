---
last_mapped_commit: 3dadbc4
analysis_date: 2026-07-20
focus: quality
---

# TESTING — backend 测试体系事实

> 记录 `backend/tests/` 的组织方式、本地回归与真实集成分离、mock 辅助与门禁建议。
> **非 pytest**：测试为可执行 assert 脚本。
> Scope：仅 `backend/`。

## 运行模型

- 包管理：`cd backend && uv sync`（不要用 `pip install -e .` 绕过 `uv.lock`）
- 调用方式：

```powershell
cd backend
python -m tests.<module_name>
```

- 每个测试模块提供 `run() -> None`，以 `assert` 失败即非零退出；多数含：

```python
if __name__ == "__main__":
    run()
```

- **没有** `pytest` 配置、fixture 发现或 `conftest.py` 依赖
- 工作目录应为 `backend/`（或保证包 `runtime` / `skills` / `tests` / 顶层 `api` 可 import）
- Windows PowerShell 友好：上表命令直接可用；长路径/中文路径在 real 测试中常见，注意引号
- mock 依赖：`unittest.mock.patch` / `patch.dict`（标准库），非第三方 mock 框架

## 测试清单（本地回归）

| 模块 | 用途 |
|------|------|
| `tests.test_tools` | 静态 5 工具目录名；MinerU `parse_documents` / `extract_archives` 在 mock HTTP 下的路径、进度、部分失败、缺 `MINERU_BASE_URL` 快速失败 |
| `tests.test_run_ledger` | `AgentResources` 路由与 SQLite 三库；`SqliteRunLedger` 状态/事件/时间戳格式；`model_usage` 聚合；`/memories/AGENTS.md` seed 不覆盖；`fail_incomplete_runs`；`latest_content_event` |
| `tests.test_harness` | FakeBrain 事件管线；middleware（telemetry / no-progress / memory / recovery / empty shell）；Philips `structured_response` 缺失 → `failed`；`input_problems` 仍 `succeeded`；env 加载 |
| `tests.test_api` | FastAPI `TestClient`：`POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`；启动中断恢复；usage 计价；OMS `run_created`；workflow API 与冲突 session |
| `tests.test_workflow_setup` | Skill `SKILL.md` 体量与关键文案；middleware 数量（主 5 / 子 4）；**denylist** 工具表；Philips prompt / ToolStrategy / skills 挂载；SubAgent 名与只读权限 |
| `tests.test_philips_wgq_inbound_recognition` | `PhilipsWgqRecognitionResult` 契约；`normalize_product_id`；Tracking Excel + **mock Oracle** 主数据与降级 |
| `tests.test_tecan_import` | `save_tecan_extraction` / `generate_tecan_import`：A/B/C 一致与冲突、`input_problems`、币种/主数据冲突、xlsx 输出 |
| `tests.test_support` | **非独立门禁**：共享 `FakeBrain` / `FakeBrainFactory` / message helpers / `wait_for_run`；被 api/harness 等 import |

### 建议本地门禁顺序（与 `Agents.md` 一致）

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

文档变更：仓库根目录 `git diff --check`。

## 真实集成测试（`test_real_*` 与 opt-in）

与本地回归**严格分离**：默认不跑外部依赖；多数通过环境变量 gate，未设置则 print skip 后返回。

| 模块 | Gate / 说明 |
|------|-------------|
| `tests.test_real_philips_wgq_inbound_recognition` | `DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1`；真实 HTTP API + 样本 PDF/Tracking；覆盖 DHL/DSV/FedEx/UPS/康捷空等 case |
| `tests.test_real_philips_wgq_ups` | 手动/诊断向 UPS 普货 case；默认连本地 API；依赖样本目录 env |
| `tests.test_real_image_run` | `DSAGENTS_RUN_REAL_IMAGE_TEST=1`；上传图片 + 真实模型描述 |
| `tests.test_real_multi_pdf_run` | `DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1`；多 PDF + MinerU 解析覆盖检查 |
| `tests.test_minimax_cache_baseline` | **opt-in 诊断**，非 Stage-1 门禁；对真实 MiniMax 服务打两次同 session 请求观察 cache_read；需已启动 server + 真实 `MINIMAX_*`；脚本本身**无** `DSAGENTS_RUN_REAL_*` gate，但不得纳入默认回归 |

常见 env（名称保留原文，**不**记录密钥值）：

- API：`DSAGENTS_API_BASE_URL` / `DSAGENTS_BASE_URL`（默认常见 `http://127.0.0.1:8500`；baseline 脚本另有内网默认 host，以代码为准）
- 样本路径：`DSAGENTS_PHILIPS_WGQ_SAMPLE_ROOT`、`DSAGENTS_IMAGE_PATH`、`DSAGENTS_PDF_DIR` 等
- 超时/轮询：`DSAGENTS_REAL_*_TIMEOUT_SECONDS`、`DSAGENTS_REAL_*_POLL_SECONDS`
- 外部能力本身：`MINIMAX_*`、`MINERU_*`、`ORACLE_*`、`ORACLE_CLIENT_LIB_DIR`（thick mode；缺失优雅降级）

真实集成前通常需另开终端启动服务：

```powershell
cd backend
uv run uvicorn api:app --host 0.0.0.0 --port 8500
```

```powershell
cd backend
$env:DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST = "1"
python -m tests.test_real_philips_wgq_inbound_recognition
```

## test_support 辅助

路径：`backend/tests/test_support.py`

| 符号 | 作用 |
|------|------|
| `FakeBrain` | 脚本化 v2 stream：`messages` / `custom` / `updates`；产出 thinking、subagent usage（文本过滤）、tool_execution、tool_progress、`structured_response`（Philips）、assistant_message |
| `FakeBrainFactory` | 记录 `created_workflows` / `received_payloads`；注入 `HarnessRuntime` |
| `StreamControl` | `started` / `release` Event，配合 `hold` 消息测 cancel |
| `text_block` / `artifact_block` / `user_message` / `messages_json` | 构造 Run 输入 |
| `wait_for_run(client, run_id, expected_status)` | `TestClient` 短轮询至目标 status |
| `_recognition_result(text)` | Philips 成功或 `input_problems` 样例 dict |

注入模式（api 测试）：

```python
def fake_harness(resources: AgentResources) -> HarnessRuntime:
    return HarnessRuntime(
        resources=resources,
        tools=ToolCatalog(()),
        brain_factory=factory,
    )

app = create_app(
    resource_config=ResourceConfig(data_dir=...),
    harness_factory=fake_harness,
)
```

`FakeBrain.stream` 强制断言：

- `stream_mode == ["messages", "custom", "updates"]`
- `version == "v2"`
- `subgraphs is True`
- 消息 content 为 text block 列表

## Mock 策略 vs 真实

### 本地回归（默认 CI/开发门禁）

- **不**调用真实 MiniMax / MinerU HTTP / Oracle
- HTTP 外部：`unittest.mock.patch`（如 `integrations.mineru` 的 requests、`oracledb.connect`）
- Brain：`FakeBrain` / `FakeBrainFactory` 或 `patch("runtime.agent.create_deep_agent", ...)`
- 文件系统：`tempfile.TemporaryDirectory` + `ResourceConfig(data_dir=...)`；artifact 根通过 `patch("integrations.artifacts.artifacts_root", ...)`
- env：`patch.dict(os.environ, ...)`；模型相关可写临时 `.env` + `load_dotenv`
- 断言业务契约：Pydantic `ValidationError`、工具返回 `code == "input_problems"`、run `status`/`result.outcome`

### 真实集成

- 走完整 HTTP 四端点或 live server
- 需要真实模型、MinerU、样本文件、可选 Oracle
- 失败应视为环境/样本/服务问题，**不**替代本地 assert 门禁
- `test_minimax_cache_baseline` 明确声明：非 release gate，仅观察 cache 指标；第二轮 cache_read 为 0 时只诊断、不 fail

## 覆盖重点

1. **Run-first**：queued→running→终态；事件 7 类；cancel / 启动中断 `failed`（`INTERRUPTED_RUN_ERROR`）
2. **StructuredOutputRecovery**：合法文本恢复、空壳 coaching、耗尽 skeleton vs 无 structured_response、`can_jump_to` 含 end；空壳耗尽 → `partial_success` 可 succeeded；其它耗尽无 structured → 可 failed
3. **Workflow denylist**：Philips 工具 = MinerU 共享 + 主数据；排除 Tecan 两工具（与 `default_tool_catalog` 对照防漂移）
4. **业务 outcome**：`input_problems` 时 run `succeeded`；缺 structured 时 `failed`
5. **Skill 契约**：Philips schema forbid extra；Tecan A/B/C 与 xlsx 生成
6. **资源边界**：`/artifacts/`、`/artifacts/`、`/large_tool_results/`、`/skills/`；手册 seed 一次
7. **时间戳**：UTC+8 `YYYY-MM-DD HH:MM:SS`
8. **OMS**：best-effort JSONL，不进入 `run_events`
9. **工具静态表**：5 handler 名称集合稳定
10. **usage**：主 Agent + subagent `model_usage` 聚合；subagent 文本不泄漏为 `text_delta`

## 门禁建议

| 层级 | 包含 | 何时跑 |
|------|------|--------|
| **L0 文档** | 根目录 `git diff --check` | 任何文档/代码提交前 |
| **L1 本地回归** | 上表 7 个 `python -m tests.test_*`（不含 real / support / minimax baseline） | 每次改 `backend/` 实现；middleware / workflow / harness 必跑 `test_harness` + `test_workflow_setup` |
| **L2 专项** | 按改动面：tools→`test_tools`；ledger→`test_run_ledger`；HTTP→`test_api`；Philips tools→`test_philips_*`；Tecan→`test_tecan_import` | 局部修改时缩小范围，合并前仍建议 L1 全量 |
| **L3 真实集成** | `test_real_*` + 可选 `test_minimax_cache_baseline` | 发版前或联调；需样本与密钥环境；**不**作为默认 PR 硬门禁除非流水线已配置隔离 runner |

改 middleware / recovery 后最低验证：

```powershell
cd backend
python -m tests.test_harness
python -m tests.test_workflow_setup
```

改工具 denylist / Skill 挂载后最低验证：

```powershell
cd backend
python -m tests.test_workflow_setup
python -m tests.test_tools
```

改 run ledger / 事件投影后最低验证：

```powershell
cd backend
python -m tests.test_run_ledger
python -m tests.test_api
```

## 事件与 API 断言参考

本地 harness/api 对 FakeBrain 管线常见事件序列（类型集合固定为 7 类）：

- `status`（running）→ `thinking` → `model_usage` → `text_delta` → `tool_execution` → `tool_progress` → … → `assistant_message` → `status`（终态）
- HTTP 查询体含 `run`、`events`、`latest_content_event`、usage 摘要
- cursor 分页：以 `latest_content_event.event_id` 为游标再拉增量

## 文件索引

```
backend/tests/
  __init__.py
  test_support.py
  test_tools.py
  test_run_ledger.py
  test_harness.py
  test_api.py
  test_workflow_setup.py
  test_philips_wgq_inbound_recognition.py
  test_tecan_import.py
  test_real_philips_wgq_inbound_recognition.py
  test_real_philips_wgq_ups.py
  test_real_image_run.py
  test_real_multi_pdf_run.py
  test_minimax_cache_baseline.py
```

## 与质量约定交叉引用

- Protocol / denylist / recovery / `input_problems` 语义见本目录 `CONVENTIONS.md`
- HTTP 四端点与 OMS 旁路边界见根级 `INTERFACES.md`
- 完整命令与真实集成开关见根级 `docs/commands.md`
