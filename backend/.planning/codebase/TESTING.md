# TESTING

> backend 子项目的测试与验证策略。事实来源 = `backend/tests/test_*.py` + `backend/pyproject.toml` + `tests/test_support.py`。
> 本轮刷新（2026-07-13）已核对当前工作树：本地测试脚本、`run()` / `main()` 入口、真实集成开关、cancel 覆盖、FakeBrain 契约（`updates` + `subgraphs` + `v2`）、业务测试的 `input_problems` 形状均与代码逐一比对。

## 1. 主要验证手段：直接运行测试脚本（已确认）

当前**没有** pytest 套件，也**没有**总控自检脚本（无 `self_check` 之类聚合器）。backend 代码变更按影响范围直接运行对应脚本：

```powershell
cd backend
python -m tests.test_api
```

- 测试脚本使用 `assert`；任一断言失败则抛异常退出非零。
- `tests/` 下有 `__init__.py`，使 `tests` 成为可被 `python -m tests.test_xxx` 导入的包；**不**用 `python backend/tests/test_xxx.py`（绝对顶层导入 `from runtime...` / `from integrations...` / `from skills...` 会失败）。
- 普通本地脚本使用 `FakeBrain` / `FakeBrainFactory`，**不**打真实 LLM，也**不**打真实 MinerU。
- 继续使用 `tempfile.TemporaryDirectory`（多数带 `ignore_cleanup_errors=True`）做隔离，不污染 `backend/data/`。
- 继续使用 `unittest.mock.patch` / `patch.dict(os.environ, ..., clear=True)` 替身网络与环境；HTTP 层用 `fastapi.testclient.TestClient`，无需手动起服务。
- `pyproject.toml` 当前**没有** `[tool.pytest...]` / `[tool.coverage]` / `[tool.ruff]` / `[tool.mypy]` / `[tool.black]` 段，即无 pytest、无覆盖率、无 lint / type-check 门禁（已确认）。

## 2. 测试目录与模块分工（已确认）

`backend/tests/` 当前文件（`__init__.py` + 10 个 `test_*.py`，其中 3 个显式真实集成脚本 + `test_support.py` + 资源目录）：

