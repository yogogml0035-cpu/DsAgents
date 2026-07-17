---
last_mapped_commit: d012362
---

# Testing Patterns

**Analysis Date:** 2026-07-17

> 事实来源：`backend/tests/*`、`backend/pyproject.toml`、以及被测 `runtime/` / `api.py` / `skills/` 行为。

## Test Framework

- **不是 pytest 套件**。`backend/pyproject.toml` 无 pytest、coverage、ruff、mypy 或 black 门禁，也无 CI 聚合器。
- 每个普通验证模块提供 `run() -> None`，内部使用原生 `assert`；失败即异常和非零退出。成功路径通常静默（exit 0）。
- 必须在 `backend/` 下以模块方式执行：

  ```powershell
  cd backend
  python -m tests.test_api
  ```

  不要直接运行 `python tests/test_xxx.py`，否则绝对顶层导入可能失败。
- HTTP 测试使用 `fastapi.testclient.TestClient`；磁盘隔离使用 `TemporaryDirectory` + `ResourceConfig(data_dir=...)`。
- `FakeBrain` / `FakeBrainFactory`、消息构造器与轮询 helper 集中在 `tests/test_support.py`。
- 真实模型、MinerU、Oracle 和外部 HTTP 验收与普通本地回归分开，默认不执行（或标为诊断脚本）。
- 入口惯例：普通回归 `if __name__ == "__main__": run()`；部分真实脚本另有 `main()` + `argparse`（如 `test_real_image_run`、`test_real_multi_pdf_run`）。

## Run Commands

依赖同步：

```powershell
cd backend
uv sync
```

### 本地回归（FakeBrain / mock，不触达真实外部服务）

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

### 真实集成 / 诊断（手工 opt-in，不纳入普通门禁）

```powershell
cd backend
uv run uvicorn api:app --host 0.0.0.0 --port 8500

$env:DSAGENTS_RUN_REAL_IMAGE_TEST="1"
python -m tests.test_real_image_run

$env:DSAGENTS_RUN_REAL_MULTI_PDF_TEST="1"
python -m tests.test_real_multi_pdf_run --pdf-dir <dir>

$env:DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST="1"
python -m tests.test_real_philips_wgq_inbound_recognition

# UPS 单用例诊断：当前无 env 门闸，会直连 DSAGENTS_API_BASE_URL（脚本默认端口 8501）
# 断言主体已注释，偏人工观察
python -m tests.test_real_philips_wgq_ups

# 诊断型 MiniMax prompt-cache 基线；无开关，会触达真实端点（BASE_URL 默认读 DSAGENTS_BASE_URL）
python -m tests.test_minimax_cache_baseline
```

仅文档变更至少执行 `git diff --check`。改 `StructuredOutputRecovery` 重试/退出语义时务必跑 `python -m tests.test_harness`，确认重试次数封顶且耗尽时 `jump_to: "end"`。改 Philips 工具裁剪时务必跑 `python -m tests.test_workflow_setup`，确认工具名集合含 `extract_archives`、不含帝肯工具。改 OMS 索引日志时务必跑 `python -m tests.test_api`（含 `_check_oms_run_created_log`）。

## Test File Organization

