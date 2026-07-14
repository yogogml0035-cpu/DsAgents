---
last_mapped_commit: 08413f4688e03e5a24fb8ac08270541d280aee5d
---

# Testing Patterns

**Analysis Date:** 2026-07-15

## Test Framework

- **不是 pytest 套件**。`backend/pyproject.toml` 无 pytest、coverage、ruff、mypy 或 black 门禁，也无 CI 聚合器。
- 每个普通验证模块提供 `run() -> None`，内部使用原生 `assert`；失败即异常和非零退出。
- 必须在 `backend/` 下以模块方式执行：

  ```powershell
  cd backend
  python -m tests.test_api
  ```

  不要直接运行 `python tests/test_xxx.py`，否则绝对顶层导入可能失败。
- HTTP 测试使用 `fastapi.testclient.TestClient`；磁盘隔离使用 `TemporaryDirectory` + `ResourceConfig(data_dir=...)`。
- `FakeBrain` / `FakeBrainFactory`、消息构造器与轮询 helper 集中在 `tests/test_support.py`。
- 真实模型、MinerU、Oracle 和外部 HTTP 验收与普通本地回归分开，默认不执行。

## Run Commands

依赖同步：

```powershell
cd backend
uv sync
```

普通本地回归（FakeBrain / mock，不触达真实外部服务）：

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

真实集成（手工 opt-in，不纳入普通门禁）：

```powershell
cd backend
uv run uvicorn api:app --host 0.0.0.0 --port 8500

$env:DSAGENTS_RUN_REAL_IMAGE_TEST="1"
python -m tests.test_real_image_run

$env:DSAGENTS_RUN_REAL_MULTI_PDF_TEST="1"
python -m tests.test_real_multi_pdf_run --pdf-dir <dir>

$env:DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST="1"
python -m tests.test_real_philips_wgq_inbound_recognition

# 诊断型 MiniMax prompt-cache 基线；无开关，会触达真实端点
python -m tests.test_minimax_cache_baseline
```

仅文档变更至少执行 `git diff --check`。

## Test File Organization

| 文件 | 入口 | 角色 |
| --- | --- | --- |
| `tests/test_support.py` | 无独立 `run()` | `FakeBrain` / `FakeBrainFactory`、消息构造、轮询 helper、结构化结果 fixture |
| `tests/test_tools.py` | `run()` | MinerU env guard、解析/ZIP/解压、`default_tool_catalog` 5 工具 |
| `tests/test_run_ledger.py` | `run()` | 资源、run ledger、`workflow` / `result_json` 持久化、外溢、usage、时间戳 |
| `tests/test_harness.py` | `run()` | Brain 装配、ToolTelemetry、artifact 归一、事件序列、Philips `structured_response` 成功/缺失 |
| `tests/test_api.py` | `run()` | upload/runs/workflow/result/cancel/usage/recovery/session 单飞等 HTTP 契约 |
| `tests/test_workflow_setup.py` | `run()` | Skill 文件、Philips ToolStrategy/工具裁剪/无 SubAgent、Tecan 两个 SubAgent、middleware、`_update_events` |
| `tests/test_philips_wgq_inbound_recognition.py` | `run()` | Pydantic 结果合同、Tracking 严格倒序选行、申报页优先、Oracle 补缺/降级、交易字段隔离 |
| `tests/test_tecan_import.py` | `run()` | Tecan A/B 裁决、`input_problems`、join、币种和工作簿 |
| `tests/test_real_philips_wgq_inbound_recognition.py` | `run()` | DHL、DSV、FedEx、UPS、康捷空 upload → workflow run → poll 真实 HTTP 验收 |
| `tests/test_real_image_run.py` | `run()` + `main()` | 真实 HTTP 图片 run |
| `tests/test_real_multi_pdf_run.py` | `run()` + `main()` | 真实多 PDF + MinerU |
| `tests/test_minimax_cache_baseline.py` | `run()` | 真实 MiniMax cache 基线（诊断型） |
| `tests/tests_file/` | — | 仓库内夹具资源 |

命名统一 `test_*.py`。普通回归以 `if __name__ == "__main__": run()` 收口；真实脚本必须在模块说明和命令文档中标明外部依赖。

## Test Types / Suites

### 1. Run / HTTP / ledger

- `test_run_ledger` 断言 `RunSnapshot.workflow`、解析后的 `result`、终态 `status.payload.result` 和重开 SQLite 后的持久化结果。
- `test_api` 覆盖四个端点、增量轮询、`latest_content_event`、usage 计价、启动恢复和 cancel 全出口。
- Philips workflow 覆盖：未知 workflow `422`、非空 `session_id` `422`、每次服务端生成不同 session、GET 顶层与 `run` 快照同时暴露 `workflow` / `result`。
- `result.outcome=input_problems` 仍断言 `run.status=succeeded`；结构化结果缺失则断言 `failed`。

### 2. Harness / stream / Brain 装配

- `FakeBrain` 硬断言 `stream_mode=["messages","custom","updates"]`、`subgraphs=True`、`version="v2"` 和 `thread_id=session_id`。
- 验证 artifact block 进入 Brain 前全部转为 text block。
- 覆盖 subagent usage 保留但文本过滤、thinking/text/tool progress/tool execution/assistant message/model usage 七类事件。
- Philips invocation 验证 `BrainFactory.create(..., workflow=...)`、关闭 Anthropic thinking、从 `updates` 捕获嵌套 `structured_response` 并再次 Pydantic 校验。
- `test_workflow_setup` 断言 Philips 仅暴露 `parse_documents` 与 `lookup_philips_wgq_master_data`、`subagents=[]`；通用/Tecan 路径仍注册 `tecan-extractor-a/b`。