| 文件 | 入口 | 作用 |
| --- | --- | --- |
| `test_tools.py` | `run()` | MinerU 从 `backend/.env` 加载、`parse_documents` env guard（缺 `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_TIMEOUT_SECONDS` 抛 `RuntimeError`，`MINERU_EFFORT` 可空）、`/artifacts/...` 路径解析、默认 `/tasks` form 只开 `return_content_list=true` 且保存 `<stem>.json`，Markdown/图片请求会全量开启 ZIP 参数并保存 `<stem>.zip` / `<first-stem>_etc_<ts>.zip`，`succeeded[]` 不伪造每文件输出、`extract_archives` 解压并返回文件清单、部分失败不抛异常、task 状态失败透传、`default_tool_catalog()` 注册 **6 个**工具（2 通用 + 4 Philips/Tecan 业务，每 Skill 2 个） |
| `test_run_ledger.py` | `run()` | `AgentResources` / `SqliteRunLedger`、`input_messages_json`、`latest_content_event`、大 payload 外溢目录、启动恢复、`model_usage` 事件聚合（总量 + by_agent + per-call）与 `get_latest_content_event` 排除 `model_usage`、UTC ISO-8601 毫秒时间戳 |
| `test_harness.py` | `run()` | `DeepAgentsBrainFactory` 从 `backend/.env` 加载 MiniMax 配置、`ToolTelemetry`（`tool_execution` 三态 + 计时 + scope）、artifact block 归一化（`ARTIFACT_REFERENCE_HINT`）、最终 `assistant_message.payload.thinking`、`observability.model_usage` 规范化（主/subagent scope + cache_creation 汇总）、单 run 只产主 agent 一次 `model_usage`、subagent usage 进 by_agent 但文本不外泄、failed run 保留异常前 usage、新事件序列（`tool_execution` / `tool_progress`） |
| `test_api.py` | `run()` | `POST /upload`（单/多/混合/重名/Unicode 空白归一）、`POST /runs` 新契约（`extra="forbid"`，旧单数 `message` 返回 `422`）、`latest_content_event`、`assistant_message.payload.thinking`、`after_event_id`（只裁剪 `events[]`，不影响 `latest_content_event` 与顶层 `usage`）、同 session 冲突 `409`、失败后续跑、启动恢复、未知 run `404`、`POST /files` 返回 `404`、顶层 `usage`（succeeded/failed 均返回、cache_hit_rate、长/短上下文 tier 计价、节省公式、不可计价时金额 `null`、零输入 hit_rate `null`）、**`POST /runs/{run_id}/cancel` 完整覆盖**（见 §3） |
| `test_workflow_setup.py` | `run()` | 两个 Skill 的 `SKILL.md` 行数 `<= 100` 且含触发关键词、四个 SubAgent 的 `name` 顺序 / 各只挂 1 个业务工具 / 写权限全 deny / `ToolStrategy` structured response / **每个 SubAgent 各自装 `runtime_middlewares()`**（声明式 SubAgent 不继承主 Agent middleware）、brain 工厂 kwargs、`_update_events`、A/B 同一 AIMessage 的两个 task 调用与 task 事件 |
| `test_philips_wgq_import.py` | `run()` | Philips A/B/C 投票、裁决 `decisions`、`generate_philips_wgq_import` 的 `input_problems` 形状（A/B 一致才成功；缺 C / 冲突未决 / 必需字段缺失 / 空 items / 缺 forwarder / 非法 decision 均返回 `{"code":"input_problems","problems":[{"source","location","issue","action"}]}`）、旧合同拒绝、料号合并、tracking 历史净重、Oracle 配置缺失 fallback（单元格写 `"需确认：申报计量单位"`）与三个工作簿关键单元格 |
| `test_tecan_import.py` | `run()` | Tecan A/B/C 投票、裁决 `decisions`、`generate_tecan_import` 的 `input_problems` 形状、订单/信息表 join、Net Price 推导、币种、跨 sheet、来源冲突一律作为 `input_problems`（无 `info_source_preference` / `pn_info_source_overrides`）、重量守恒与工作簿关键单元格 |
| `test_support.py` | （无） | `FakeBrain` / `FakeBrainFactory` / `StreamControl` / `text_block` / `artifact_block` / `user_message` / `messages_json` / `wait_for_run`；只放共享替身/辅助函数，不作为独立验证入口 |
| `test_real_image_run.py` | `run()`（受 `DSAGENTS_RUN_REAL_IMAGE_TEST=1` 开关）+ `main()`（argparse，立即执行） | 手动真实 HTTP 集成脚本：上传图片 → `POST /runs` → 轮询 `GET /runs/{run_id}` 读取 `latest_content_event` / 最终 `reply`。直接 `python -m tests.test_real_image_run` 会通过 `main()`（argparse，`--base-url`/`--image`）立即触达真实服务与模型；`run()` 默认跳过，需 `DSAGENTS_RUN_REAL_IMAGE_TEST=1` 才执行。`DEFAULT_BASE_URL = "http://127.0.0.1:8500"`（可被 `DSAGENTS_API_BASE_URL` 覆盖，与根级 `scripts/start-backend.bat` 端口一致）；`DEFAULT_IMAGE_PATH` 指向 `backend/tests/tests_file/imags1.jpg`（可用 `--image` 或 `DSAGENTS_IMAGE_PATH` 覆盖） |
| `test_real_multi_pdf_run.py` | `run()`（受 `DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1` 开关）+ `main()`（argparse，立即执行） | 手动真实 HTTP / 模型 / MinerU 集成脚本：一次上传 PDF 清单 → 要求 agent 调用 `parse_documents` 解析这些文件 → 轮询 run 完成 → 校验至少发生过一次 `parse_documents` 调用，且所有上传 `file_path` 都被纳入过解析调用；不硬编码批量调用次数或 ZIP/JSON 输出参数。直接 `python -m tests.test_real_multi_pdf_run` 会通过 `main()`（argparse，`--base-url`/`--pdf-dir`/`--poll`）立即触达真实服务、模型与 MinerU；`run()` 默认跳过，需 `DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1` 才执行。`DEFAULT_PDF_DIR` 当前指向外部用户路径（**需确认**是否应改为仓库内固定夹具，运行前用 `--pdf-dir` / `DSAGENTS_PDF_DIR` 指向真实可用目录），`DEFAULT_POLL_SECONDS = 0.1`，亦可用 `DSAGENTS_API_BASE_URL` / `DSAGENTS_REAL_MULTI_PDF_POLL_SECONDS` 覆盖 |
| `test_minimax_cache_baseline.py` | `run()`（`__main__` 直接调用，无开关） | 手动真实 MiniMax-M3 prompt-cache 基线脚本（默认不运行、非普通回归、非第一阶段发布门禁）：五分钟内以同一 session 串行跑两轮、保持 ≥512 token 稳定前缀，逐轮对应打印用户消息、最终 `reply` 与 `model_usage`（`cache_read_input_tokens` 等）；第二轮 cache read 为 0 时只记录诊断不失败。只通过 HTTP 调 `BASE_URL`（默认 `http://127.0.0.1:8500`，可用 `DSAGENTS_BASE_URL` 覆盖），不导入会开 DB 的 app 代码；HTTP 非 2xx 会原样报告状态码和响应体 |