| 文件 | 入口 | 角色 |
| --- | --- | --- |
| `tests/test_support.py` | 无独立 `run()` | `FakeBrain` / `FakeBrainFactory`、`StreamControl`、消息构造、`wait_for_run`、Philips 结构化结果 fixture |
| `tests/test_tools.py` | `run()` | MinerU env guard、解析/ZIP/解压、`default_tool_catalog` 精确 5 工具名 |
| `tests/test_run_ledger.py` | `run()` | 资源、`/memories/AGENTS.md` seed/不覆盖、run ledger、`workflow` / `result_json` 持久化、外溢、usage、时间戳 |
| `tests/test_harness.py` | `run()` | Brain 装配、ToolTelemetry、主 Agent MemoryMiddleware、NoProgressMiddleware 消息状态检测、Philips `StructuredOutputCompatibility`、`StructuredOutputRecovery` 文本 JSON 恢复 / 校验失败 `jump_to: model` / 耗尽 `jump_to: end`、空 data 壳纠错、artifact 归一、七类事件、Philips `structured_response` 成功/缺失/`input_problems` |
| `tests/test_api.py` | `run()` | upload/runs/workflow/result/cancel/usage/recovery/session 单飞；OMS `run_created` JSONL 索引（`_check_oms_run_created_log`） |
| `tests/test_workflow_setup.py` | `run()` | Skill 文件、Philips ToolStrategy/工具 denylist/无 SubAgent、Tecan 两个 SubAgent、主/Sub middleware 差异（Sub **4** 个、主含 Memory）、`_update_events` |
| `tests/test_philips_wgq_inbound_recognition.py` | `run()` | Pydantic 结果合同、Tracking 严格倒序选行、申报页优先、Oracle 补缺/降级、交易字段隔离 |
| `tests/test_tecan_import.py` | `run()` | Tecan A/B 裁决、`input_problems`、join、币种和工作簿 |
| `tests/test_real_philips_wgq_inbound_recognition.py` | `run()` | 多渠道 upload → workflow run → poll 真实 HTTP 验收；默认 skip，需 `DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1` |
| `tests/test_real_philips_wgq_ups.py` | `run()` | UPS 普货单用例诊断：上传 case 目录 PDF、流式轮询并打印 `result`/`usage`；当前断言主体注释，无默认 skip 开关 |
| `tests/test_real_image_run.py` | `run()` + `main()` | 真实 HTTP 图片 run；`DSAGENTS_RUN_REAL_IMAGE_TEST=1` |
| `tests/test_real_multi_pdf_run.py` | `run()` + `main()` | 真实多 PDF + MinerU；`DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1` |
| `tests/test_minimax_cache_baseline.py` | `run()` | 真实 MiniMax cache 基线（诊断型，无 env 门闸；读 `DSAGENTS_BASE_URL`） |

命名统一 `test_*.py`。普通回归以 `if __name__ == "__main__": run()` 收口；真实脚本必须在模块说明和命令文档中标明外部依赖。仓库内**没有** `tests/tests_file/` 夹具目录；真实样例路径由 env 或脚本默认值指向外部目录。

## Test Types / Suites

### 1. Run / HTTP / ledger

