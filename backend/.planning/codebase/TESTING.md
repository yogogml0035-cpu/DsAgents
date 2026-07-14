---
last_mapped_commit: 08413f4688e03e5a24fb8ac08270541d280aee5d
---

# Testing Patterns

**Analysis Date:** 2026-07-14

## Test Framework

- **不是 pytest 套件**。`backend/pyproject.toml` 无 `[tool.pytest...]` / `[tool.coverage]`；无 CI 聚合器、无 `self_check` 总控脚本。
- **可执行 assert 脚本**：每个验证模块提供 `run() -> None`，内部用原生 `assert`；失败即异常、进程非零退出。
- **包入口**：`backend/tests/__init__.py` 使 `tests` 可被模块方式导入。运行方式必须是：

  ```powershell
  cd backend
  python -m tests.test_api
  ```

  **不要**用 `python backend/tests/test_xxx.py`（绝对顶层导入 `from runtime...` / `from api...` 会失败）。
- **HTTP 层**：`fastapi.testclient.TestClient`，无需手动起 uvicorn。
- **隔离**：`tempfile.TemporaryDirectory(ignore_cleanup_errors=True)` + 可选 `ResourceConfig(data_dir=...)`，不污染 `backend/data/`。
- **替身**：`unittest.mock.patch` / `patch.dict`；共享替身在 `tests/test_support.py`。

## Run Commands

依赖同步与服务（与约定一致，使用 `uv`）：

```powershell
cd backend
uv sync
```

**普通本地回归**（FakeBrain / mock 网络，不打真实 LLM / MinerU / Oracle）：

```powershell
cd backend
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_import
python -m tests.test_tecan_import
```

**真实集成**（默认与普通回归分开；需真实服务与密钥，**勿**纳入默认门禁）：

```powershell
cd backend
# 终端 1：真实服务（从 backend/.env 读 MINIMAX_* / MINERU_* 等）
uv run uvicorn api:app --host 0.0.0.0 --port 8500

# 终端 2：
# 图片 run — run() 需开关；直接 -m 走 main() 立即执行
$env:DSAGENTS_RUN_REAL_IMAGE_TEST="1"
python -m tests.test_real_image_run
# 或：python -m tests.test_real_image_run --base-url http://127.0.0.1:8500 --image <path>

# 多 PDF + MinerU — run() 需开关；DEFAULT_PDF_DIR 可能指向外部路径，建议 --pdf-dir
$env:DSAGENTS_RUN_REAL_MULTI_PDF_TEST="1"
python -m tests.test_real_multi_pdf_run --pdf-dir <dir>

# MiniMax prompt-cache 基线 — 无开关，-m 直接 run()；非发布门禁
python -m tests.test_minimax_cache_baseline
```

**仅文档变更**：`git diff --check`。

## Test File Organization

| 文件 | 入口 | 角色 |
| --- | --- | --- |
| `tests/test_support.py` | 无 `run()` | 共享替身与 helper，非独立验证入口 |
| `tests/test_tools.py` | `run()` | MinerU env guard、解析/ZIP/解压、`default_tool_catalog` 6 工具 |
| `tests/test_run_ledger.py` | `run()` | `AgentResources` / `SqliteRunLedger`、外溢、usage 聚合、时间戳 |
| `tests/test_harness.py` | `run()` | Brain 工厂 env、ToolTelemetry、artifact 归一、model_usage、事件序列 |
| `tests/test_api.py` | `run()` | upload/runs/cancel/usage/recovery/409 等 HTTP 契约 |
| `tests/test_workflow_setup.py` | `run()` | SKILL.md 约束、四 SubAgent 装配、middleware、`_update_events` |
| `tests/test_philips_wgq_import.py` | `run()` | Philips 投票/裁决/`input_problems`/Excel/Oracle fallback |
| `tests/test_tecan_import.py` | `run()` | Tecan 投票/裁决/`input_problems`/join/币种/工作簿 |
| `tests/test_real_image_run.py` | `run()`+`main()` | 真实 HTTP 图片 run |
| `tests/test_real_multi_pdf_run.py` | `run()`+`main()` | 真实多 PDF + `parse_documents` 覆盖断言 |
| `tests/test_minimax_cache_baseline.py` | `run()` | 真实 MiniMax cache 基线（诊断型） |
| `tests/tests_file/` | — | 夹具资源（如 `imags1.jpg`、用例 PDF 目录） |