### 3. Philips 确定性业务规则

- `PhilipsWgqRecognitionResult` 使用固定 alias、`extra="forbid"` 和 outcome/data/problems 联动校验。
- 重复 12NC 保持原商品行顺序，不在 schema 层合并。
- 12NC 去空格/连字符，超过 12 位取后 12 位。
- Tracking 只读 `进口` sheet；A 列 trim 后严格接受 `进口` / `出1` / `出2` / `已出`；从后向前取第一条合格行。
- 空白状态、`备注进口` 等非严格状态不得 fallback；未命中进口行时不得读取同料号 `申报要素` 行。
- Tracking 内 `申报要素` sheet 优先，所选进口行次之；Oracle 只补仍缺失字段。
- 工具结果不暴露旧数量、价格、单号、金额或重量；`//`、`N/A`、“需确认…”、“未找到…”等占位值归一为 `null`。
- Oracle 配置缺失、查询异常、未命中均返回 `problems`，不抛掉 PDF/Tracking 数据。

跨单据关联、多个票次拒绝、ZIP/DOCX 忽略、PDF 字段优先级由 Skill 指令和真实 HTTP 验收覆盖；确定性的 schema、run 通道与主数据规则由普通回归锁定。

### 4. Tecan 回归

Tecan 保留 A/B extractor、必要时 C 回查、`input_problems`、canonical 和 Excel 生成。Philips 删除旧 A/B/C 与 Excel 后，`test_tecan_import` / `test_workflow_setup` 继续防止误删 Tecan 行为。

### 5. Cancel 状态机

`test_api._check_cancel` 覆盖未知 `404`、终态 `409`、活跃 `202` + drain → `cancelled`、drain 中重复 cancel `200`。测试用 `StreamControl` 的 `started` / `release` 事件制造活跃窗口。

### 6. 真实 Philips HTTP 验收

`test_real_philips_wgq_inbound_recognition.py` 默认 skip；开关为 `DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1`。默认样例根可用 `DSAGENTS_PHILIPS_WGQ_SAMPLE_ROOT` 覆盖，服务地址用 `DSAGENTS_API_BASE_URL`，超时/轮询分别用 `DSAGENTS_REAL_PHILIPS_WGQ_TIMEOUT_SECONDS` / `DSAGENTS_REAL_PHILIPS_WGQ_POLL_SECONDS`。

脚本逐例上传 PDF、共用 Tracking 和可选无关附件，提交固定 workflow 并轮询终态；断言新 session、运单号、商品行、12NC、数量/价格、至少一项主数据、lookup 工具调用，以及 ZIP/DOCX 对应 `partial_success` 问题。

## Mocking Patterns

- **Brain**：`FakeBrainFactory` 记录 `created_workflows` 和收到的 payload；`FakeBrain` 按输入文本产生成功、`input_problems`、缺少 structured response、失败、hold 等路径。
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

- 禁止把真实 LLM、MinerU、Oracle 或外部 HTTP 调用混入普通 `run()`。

## Fixtures / Support

- `text_block` / `artifact_block` / `user_message` / `messages_json` 构造 HTTP/run 输入。
- `wait_for_run(client, run_id, expected_status)` 使用有限 deadline 轮询。
- `_recognition_result` 提供完整固定字段的 Philips 成功与 `input_problems` payload。
- Philips Tracking 与 Oracle fixture 留在对应业务测试内，避免共享隐式状态。
- 真实样例目录属于外部测试材料，不复制进仓库；脚本允许通过 env 覆盖路径。
- 文档只记录 env 键名，不读取或写出本地 `.env` 值。

## Coverage Expectations

普通回归至少覆盖：

- 5 工具静态注册与 MinerU mock 路径；
- ledger 的 `workflow` / `result_json`、事件、spill、usage；
- Harness 的结构化响应捕获、七类事件和通用行为；
- HTTP 四端点、workflow 校验、新 session、result 投影、cancel、usage；
- Philips Pydantic 合同、严格 Tracking、Oracle 补缺与降级、交易字段隔离；
- Tecan SubAgent 与 Excel 行为未回归。

明确不在普通回归内：真实模型抽取质量、真实 MinerU 内容、真实 Oracle 命中、prompt-cache 数值、跨进程锁和 SQLite 压力。

## How to Add Tests

1. 普通回归放在 `backend/tests/test_<area>.py`，实现 `run()`，使用 `assert`。
2. 需要磁盘时使用 `TemporaryDirectory` + 独立 `ResourceConfig(data_dir=...)`；不得污染 `backend/data/`。
3. 需要 Brain 时扩展共享 `FakeBrain` 路径；不要复制另一套替身。
4. 改 workflow/result/事件时同步 `test_api`、`test_harness`、`test_run_ledger` 和 `test_support`。
5. 改 Philips schema/Tracking/Oracle 时同步 `test_philips_wgq_inbound_recognition`；改 Tecan 时同步 `test_tecan_import`。
6. 新工具必须静态登记进 `default_tool_catalog()`，并更新 `test_tools` 的精确名称列表。
7. 真实外部测试单独建文件、默认 skip，并在 `docs/commands.md` 标明开关、服务和样例依赖。

---
*Testing analysis: 2026-07-15*