- `test_run_ledger` 断言 `RunSnapshot.workflow`、解析后的 `result`、终态 `status.payload.result` 和重开 SQLite 后的持久化结果；时间戳格式为中国时区 `YYYY-MM-DD HH:MM:SS`。
- `test_api` 覆盖四个端点、增量轮询、`latest_content_event`、usage 计价、启动恢复、cancel 全出口，以及 OMS `oms_log.log`（见下节）。
- Philips workflow 覆盖：未知 workflow `422`、非空 `session_id` `422`、每次服务端生成不同 session、GET 顶层与 `run` 快照同时暴露 `workflow` / `result`。
- `result.outcome=input_problems` 仍断言 `run.status=succeeded`；结构化结果缺失则断言 `failed`。
- 启动恢复：遗留 `queued`/`running`/`cancelling` 经 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` 变为 `failed`，错误文案 `"执行已中断，请重试"`。

### 1b. OMS 日志（`test_api._check_oms_run_created_log`）

关键路径覆盖：

- patch `runtime.oms_log.DEFAULT_OMS_LOG_PATH` 到临时文件，隔离真实 `backend/log/`。
- **`/upload` 不写**索引行。
- **422**（非法 body，如单数 `message`）不写。
- **409**（同 session 并发 hold）不写；活跃 hold 成功创建的那条 **写**。
- 成功创建的 run（纯文本、带 artifact、fail 路径、hold）各 **恰好一行** `event=run_created`。
- 记录字段：`created_at` 匹配 `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$`、`run_id` / `session_id` / `workflow`、`files[]`（`name` basename + `path` 虚拟路径）；无 artifact 时 `files == []`。
- failed run **不丢**索引（索引在 `create_run` 成功后立刻写，与终态无关）。
- 与实现一致：写失败 best-effort 吞掉（源码 `api.py` `try/except Exception: pass`），测试侧验证成功路径契约，不模拟磁盘故障。

### 2. Harness / stream / Brain 装配

- `FakeBrain` 硬断言 `stream_mode=["messages","custom","updates"]`、`subgraphs=True`、`version="v2"` 和 `thread_id=session_id`。
- 验证 artifact block 进入 Brain 前全部转为 text block（`ARTIFACT_REFERENCE_HINT`）。
- 覆盖 subagent usage 保留但文本过滤、thinking/text/tool progress/tool execution/assistant message/model usage **七类事件**。
- Philips invocation 验证 `BrainFactory.create(..., workflow=...)` 注册 `StructuredOutputCompatibility`，原始模型保持 adaptive、实际 handler 请求关闭 Anthropic thinking；独立 fake chat model 还实际经过 `create_agent` 的 `wrap_model_call` 链并保留 `structured_response`，Harness 再从 `updates` 捕获并做 Pydantic 校验。
- `StructuredOutputRecovery`：fenced JSON 成功恢复；无 JSON / 校验失败 → `jump_to: "model"` 且 `structured_recovery_attempts` 递增；非空壳 `attempts >= max_retries` → `jump_to: "end"` 且无 `structured_response`；空 `data: {}` 耗尽 → `partial_success` + all-null `data` + runtime problem；端到端模型调用次数 = 1 + `max_retries` 后退出；空壳文本/ToolMessage 路径使用 `EMPTY_DATA_SHELL_HINT` 与 `PHILIPS_MINIMAL_DATA_SKELETON`；`philips_structured_output_error_message` 对空壳返回专用 ToolMessage。
- `test_harness` 验证 `NoProgressMiddleware` 从当前 HumanMessage 之后的消息序列识别连续三次同一 tool+args，新 human turn 与参数变化不会误触发；middleware 不依赖实例级计数；主 Agent 仅一个 `MemoryMiddleware` 且手册进入 system prompt。
- `test_workflow_setup` 断言 Philips **denylist**：排除帝肯工具、保留 `parse_documents` / `extract_archives` / `lookup_philips_wgq_master_data`、`subagents=[]`；通用/Tecan 路径仍注册 `tecan-extractor-a/b`；SubAgent middleware 无 `MemoryMiddleware`（各 **4** 个 runtime middleware）；受限记忆提示不含默认用户偏好语义。
- `test_run_ledger` 断言 `/memories/AGENTS.md` 首次 seed 含 ZIP/`result_path` 基线，人工/追加内容在重开资源后不被覆盖。

### 3. Philips 确定性业务规则

- `PhilipsWgqRecognitionResult` 使用**英文字段名**（tool 与 `run.result` 同一套）、`extra="forbid"` 和 outcome/data/problems 联动校验；无中文 JSON alias。
- 重复 `product_id`（12NC）保持原商品行顺序，不在 schema 层合并。
- `product_id` 去空格/连字符，超过 12 位取后 12 位。
- Tracking 只读 `进口` sheet；A 列 trim 后严格接受 `进口` / `出1` / `出2` / `已出`；从后向前取第一条合格行。
- 空白状态、`备注进口` 等非严格状态不得 fallback；未命中进口行时不得读取同料号 `申报要素` 行。
- Tracking 内 `申报要素` sheet 优先，所选进口行次之；Oracle 只补仍缺失字段。
- 工具结果不暴露旧数量、价格、单号、金额或重量；`//`、`N/A`、“需确认…”、“未找到…”等占位值归一为 `null`。
- Oracle 配置缺失、查询异常、未命中均返回 `problems`，不抛掉 PDF/Tracking 数据。

跨单据关联、多个票次拒绝、ZIP/DOCX 忽略、PDF 字段优先级由 Skill 指令和真实 HTTP 验收覆盖；确定性的 schema、run 通道与主数据规则由普通回归锁定。

### 4. Tecan 回归

Tecan 保留 A/B extractor、必要时 C 回查、`input_problems`、canonical 和 Excel 生成。Philips 删除旧 A/B/C 与 Excel 后，`test_tecan_import` / `test_workflow_setup` 继续防止误删 Tecan 行为。业务问题统一返回 `{"code": "input_problems", "problems": [...]}`，不抛异常结束工具调用。