命名：统一 `test_*.py`；普通回归 `if __name__ == "__main__": run()`；真实脚本显式标注并与普通回归分离。

## Test Types / Suites

1. **单元 / 组件脚本（普通本地回归）**
   - 工具与 catalog：`test_tools.py`
   - ledger / 资源：`test_run_ledger.py`
   - harness 与 observability：`test_harness.py`
   - 工作流装配：`test_workflow_setup.py`
   - 业务确定性规则：`test_philips_wgq_import.py`、`test_tecan_import.py`

2. **HTTP 集成（仍本地、FakeBrain）**
   - `test_api.py`：`create_app(resource_config=..., harness_factory=fake_harness)` 注入空 `ToolCatalog` + `FakeBrainFactory`。
   - 覆盖：`POST /upload`、`POST /runs`（`extra="forbid"`）、`GET /runs/{id}`（`after_event_id`、`latest_content_event`、顶层 `usage`）、`POST /runs/{id}/cancel`、启动 `fail_incomplete_runs`、session 单飞 `409`。

3. **Cancel 状态机**（`test_api._check_cancel`）
   - 未知 → `404`；终态 → `409`；活跃 → `202` + drain → `cancelled` 且 `reply is None`；drain 中再 cancel → `200` 幂等。
   - 用 `StreamControl` + FakeBrain 输入 `"hold"` 制造活跃窗口。

4. **真实集成（手动、opt-in）**
   - 图片：`DSAGENTS_RUN_REAL_IMAGE_TEST=1` 或 `main()`；默认 `http://127.0.0.1:8500`。
   - 多 PDF/MinerU：`DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1` 或 `main()`；`DEFAULT_PDF_DIR` 当前为外部用户路径（**需确认**，干净环境请传 `--pdf-dir` / `DSAGENTS_PDF_DIR`）。
   - Cache 基线：直接 HTTP，不 import 会开 DB 的 app 代码；第二轮 cache read 为 0 只诊断不失败。

5. **事件契约**
   - 七种事件类型；旧 `tool_call` / `tool_status` / `tool_result` 已删除。
   - `model_usage` 排除出 `latest_content_event`；`after_event_id` 只裁 `events[]`。

6. **业务 `input_problems` 形状**
   - 成功 / 问题两种返回字典（见 `CONVENTIONS.md` Error Handling）。
   - 覆盖 A/B 一致、缺 C、冲突、非法 decisions、空 items、缺 forwarder、Oracle fallback 等。

## Mocking Patterns

- **`FakeBrain` / `FakeBrainFactory`**（`tests/test_support.py`）：
  - 硬断言 `stream_mode == ["messages", "custom", "updates"]`、`subgraphs is True`、`version == "v2"`。
  - 接受 `config["configurable"]["thread_id"]` 与可选 `control`（测试侧用 `StreamControl` 协作 hold/release；生产侧为 LangGraph `RunControl`）。
  - 断言进入 Brain 的 content 已全部为 `type=="text"`（artifact 已归一）。
  - 产出脚本化 v2 chunk：subagent usage（文本应被过滤）、custom progress、updates 工具调用、主 agent 终态 usage + assistant_message。
  - 按 `thread_id` 维护最小 history，验证失败后续跑不回滚 thread。
- **`StreamControl`**：`started` / `release` 两个 `threading.Event`，配合输入 `"hold"` / `"fail"` 脚本路径。
- **网络**：`test_tools.py` 用 `_FakeJsonResponse` / `_FakeZipResponse` + `patch` 替换 `requests` 调用链；不触达真实 MinerU。
- **环境**：`patch.dict(os.environ, {...}, clear=True)` 验证缺 env 抛 `RuntimeError`；Philips Oracle 缺失用空环境验证 fallback。
- **路径**：业务测试 `patch("integrations.artifacts.artifacts_root", return_value=artifacts)` 指向临时目录。
- **Harness 注入**：

  ```python
  def fake_harness(resources: AgentResources) -> HarnessRuntime:
      return HarnessRuntime(
          resources=resources,
          tools=ToolCatalog(()),
          brain_factory=factory,
      )
  app = create_app(resource_config=ResourceConfig(data_dir=...), harness_factory=fake_harness)
  ```

- **禁止**：把真实 LLM / MinerU / Oracle 调用混进普通 `run()` 回归脚本。

## Fixtures / Support