命名与入口约定：

- backend 测试脚本统一放 `backend/tests/`，文件名统一 `test_*.py`；`tests/__init__.py` 必须保留。
- **普通本地回归脚本**（`test_api` / `test_harness` / `test_run_ledger` / `test_tools` / `test_workflow_setup` / `test_philips_wgq_import` / `test_tecan_import`）保留 `run()`，并用 `if __name__ == "__main__": run()` 支持直接运行。
- **真实集成脚本**（`test_real_image_run` / `test_real_multi_pdf_run` / `test_minimax_cache_baseline`）必须显式标注，并默认与普通本地脚本分开运行：
  - `test_real_image_run` / `test_real_multi_pdf_run`：`python -m tests.test_real_xxx` 走 `main()`（argparse）立即执行真实调用；`run()` 默认跳过，需对应 `DSAGENTS_RUN_REAL_*_TEST=1` 才执行。
  - `test_minimax_cache_baseline`：`python -m tests.test_minimax_cache_baseline` 直接调 `run()` 触达真实服务；服务进程自行从 `backend/.env` 读取 `MINIMAX_*`，非普通回归、非第一阶段发布门禁。
  - **不要**把真实调用混进普通回归脚本，也不要把这些脚本纳入默认验证流程。
- `test_support.py` 只放共享替身/辅助函数，不作为独立验证入口。

## 3. cancel 测试覆盖（`test_api._check_cancel`，已确认）

`test_api.py` 的 `_check_cancel` 用 `FakeBrainFactory(control=hold_control)` 制造活跃 run 窗口，完整覆盖取消状态机的所有出口：

- 未知 run `POST /runs/missing/cancel` → `404`。
- 终态（`succeeded`）run → `409`（`Run already terminal`）。
- 活跃（`running`）run → `202`（`status:cancelling`），由 `RunControl` 协作 drain 投影为 `cancelled`。
- drain 中再次 cancel → `200`（幂等，`status:cancelling`）。
- 等待 run 到达 `cancelled`；断言 `cancelled_run["status"] == "cancelled"` 且 `reply is None`（取消不泄漏 succeeded reply）。

## 4. FakeBrain 契约（`tests/test_support.py`，已确认）

`FakeBrain.stream(...)` 是本地测试对运行时 stream 解析的唯一替身，硬性断言 Brain 调用契约：

- `stream_mode == ["messages", "custom", "updates"]`（**不是**旧的 `values`，已切换为 `updates`）。
- `subgraphs is True`。
- `version == "v2"`。
- 接受可选的 `control: RunControl`（用于 cancel 测试的协作 drain）。

`FakeBrain` 产出的 chunk 序列覆盖：

- SubAgent `messages` chunk（带 `usage_metadata` 与 `lc_agent_name="philips-wgq-extractor-a"`）→ 验证 subagent usage 计入而文本被过滤。
- `custom` chunk → `tool_progress` / `tool_execution`（`ToolTelemetry` 自发）。
- `updates` chunk → `_update_events` 派生 `assistant_message` / `tool_execution`。
- 主 agent 终态 `messages` chunk（带 `usage_metadata`）→ `model_usage`（main） + `text_delta`。
- 按 `thread_id` 维护最小 history，验证失败 run 后同 thread 续跑不回滚。

`FakeBrainFactory.create(**_)` → `FakeBrain`，证明 Brain 可替换。

## 5. 事件序列断言（已确认）

`test_api.py` 与 `test_harness.py` 断言新事件序列。run event 类型固定 7 种：

```text
status, tool_execution, tool_progress, thinking, text_delta, assistant_message, model_usage
```

- 旧事件 `tool_call` / `tool_status` / `tool_result` 已删除，测试不再断言这些类型。
- `test_api.py` 断言成功 run 的事件序列中按序出现 `tool_execution`、`tool_progress`、`assistant_message`、`model_usage` 等关键类型。
- `model_usage` 被排除出 `latest_content_event`（`test_run_ledger.py` 断言）。
- `after_event_id` 只裁剪 `events[]`，不影响 `latest_content_event`，也不影响顶层 `usage`。

## 6. 业务测试的 `input_problems` 形状（已确认）

`test_philips_wgq_import.py` 与 `test_tecan_import.py` 围绕「每 Skill 2 个业务 Tool」与统一问题返回形状：

