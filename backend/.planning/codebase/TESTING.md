# TESTING

> backend 子项目的测试与验证策略。事实来源 = `backend/tests/test_*.py` + `backend/pyproject.toml`。
> 本轮刷新（2026-07-09）已核对当前工作树：上传/下载 artifact 命名已切到时间戳语义，run-event spill 已移到 `data/internal/run-events/`；`python -m tests.test_api` 实跑通过（exit 0）。

## 1. 主要验证手段：直接运行测试脚本（已确认）

当前**没有** pytest 套件，也没有总控自检脚本。backend 代码变更按影响范围直接运行对应脚本：

```powershell
cd backend
python -m tests.test_api
```

- 测试脚本使用 `assert`；任一断言失败则抛异常退出非零。
- 普通本地脚本使用 `FakeBrain` / `FakeBrainFactory`，**不**打真实 LLM，也**不**打真实 MinerU。
- 继续使用 `tempfile.TemporaryDirectory` 做隔离，不污染 `backend/data/`。
- 继续使用 `unittest.mock.patch` / `patch.dict(os.environ, ..., clear=True)` 替身网络与环境。

## 2. 测试目录与模块分工（已确认）

`backend/tests/` 当前文件：

| 文件 | 作用 |
| --- | --- |
| `test_tools.py` | `parse_documents` env guard、`/artifacts/...` 路径解析、默认 `/tasks` form 只开 `return_content_list=true` 且保存 `<stem>.json`，Markdown/图片请求会全量开启 ZIP 参数并保存 `<stem>.zip` / `<first-stem>_etc_<ts>.zip`，`succeeded[]` 不伪造每文件输出、`extract_archives` 解压并返回 md/json/images/origin 文件清单、部分失败不抛异常、task 状态失败透传、`default_tool_catalog()` 注册两个工具 |
| `test_run_ledger.py` | `AgentResources` / `SqliteRunLedger`、`input_messages_json`、`latest_content_event`、大 payload 外溢目录、启动恢复 |
| `test_harness.py` | `DeepAgentsBrainFactory` env 加载、`ToolStatusMiddleware`、`HarnessRuntime.execute_run(messages, ...)`、artifact block 归一化、最终 `assistant_message.payload.thinking` |
| `test_api.py` | `POST /upload`、`POST /runs` 新契约、`latest_content_event`、`assistant_message.payload.thinking`、`after_event_id`、同 session 冲突、失败后续跑、启动恢复 |
| `test_support.py` | `FakeBrain` / `FakeBrainFactory` / `StreamControl` / message helper / `wait_for_run` |
| `test_real_image_run.py` | 手动真实 HTTP 集成脚本：上传图片 → `POST /runs` → 轮询 `GET /runs/{run_id}` 读取 `latest_content_event` / 最终 `reply`。直接 `python -m tests.test_real_image_run` 会通过 `main()`（argparse）立即触达真实服务与模型；`run()` 另起路径默认跳过（需 `DSAGENTS_RUN_REAL_IMAGE_TEST=1` 才执行）。默认服务地址 `DEFAULT_BASE_URL = "http://127.0.0.1:8500"`（可被 `DSAGENTS_API_BASE_URL` 覆盖），与根级 `scripts/start-backend.bat` 端口一致；默认图片 `DEFAULT_IMAGE_PATH` 指向 `tests/tests_images/imags1.jpg`（实际镜像在 `tests/tests_file/imags1.jpg`，需用 `--image` 或 `DSAGENTS_IMAGE_PATH` 指向真实文件） |
| `test_real_multi_pdf_run.py` | 手动真实 HTTP / 模型 / MinerU 集成脚本：一次上传 PDF 清单 → 要求 agent 只调用一次 `parse_documents` 批量解析且传五个 ZIP 输出参数为 true → 轮询 run 完成 → 校验 `parse_documents` 被调用、`file_paths` 匹配且 ZIP 参数匹配（确认可返回 `archive_path`，可选后续 `extract_archives`）。直接 `python -m tests.test_real_multi_pdf_run` 会通过 `main()`（argparse）立即触达真实服务、模型与 MinerU；`run()` 另起路径默认跳过（需 `DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1` 才执行）。默认 PDF 目录 `DEFAULT_PDF_DIR = tests/tests_file/测试用例1/`，默认轮询间隔 `DEFAULT_POLL_SECONDS = 0.1`，可用 `DSAGENTS_API_BASE_URL`、`DSAGENTS_PDF_DIR`、`DSAGENTS_REAL_MULTI_PDF_POLL_SECONDS` 等环境变量或 `--base-url`/`--pdf-dir`/`--poll` 等 CLI 参数覆盖 |

命名约定：

- backend 测试脚本统一放 `backend/tests/`
- 文件名统一 `test_*.py`
- 普通本地回归脚本（`test_api`/`test_harness`/`test_run_ledger`/`test_tools`）保留 `run()`，并用 `if __name__ == "__main__": run()` 支持 `python -m tests.test_xxx` 直接跑
- 真实集成脚本（`test_real_image_run`/`test_real_multi_pdf_run`）走 `main()`（argparse，带 `--base-url`/`--image`/`--pdf-dir`/`--poll` 等参数），`python -m tests.test_real_xxx` 直接执行真实调用；其 `run()` 入口另用环境变量门控默认跳过
- `test_support.py` 只放共享替身/辅助函数，不作为独立验证入口

