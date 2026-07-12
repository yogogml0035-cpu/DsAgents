# TESTING

> backend 子项目的测试与验证策略。事实来源 = `backend/tests/test_*.py` + `backend/pyproject.toml`。
> 本轮刷新（2026-07-10）已核对当前工作树：新增 Skill/Subagent 装配测试与两个业务回归脚本；普通本地测试继续与真实模型、MinerU、Oracle 分离。

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
| `test_run_ledger.py` | `AgentResources` / `SqliteRunLedger`、`input_messages_json`、`latest_content_event`、大 payload 外溢目录、启动恢复、`model_usage` 事件聚合（总量 + by_agent + per-call）与 `get_latest_content_event` 排除 `model_usage` |
| `test_harness.py` | `DeepAgentsBrainFactory` env 加载、`ToolStatusMiddleware`、`HarnessRuntime.execute_run(messages, ...)`、artifact block 归一化、最终 `assistant_message.payload.thinking`、`_model_usage` 规范化（主/subagent scope + cache_creation 汇总）、单 run 只产主 agent 一次 `model_usage`、subagent usage 进 by_agent 但密文不外泄、failed run 保留异常前 usage |
| `test_api.py` | `POST /upload`、`POST /runs` 新契约、`latest_content_event`、`assistant_message.payload.thinking`、`after_event_id`、同 session 冲突、失败后续跑、启动恢复、顶层 `usage`（succeeded/failed 均返回、`after_event_id` 不影响 usage、cache_hit_rate、长/短上下文 tier 计价、节省公式、不可计价时金额 `null`、零输入 hit_rate `null`） |
| `test_workflow_setup.py` | 两个 Skill 的行数/挂载、四个 subagent 的工具/权限/structured response、DeepAgent 装配、A/B 同一 AIMessage 的两个 task 调用与 task 事件 |
| `test_philips_wgq_import.py` | Philips A/B/C 投票、裁决、旧合同拒绝、料号合并、tracking 历史净重、Oracle 配置缺失 fallback 与三个工作簿关键单元格 |
| `test_tecan_import.py` | Tecan A/B/C 投票、裁决、旧合同拒绝、订单/信息表内容识别、Net Price 推导、币种、跨 sheet、来源冲突显式选择、重量守恒与工作簿关键单元格 |
| `test_support.py` | `FakeBrain` / `FakeBrainFactory` / `StreamControl` / message helper / `wait_for_run` |
| `test_real_image_run.py` | 手动真实 HTTP 集成脚本：上传图片 → `POST /runs` → 轮询 `GET /runs/{run_id}` 读取 `latest_content_event` / 最终 `reply`。直接 `python -m tests.test_real_image_run` 会通过 `main()`（argparse）立即触达真实服务与模型；`run()` 另起路径默认跳过（需 `DSAGENTS_RUN_REAL_IMAGE_TEST=1` 才执行）。默认服务地址 `DEFAULT_BASE_URL = "http://127.0.0.1:8500"`（可被 `DSAGENTS_API_BASE_URL` 覆盖），与根级 `scripts/start-backend.bat` 端口一致；默认图片 `DEFAULT_IMAGE_PATH` 指向 `tests/tests_images/imags1.jpg`（实际镜像在 `tests/tests_file/imags1.jpg`，需用 `--image` 或 `DSAGENTS_IMAGE_PATH` 指向真实文件） |
| `test_real_multi_pdf_run.py` | 手动真实 HTTP / 模型 / MinerU 集成脚本：一次上传 PDF 清单 → 要求 agent 调用 `parse_documents` 解析这些文件 → 轮询 run 完成 → 校验至少发生过一次 `parse_documents` 调用，且所有上传 `file_path` 都被纳入过解析调用；不再硬编码要求单次批量调用，也不硬编码要求 ZIP/JSON 输出参数，实际解析策略与结果格式由 agent 按用户请求决定。直接 `python -m tests.test_real_multi_pdf_run` 会通过 `main()`（argparse）立即触达真实服务、模型与 MinerU；`run()` 另起路径默认跳过（需 `DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1` 才执行）。默认 PDF 目录 `DEFAULT_PDF_DIR = tests/tests_file/测试用例1/`，默认轮询间隔 `DEFAULT_POLL_SECONDS = 0.1`，可用 `DSAGENTS_API_BASE_URL`、`DSAGENTS_PDF_DIR`、`DSAGENTS_REAL_MULTI_PDF_POLL_SECONDS` 等环境变量或 `--base-url`/`--pdf-dir`/`--poll` 等 CLI 参数覆盖 |
| `test_minimax_cache_baseline.py` | 手动真实 MiniMax-M3 prompt-cache 基线脚本（默认不运行）：五分钟内以同一 session 跑两轮、保持 ≥512 token 稳定前缀，打印两轮 `model_usage`（`cache_read_input_tokens` 等）；第二轮 cache read 为 0 时只记录诊断不失败。只通过 HTTP 调 `BASE_URL`（默认 `http://127.0.0.1:8000`，可用 `DSAGENTS_BASE_URL` 覆盖），不导入会开 DB 的 app 代码。非普通回归、非第一阶段发布门禁 |