- 成功：`{"status":"generated","canonical_artifact","artifacts","manual_checks"}`。
- 问题：`{"code":"input_problems","problems":[{"source","location","issue","action"}]}`。
- 覆盖：A/B 一致才成功；一路失败（缺 C）；A/B 冲突（需 C）；双失败；无多数裁决；必需字段缺失；空 items；缺 forwarder；非法 `decisions`（引用不存在的 conflict_id）；C 单独；两票多数；旧合同拒绝。
- Philips 额外：料号合并、tracking 历史净重、Oracle 配置缺失 fallback（单元格写 `"需确认：申报计量单位"`，由 `test_philips_wgq_import.py` 断言）、三个工作簿（tracking / invoice,packing / bonded checklist）关键单元格。
- Tecan 额外：订单 + 信息表 join、Net Price 推导、币种、跨 sheet、来源冲突一律作 `input_problems`（无 `info_source_preference` / `pn_info_source_overrides`）、重量守恒、工作簿关键单元格。

## 7. 测试替身与策略（已确认）

- `FakeBrainFactory.create(**_)` → `FakeBrain`：证明 Brain 可替换。
- `FakeBrain.stream(...)`：断言 Brain 侧收到的是 `messages[]` 与 text blocks（artifact block 已归一化为 `ARTIFACT_REFERENCE_HINT`）；按 `thread_id` 维护最小 history；在 SubAgent namespace 上发带 `usage_metadata` 与 `lc_agent_name` 的 subagent chunk，验证 subagent usage 计入而文本被过滤。
- `StreamControl`：用 `"hold"` 输入制造并发冲突窗口与 cancel drain 窗口（`started` / `release` 两个 `threading.Event`）。
- 网络/环境替身：保留 MinerU stream/requests patch；业务测试 patch `integrations.artifacts.artifacts_root`，Philips 用空环境验证 Oracle fallback。
- message helper：`text_block` / `artifact_block` / `user_message` / `messages_json` 构造请求体；`wait_for_run` 以 5s deadline + 0.05s 轮询等待 run 到达预期状态。

## 8. 验证流程（按变更类型）

- **仅文档变更**：`git diff --check`（检查空白/行尾错误）。
- **代码变更**（`api.py`、`runtime/**/*.py`、`integrations/**/*.py`、`skills/**/*.py` 或 `backend/tests/*.py`）：按影响范围跑对应 `cd backend && python -m tests.test_xxx`。
- **HTTP 行为变更**：默认已被 `backend/tests/test_api.py` 覆盖，无需手动起服务。
- **Skills/SubAgents 或业务工具变更**：运行 `test_workflow_setup`、对应 Philips/Tecan 测试，并复跑 `test_tools` / `test_harness` / `test_api`。
- **middleware 变更**：若改 `runtime_middlewares()`，必须同步主 Agent 与所有 SubAgent 装配（声明式 SubAgent 不继承主 Agent middleware），并复跑 `test_workflow_setup` / `test_harness`。
- **真实集成验证（手动、非默认）**：先 `uv run uvicorn api:app --host 0.0.0.0 --port 8500`，再 `python -m tests.test_real_image_run` / `test_real_multi_pdf_run` / `test_minimax_cache_baseline`，并按需设置 `DSAGENTS_RUN_REAL_*_TEST=1` / `DSAGENTS_API_BASE_URL` / `DSAGENTS_BASE_URL` / `DSAGENTS_IMAGE_PATH` / `DSAGENTS_PDF_DIR`。

## 9. 当前缺口（待补充）

- **没有 pytest / CI**：当前测试仍是 `assert` 风格脚本，不是 pytest 套件，也没有 CI 自动执行，也没有 `self_check` 之类总控聚合脚本。
- **没有 lint / type-check gate**：`pyproject.toml` 未配置 ruff / mypy / black / flake8 等门禁（无对应 `[tool.*]` 段）。
- **没有覆盖率统计**：未配置 `coverage`，覆盖点靠本文件 §2/§3/§5/§6 的人工清单维护。
- **普通本地脚本没有真实业务编排覆盖**：配置/投票/Excel 规则可本地验证，但 A/B 模型抽取质量、真实并行 task 行为、MinerU PDF 内容与 Oracle 命中仍需独立的 `test_real_*` 手动验证；现有三个真实脚本不承担 Philips/Tecan 验收。
- **`test_real_multi_pdf_run.DEFAULT_PDF_DIR` 指向外部用户路径**（**需确认**）：应考虑改为仓库内固定夹具或显式要求运行者传入，避免在干净环境直接运行时报错。