## 3. 当前覆盖点（已确认）

| 模块 | 覆盖事实 |
| --- | --- |
| `test_tools.py` | `parse_documents` 缺 `MINERU_BASE_URL` 时 fail-fast；默认 `/tasks` form 为 `return_content_list=true` 且其余输出/ZIP 为 false；默认单文件保存 `<stem>.json` 并返回 `result_path`；`return_md=True` 或 `return_images=True` 会把 `return_md/return_content_list/return_images/return_original_file/response_format_zip` 全部提交为 true 并保存 ZIP；多文件只发一次 `/tasks` 并保存 `<first-stem>_etc_<batch-ts>.zip`，`succeeded[]` 不伪造每文件输出；无效输入只进 `failed[]`；task 级失败仍抛异常；custom `tool_status` 进度含批量计数与 `archive_path` 或 `result_path`；`extract_archives` 解出 ZIP 并返回 md/json/images/origin 文件清单；`default_tool_catalog()` 注册 `parse_documents` + `extract_archives` |
| `test_run_ledger.py` | `AgentResources` 创建 3 个 sqlite db；`SqliteRunLedger` 快照/事件/状态机；run 输入字段为 `input_messages_json`；`get_latest_content_event()` 在仅 `status`、`assistant_message→status`、多非 `status`、大 payload artifact 场景下的返回；普通启动和普通 run 不会创建旧的用户可见 spill 子目录；大 payload（`max_inline_bytes=10`）只在真正 spill 时写到 `data/internal/run-events/*.json`；时间戳迁移：旧 UTC ISO / naive UTC 文本经 `_normalize_existing_timestamps(assume_naive_utc=True)` 平移到本机时区，且对已是本机时区的文本再次迁移保持不变（幂等，对应 `normalized_again` 断言） |
| `test_harness.py` | `DeepAgentsBrainFactory` 从 `MINIMAX_*` 构造 `ChatAnthropic`；`ToolStatusMiddleware` 成功发 `started→completed`，异常发 `started→error` 并透传；`execute_run(messages, ...)` 事件序列 = `status/thinking/text_delta/tool_call/tool_status/tool_result/text_delta/assistant_message/status`；`tool_call` payload 含 `tool_call_id/name/args`；图片类 `tool_result` 只保留摘要不带 base64；`assistant_message.payload` 保留最终 `thinking` 与 `text`；同 `thread_id` 续跑 reply 计数递增；artifact block 进入 Brain 前会被归一化为文本路径提示 |
| `test_api.py` | `POST /upload` 支持单文件、多文件、混合文件；`POST /files` 返回 `404`；`POST /runs` 只接受 `messages[] + content blocks`，旧 `message` 请求失败；上传后引用 artifact 路径的 run 可轮询到 `succeeded`；`after_event_id` 只裁剪 `events[]`，不影响 `latest_content_event`；成功 run 的最终 `latest_content_event.type == "assistant_message"` 且 payload 含最终 `thinking`；同 `session_id` 并发返回 `409`；失败 run 后同 session 可续跑；未知 run 返回 `404`；app 启动时会清理遗留 `queued/running` run |

## 4. 测试替身与策略（已确认）

- `FakeBrainFactory.create(**_)` → `FakeBrain`：证明 Brain 可替换。
- `FakeBrain.stream(...)`：断言 Brain 侧收到的是 `messages[]` 与 text blocks；按 `thread_id` 维护最小 history，以验证失败 run 后同 thread 续跑不回滚。
- `StreamControl`：用 `"hold"` 输入制造并发冲突窗口。
- 网络/环境替身：`patch("hands.get_stream_writer")`、`patch("tools.get_stream_writer")`、`patch("tools._artifacts_root")`、`patch("tools.requests.post")`、`patch("tools.requests.get")`、`patch.dict(os.environ, ..., clear=True)`。

## 5. 验证流程（按变更类型）

- **仅文档变更**：`git diff --check`（检查空白/行尾错误）。
- **代码变更**（`backend/*.py` 或 `backend/tests/*.py`）：按影响范围跑对应 `cd backend && python -m tests.test_xxx`。
- **HTTP 行为变更**：默认已被 `backend/tests/test_api.py` 覆盖，无需手动起服务。

## 6. 当前缺口（待补充）

- **没有 pytest / CI**：当前测试仍是 `assert` 风格脚本，不是 pytest 套件，也没有 CI 自动执行。
- **没有 lint / type-check gate**：未配置 ruff / mypy / black 等门禁（`pyproject.toml` 无对应 `[tool.*]` 段）。
- **普通本地脚本没有真实 provider 覆盖**：普通本地脚本不触达真实 `MINIMAX_*` / `MINERU_*`；`backend/tests/test_real_image_run.py` 可手动打本地 HTTP 服务与真实模型，`backend/tests/test_real_multi_pdf_run.py` 可手动打本地 HTTP 服务、真实模型与 MinerU 做多 PDF 端到端冒烟。