- **`tests/test_support.py`**
  - `text_block` / `artifact_block` / `user_message` / `messages_json` — 构造 HTTP/run 请求体。
  - `wait_for_run(client, run_id, expected_status)` — 5s deadline、0.05s 轮询。
  - `FakeBrain` / `FakeBrainFactory` / `StreamControl`。
- **业务 fixture 工厂**（各业务测试文件内私有）：
  - `_field` / `_extraction` / `_save` / `_generate`
  - Excel：`_tracking_fixture`、`_order_fixture`、`_information_fixture` 等（`openpyxl.Workbook`）
- **磁盘夹具**：`tests/tests_file/imags1.jpg`；多 PDF 用例目录 `tests/tests_file/测试用例1|2|3/`（真实多 PDF 脚本默认仍可能指向外部路径）。
- **env 覆盖键（真实脚本）**：`DSAGENTS_API_BASE_URL` / `DSAGENTS_BASE_URL`、`DSAGENTS_IMAGE_PATH`、`DSAGENTS_PDF_DIR`、`DSAGENTS_RUN_REAL_IMAGE_TEST`、`DSAGENTS_RUN_REAL_MULTI_PDF_TEST`、超时与 poll 相关 `DSAGENTS_REAL_*`。
- **不读密钥入文档**：测试可触发 `load_dotenv(backend/.env)`，但事实文档只记键名。

## Coverage Expectations

- **无自动覆盖率门禁**（未配置 `coverage`）。覆盖清单靠本文件与脚本内 `_check_*` 人工维护。
- **普通回归应覆盖**：
  - 6 工具静态注册与 MinerU 默认 form / ZIP 模式 / 部分失败
  - ledger 快照、事件、大 payload 外溢、`model_usage` 聚合
  - harness 事件序列与 artifact 归一、`assistant_message.payload.thinking`
  - HTTP 全表面与 cancel 全出口、usage 计价边界（可计价 / 不可计价 / 零输入 hit_rate）
  - 四 SubAgent 顺序、每 Agent 1 工具、写 deny、各自 2 个 middleware、`ToolStrategy`
  - Philips/Tecan `input_problems` 与成功生成、关键 Excel 单元格
- **明确不在普通回归内**：真实模型抽取质量、真实并行 task、MinerU PDF 内容正确性、Oracle 命中、prompt-cache 数值。
- **当前缺口**：无 pytest/CI；无 lint/type gate；`test_real_multi_pdf_run.DEFAULT_PDF_DIR` 外部路径需运行者覆盖；无专用真实 Philips/Tecan 端到端验收脚本。

## How to Add Tests

1. **新普通回归脚本**
   - 放在 `backend/tests/test_<area>.py`。
   - 实现 `run() -> None`，用 `assert`；`if __name__ == "__main__": run()`。
   - 需要隔离时用 `TemporaryDirectory` + `ResourceConfig(data_dir=...)`。
   - 需要 Brain 时用 `FakeBrainFactory`，不要调真实模型。
   - 需要 HTTP 时用 `create_app(..., harness_factory=...)` + `TestClient`。
   - 共享构造放 `test_support.py`，不要复制 FakeBrain。

2. **扩展现有脚本**
   - 优先新增 `_check_*` 函数并在 `run()` 中调用，保持单文件可一次跑完相关域。
   - 改事件类型 / HTTP 契约 / Brain stream 参数时，同步改 `test_support.FakeBrain` 断言与 `test_api` / `test_harness`。

3. **业务工具**
   - 在对应 `test_philips_wgq_import.py` 或 `test_tecan_import.py` 增加成功与 `input_problems` 用例；问题项断言 `source` / `location` / `issue` / `action` 语义，而非仅检查 code 字符串。
   - 新工具必须登记进 `default_tool_catalog()`，并更新 `test_tools` 的 handler 名列表（当前 6 个）。

4. **middleware / SubAgent**
   - 改 `runtime_middlewares()` 或 `workflow_subagents()` 后跑 `test_workflow_setup` + `test_harness`；确认每个 SubAgent 仍自带 middleware 实例。

5. **真实集成脚本**
   - 单独文件，模块 docstring 标明 REAL INTEGRATION。
   - `run()` 默认 skip（环境开关）或明确「非回归」；`main()` 可用 argparse。
   - 只通过 HTTP 打已启动服务，或显式文档说明依赖；**不要**并入普通 `run()` 链。

6. **文档同步**
   - 改测试策略或覆盖面后更新本文件；backend 实现变更同步 `backend/.planning/codebase/` 对应 fact docs。

---
*Testing analysis: 2026-07-14*