### 5. Cancel 状态机

`test_api._check_cancel` 覆盖未知 `404`、终态 `409`、活跃 `202` + drain → `cancelled`、drain 中重复 cancel `200`。测试用 `StreamControl` 的 `started` / `release` 事件制造活跃窗口。

### 6. 真实 Philips HTTP 验收

`test_real_philips_wgq_inbound_recognition.py` 默认 skip；开关为 `DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1`。默认样例根可用 `DSAGENTS_PHILIPS_WGQ_SAMPLE_ROOT` 覆盖，服务地址用 `DSAGENTS_API_BASE_URL`（默认 `http://127.0.0.1:8500`），超时/轮询分别用 `DSAGENTS_REAL_PHILIPS_WGQ_TIMEOUT_SECONDS` / `DSAGENTS_REAL_PHILIPS_WGQ_POLL_SECONDS`。

脚本逐例上传 PDF、共用 Tracking 和可选无关附件，提交固定 workflow 并轮询终态；断言新 session、`original_waybill_number`、商品行、`product_id`、`quantity`/价格（`currency` / `unit_price` / `total_price`）、至少一项主数据、lookup 工具调用，以及 ZIP/DOCX 对应 `partial_success` 问题。渠道用例包括 DHL、DSV、FedEx、UPS、康捷空。

`test_real_philips_wgq_ups` 是单用例诊断脚本：默认 case 目录可通过 `DSAGENTS_PHILIPS_WGQ_UPS_CASE_DIR` 覆盖；**当前无 env 门闸**，会直接 HTTP 访问 `DSAGENTS_API_BASE_URL`（脚本默认 `http://127.0.0.1:8501`）。运行时打印上传列表、流式事件与最终 `result`/`usage`；`_assert_response` 仍保留但调用处已注释，适合人工观察而非门禁。

### 7. 其它真实诊断

- `test_real_image_run`：`DSAGENTS_RUN_REAL_IMAGE_TEST=1`；可选 `DSAGENTS_API_BASE_URL`、`DSAGENTS_IMAGE_PATH`、`DSAGENTS_IMAGE_QUESTION`、超时/轮询 env。
- `test_real_multi_pdf_run`：`DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1`；`--pdf-dir` 或 `DSAGENTS_PDF_DIR`；另有 request/timeout/upload/poll 相关 env。
- `test_minimax_cache_baseline`：无开关；读 `DSAGENTS_BASE_URL`（注意与其它真实脚本的 `DSAGENTS_API_BASE_URL` 不同）；两 turn 同 session 打印 cache 相关 `model_usage`，零 cache 只记诊断不失败。

## Mocking Patterns（test_support）

- **Brain**：`FakeBrainFactory` 记录 `created_workflows` 和收到的 payload；`FakeBrain` 按输入文本产生成功、`input_problems`（文本含 `"input problems"`）、缺少 structured response（文本含 `"missing structured"`）、失败（`"fail"`）、hold（`"hold"` + `StreamControl`）等路径。脚本化 v2 stream 产出七类事件管道。
- **StreamControl**：`started` / `release` 两个 `threading.Event`，用于 cancel 与 session 单飞窗口。
- **消息构造**：`text_block` / `artifact_block` / `user_message` / `messages_json` 构造 HTTP/run 输入。
- **轮询**：`wait_for_run(client, run_id, expected_status)` 使用有限 deadline（约 5s）轮询 GET。
- **Philips fixture**：`_recognition_result`（test_support 内）提供完整固定字段的成功与 `input_problems` payload。
- **网络**：`test_tools.py` 替换 `requests` 调用链，不访问真实 MinerU。
- **Oracle**：Philips 测试 patch `oracledb.connect`，用 `_FakeConnection` / `_FakeCursor` 记录实际查询的 12NC；`patch.dict(os.environ, ..., clear=True)` 覆盖配置缺失与异常。
- **Tracking**：测试内用 `openpyxl.Workbook` 创建临时 `进口` / `申报要素` sheet，再 patch `integrations.artifacts.artifacts_root`。
- **Harness 注入**：

  ```python
  def fake_harness(resources: AgentResources) -> HarnessRuntime:
      return HarnessRuntime(
          resources=resources,
          tools=ToolCatalog(()),
          brain_factory=factory,
      )
  ```

  再经 `create_app(resource_config=..., harness_factory=fake_harness)` 挂到 `TestClient`。