命名约定：

- backend 测试脚本统一放 `backend/tests/`
- 文件名统一 `test_*.py`
- 普通本地回归脚本（含 `test_api`、`test_harness`、`test_run_ledger`、`test_tools`、`test_workflow_setup` 和两个业务脚本）保留 `run()`，并用 `if __name__ == "__main__": run()` 支持直接运行
- 真实集成脚本（`test_real_image_run`/`test_real_multi_pdf_run`/`test_minimax_cache_baseline`）走 `main()`（argparse，带 `--base-url`/`--image`/`--pdf-dir`/`--poll` 等参数）或 `run()` 直连，`python -m tests.test_real_xxx` / `python -m tests.test_minimax_cache_baseline` 直接执行真实调用；非普通回归、非第一阶段发布门禁
- `test_support.py` 只放共享替身/辅助函数，不作为独立验证入口

## 3. 当前覆盖点（已确认）

| 模块 | 覆盖事实 |
| --- | --- |
| `test_tools.py` | 保留全部 MinerU/解压断言；`default_tool_catalog()` 另断言八个 Philips/Tecan 业务工具，总数为十个 |
| `test_run_ledger.py` | `AgentResources` 创建 3 个 sqlite db 并包含 `/skills/` 路由；其余 run 快照/事件/状态机、spill、恢复与时间戳迁移断言不变 |
| `test_harness.py` | 原有事件/中间件/消息断言不变；FakeBrain 的 subagent chunk 现在也带 `usage_metadata`，事件序列在 subagent 处多一个 `model_usage`（subagent）、终态文本 chunk 多一个 `model_usage`（main），但 subagent 文本 token 仍不外泄，证明 `lc_agent_name` 过滤只挡文本不挡 usage |
| `test_api.py` | `POST /upload` 支持单文件、多文件、混合文件；`POST /files` 返回 `404`；`POST /runs` 只接受 `messages[] + content blocks`，旧 `message` 请求失败；上传后引用 artifact 路径的 run 可轮询到 `succeeded`；`after_event_id` 只裁剪 `events[]`，不影响 `latest_content_event`，也不影响顶层 `usage`；成功 run 的最终 `latest_content_event.type == "assistant_message"` 且 payload 含最终 `thinking`；同 `session_id` 并发返回 `409`；失败 run 后同 session 可续跑；未知 run 返回 `404`；app 启动时会清理遗留 `queued/running` run；`usage` 块覆盖 succeeded/failed、cache_hit_rate、长短上下文 tier 计价、节省公式、不可计价时金额 `null`、零输入 hit_rate `null` |
| `test_workflow_setup.py` | Skill 路由可 `ls/read`；四个 subagent 各只有一个业务工具且文件写入被拒；A/B task description 完全相同并位于同一 AIMessage |
| 两个业务测试 | 每个覆盖 A/B 一致、一路失败、冲突、双失败、C 单独、两票多数、无多数裁决、必需字段缺失和旧合同拒绝；Excel 断言 sheet、关键单元格与数值类型 |

## 4. 测试替身与策略（已确认）

- `FakeBrainFactory.create(**_)` → `FakeBrain`：证明 Brain 可替换。
- `FakeBrain.stream(...)`：断言 Brain 侧收到的是 `messages[]` 与 text blocks；按 `thread_id` 维护最小 history，以验证失败 run 后同 thread 续跑不回滚。
- `StreamControl`：用 `"hold"` 输入制造并发冲突窗口。
- 网络/环境替身：保留 MinerU stream/requests patch；业务测试 patch `workflow_artifacts.artifacts_root`，Philips 用空环境验证 Oracle fallback。

## 5. 验证流程（按变更类型）

- **仅文档变更**：`git diff --check`（检查空白/行尾错误）。
- **代码变更**（`backend/*.py` 或 `backend/tests/*.py`）：按影响范围跑对应 `cd backend && python -m tests.test_xxx`。
- **HTTP 行为变更**：默认已被 `backend/tests/test_api.py` 覆盖，无需手动起服务。
- **Skills/Subagents 或业务工具变更**：运行 `test_workflow_setup`、对应 Philips/Tecan 测试，并复跑 `test_tools` / `test_harness` / `test_api`。

## 6. 当前缺口（待补充）

- **没有 pytest / CI**：当前测试仍是 `assert` 风格脚本，不是 pytest 套件，也没有 CI 自动执行。
- **没有 lint / type-check gate**：未配置 ruff / mypy / black 等门禁（`pyproject.toml` 无对应 `[tool.*]` 段）。
- **普通本地脚本没有真实业务编排覆盖**：配置/投票/Excel 规则可本地验证，但 A/B 模型抽取质量、真实并行 task 行为、MinerU PDF 内容与 Oracle 命中仍需独立的 `test_real_*` 手动验证；现有两个真实脚本不承担 Philips/Tecan 验收。
