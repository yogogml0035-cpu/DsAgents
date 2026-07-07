# TESTING

> backend 子项目的测试与验证策略。事实来源 = `backend/self_check.py` 源码 + `backend/pyproject.toml`。

## 1. 主要验证手段：self_check（已确认）

当前**没有**正式 pytest 套件；唯一且主要的验证入口是：

```powershell
python backend/self_check.py
```

- 用 `_FakeBrainFactory` / `_FakeBrain` 替换真实 Brain（**不**打真实 LLM / 不打真实 MinerU）。
- `main()` 在所有检查通过后打印 `self-check passed`；任一 `assert` 失败则抛异常退出非零。**这是通过判定的字符串契约。**
- 用 `tempfile.TemporaryDirectory` 做隔离，不污染 `backend/data/`。
- 用 `unittest.mock.patch` / `patch.dict(os.environ, ..., clear=True)` 替身网络与环境。

### 1.1 self_check 覆盖点（已确认，对应源码 `_check_*`）

| 检查函数 | 覆盖事实 |
| --- | --- |
| `main()` 前置断言 | `_thinking_delta`、`_find_value`、`_extract_markdown`、`default_tool_catalog().handlers[0].__name__ == "parse_document"` |
| `_check_model_env_loading` | `DeepAgentsBrainFactory` 从 `MINIMAX_*` 构造 `ChatAnthropic`，`thinking={"type":"adaptive"}` |
| `_check_parse_document_env_guard` | `parse_document` 缺 `MINERU_BASE_URL` 时 fail-fast（`RuntimeError`） |
| `_check_resources_and_ledger` | `AgentResources` 创建 3 个 sqlite db；`SqliteRunLedger` 快照/事件/状态机；`get_latest_content_event()` 在仅 `status`、`values→status`、多非 `status`、大 payload artifact 场景下的返回；`fail_incomplete_runs` 启动恢复；大 payload（`max_inline_bytes=10`）外溢到 `artifacts/run-events/*.json` |
| `_check_tool_status_middleware` | `ToolStatusMiddleware` 成功发 `started→completed`，异常发 `started→error` 并透传异常 |
| `_check_harness` | `execute_run` 事件序列 = `status/values/thinking/text_delta/tool_status/text_delta/values/status`；同 thread 续跑 reply 计数递增；thinking 事件 `raw["type"]=="messages"` |
| `_check_api` | `POST /runs` 返回 `queued`；轮询到 `succeeded`；`GET /runs/{run_id}` 默认返回 `latest_content_event`；`after_event_id` 只裁剪 `events[]`、不影响 `latest_content_event`；同 `session_id` 并发返回 `409`；`/files` 上传与 `/artifacts/uploads/` 解析；失败 run `failed`+`error`；同 session 续跑成功；未知 run `404` |
| `_check_startup_recovery` | app lifespan 启动时把遗留 `queued/running` run 标记 `failed`（`error == INTERRUPTED_RUN_ERROR`）；仅有 `status` 事件时 `latest_content_event is None`；harness 只创建一次 |
| `_check_virtual_artifacts` | `parse_document` 虚拟路径 `/artifacts/...` 解析与 `..` 路径穿越拒绝（`ValueError`） |

### 1.2 self_check 测试替身（已确认）

- `_FakeBrainFactory.create(**_)` → `_FakeBrain`：证明 Brain 可替换。
- `_FakeBrain.stream`：断言 `len(payload["messages"])==1` 且无 `id`（证明 payload 只含当前 user message）；按 `thread_id` 维护最小 history（list）以验证失败 run 后同 thread 续跑不回滚。
- 特殊输入：`"fail"` → `raise RuntimeError("planned failure")`；`"hold"` → 用 `_StreamControl` 阻塞，制造并发冲突窗口。
- 网络/环境替身：`patch("hands.get_stream_writer")`、`patch("tools._artifacts_root")`、`patch("tools._submit_mineru_task")`、`patch("tools._wait_for_mineru_result")`、`patch.dict(os.environ, ..., clear=True)`。

## 2. 测试目录现状（已确认）

- 当前没有 `backend/tests/` 测试源码目录。
- **没有** `conftest.py` / `pytest.ini` / `tox.ini`（仓库根与 `backend/` 下均无）。
- `pytest` **未**声明在 `pyproject.toml` 依赖中（dev 依赖也未声明）。

## 3. 验证流程（按变更类型）

- **仅文档变更**：`git diff --check`（检查空白/行尾错误），无需跑 self_check。
- **代码变更**（`backend/*.py`）：在改完后跑 `python backend/self_check.py`，必须看到结尾的 `self-check passed`。
  - 根级 `AGENTS.md` 已把它列为 backend 代码变更的验证入口。
- **HTTP 行为变更**：self_check 的 `_check_api` / `_check_startup_recovery` 已用 `fastapi.testclient.TestClient` 覆盖，无需手动起服务。

## 4. 当前缺口（待补充）

- **没有正式单元测试目录/套件**：验证完全集中在 `self_check.py` 这一个脚本。
- **没有 CI**：仓库内未见 CI 配置；`self-check passed` 目前靠手工/agent 触发判定。
- **没有 lint / type-check gate**：未配置 ruff / mypy / black 等门禁（pyproject.toml 无 `[tool.ruff]` 等段落）。
- **没有真实 provider 集成测试**：self_check 全程用 `_FakeBrain` 与 patch 后的 MinerU；真实 `MINIMAX_*` / `MINERU_*` 调用没有自动化覆盖（需确认是否有手动冒烟流程）。