- **OMS 路径**：`patch("runtime.oms_log.DEFAULT_OMS_LOG_PATH", log_path)` 隔离索引文件。
- 禁止把真实 LLM、MinerU、Oracle 或外部 HTTP 调用混入普通 `run()`。

## Fixtures / Support

- `text_block` / `artifact_block` / `user_message` / `messages_json` 构造 HTTP/run 输入。
- `wait_for_run(client, run_id, expected_status)` 使用有限 deadline（约 5s）轮询。
- Philips Tracking 与 Oracle fixture 留在对应业务测试内，避免共享隐式状态。
- 真实样例目录属于外部测试材料，不复制进仓库；脚本允许通过 env 覆盖路径。
- 文档只记录 env 键名，不读取或写出本地 `.env` 值。

## Coverage Expectations

普通回归至少覆盖：

- 5 工具静态注册与 MinerU mock 路径（`test_tools` 精确名称列表：`parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`、`save_tecan_extraction`、`generate_tecan_import`）；
- ledger 的 `workflow` / `result_json`、事件、spill、usage、时间戳、`/memories/AGENTS.md` seed；
- Harness 的结构化响应捕获、七类事件、`StructuredOutputRecovery` 有界重试与 `jump_to: end`、空 data 壳路径、通用行为；
- HTTP 四端点、workflow 校验、新 session、result 投影、cancel、usage、**OMS `run_created` 索引**；
- Philips Pydantic 合同、严格 Tracking、Oracle 补缺与降级、交易字段隔离；
- Tecan SubAgent 与 Excel 行为未回归；两个 SubAgent 各含 **4** 个 runtime middleware（无 handbook），且 `StructuredOutputRecovery` / `StructuredOutputCompatibility` 仍在场；
- Philips workflow 工具 denylist：含 `extract_archives`，不含帝肯工具。

明确不在普通回归内：真实模型抽取质量、真实 MinerU 内容、真实 Oracle 命中、prompt-cache 数值、跨进程锁和 SQLite 压力、UPS 诊断脚本。

## How to Add Tests

1. 普通回归放在 `backend/tests/test_<area>.py`，实现 `run()`，使用 `assert`。
2. 需要磁盘时使用 `TemporaryDirectory` + 独立 `ResourceConfig(data_dir=...)`；不得污染 `backend/data/`。
3. 需要 Brain 时扩展共享 `FakeBrain` 路径；不要复制另一套替身。
4. 改 workflow/result/事件时同步 `test_api`、`test_harness`、`test_run_ledger` 和 `test_support`。
5. 改 Philips schema/Tracking/Oracle 时同步 `test_philips_wgq_inbound_recognition`；改 Tecan 时同步 `test_tecan_import`。
6. 新工具必须静态登记进 `default_tool_catalog()`，并更新 `test_tools` 的精确名称列表。
7. 为 workflow 收窄 `tools` 时用 denylist 排除**其他业务**工具，保留共享 MinerU 工具；用 `python -m tests.test_workflow_setup` 验证。
8. 改 `after_model` + `jump_to` 时必须覆盖：`can_jump_to` 含 `"end"`、耗尽 `max_retries` 时 `jump_to: "end"`、不可仅 `return None`；空壳耗尽应产出合法 all-null `data`；用 `python -m tests.test_harness` 验证。
9. 改 OMS 索引时同步 `test_api._check_oms_run_created_log`：成功创建一行、upload/422/409 不写、failed 不丢索引、时间戳格式。
10. 真实外部测试单独建文件、默认 skip（或明确标为诊断），并在 `docs/commands.md` 标明开关、服务和样例依赖。

---
*Testing analysis: 2026-07-17*
