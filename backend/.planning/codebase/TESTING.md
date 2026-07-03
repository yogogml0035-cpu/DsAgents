# 测试与验证 (TESTING)

> 事实来源：backend/ 源码（2026-07-03 刷新）

本文档如实反映首个里程碑早期的测试现状：**无正式自动化测试套件、无 CI、无类型/lint 工具**。唯一产品级验证手段是 `self_check.py` 端到端自检。

## 1. 测试框架与依赖现状

- `backend/pyproject.toml` 的 `[project.dependencies]` **不含任何测试/lint/类型工具**：无 `pytest`、`unittest`(显式依赖)、`ruff`、`black`、`mypy`、`coverage`。无 `[project.optional-dependencies]`/`[dependency-groups]` 声明 dev 工具。→ 验证：`backend/pyproject.toml`。
- 仓库内无 `pytest.ini`/`setup.cfg`/`tox.ini`，`pyproject.toml` 无 `[tool.pytest]`/`[tool.ruff]`。
- `self_check.py` 引入的"测试相关"仅为标准库 `unittest.mock.patch`、`types.SimpleNamespace`、`fastapi.testclient.TestClient`（`self_check.py:11-12/20`），**不走任何测试 runner**。
- `backend/tests/` 仅一个文件 `test_stream_typing.py`：**它不是 pytest 用例**，而是一个**手动 SSE 打字机客户端**（`argparse` + `requests`，默认连 `http://127.0.0.1:8500/sessions/messages/stream`，逐字打印 `text_delta`）。无 `assert`、无 test 函数、无 `conftest.py`。→ 验证：`tests/test_stream_typing.py:11/14-44`。

结论：**当前没有正式测试；自检脚本用裸 `assert` 实现，`tests/` 目录名具误导性，内含的是辅助工具而非测试。**

## 2. self_check.py 的角色

`backend/self_check.py`（465 行）是端到端冒烟自检，覆盖五大边界的核心不变量。通过 `python backend/self_check.py` 运行（`if __name__ == "__main__": main()`）。

### 五大边界自检（`main()`，均裸 `assert`）

| 边界 | 自检内容 | 验证代码 |
|------|---------|---------|
| **Session（append-only）** | 事件 round-trip、`emit_event` 只增不改 | `session.py:177-198`；自检 `self_check.py:138-148/217-226` |
| **Session（派生视图）** | `context_window` 从 `user_message`/`assistant_message` 投影 | `session.py:200-213`；自检 `self_check.py:144-147` |
| **Hands（错误透传）** | model/tool 异常 emit `*_error` 后必 `raise` | `hands.py:42-46/66-71`；自检 `self_check.py:170-189` |
| **Harness（Brain 可替换）** | `_FakeBrainFactory` 注入，`run_turn` 多轮事件序列 | `harness.py:104-116`；自检 `self_check.py:191-207` |
| **Tools（fail-fast）** | 缺 `MINERU_BASE_URL` 抛 `RuntimeError` | `tools.py:84-88`；自检 `self_check.py:121-129` |

### 关键断言的不变量

- **append-only**：`emit_event`/`emit_run_event` 返回的事件 payload 与读回一致，超大 payload 外溢后仍可 round-trip 读回（`self_check.py:217-226`，`max_inline_bytes=10` 触发外溢）。
- **错误透传**：`model`/`tool` handler 抛 `ValueError`/`RuntimeError`，`wrap_model_call`/`wrap_tool_call` 必须 re-raise，且末事件为 `model_error`/`tool_error`（`self_check.py:170-189`）。
- **fail-forward 配置**：缺 `MINERU_BASE_URL` 必抛 `RuntimeError("Missing required environment variable: MINERU_BASE_URL")`（`self_check.py:121-129`）。
- **失败 run 不写 `assistant_message`**：`fail` 输入的 session 中 `assistant_message` 计数为 0（`self_check.py:355-365`）。
- **启动清理**：进程（TestClient）重启后遗留 `queued`/`running` 的 run 被标记 `failed` + `INTERRUPTED_RUN_ERROR`（`self_check.py:368-389`、`api.py:40`）。
- **409 并发**：后台 run 持有 session 锁时，同步 POST 返回 409（`self_check.py:294-308`）。
- **超大 payload 外溢**：`session-events/*.json` 与 `run-events/*.json` 文件存在（`self_check.py:225-226`）。

### `_FakeBrain` / `_FakeBrainFactory`（证明 Brain 可替换）

- `_FakeBrain`（`self_check.py:37`）：断言首条消息是 `RemoveMessage(REMOVE_ALL_MESSAGES)`（对应 harness 的 `_reset_messages`，`harness.py:283-284`），返回 `echo: {原文}`，绕开真实 LLM；`stream()` 按 `messages/custom/values` 序列产出 thinking + 文本 chunk，`text=="fail"` 时抛 `RuntimeError`，`text=="hold"` 时用 `_StreamControl` 同步阻塞（用于并发锁测试）。
- `_FakeBrainFactory`（`self_check.py:90`）：实现 `BrainFactory.create(...)`，返回 `_FakeBrain`，注入 `HarnessRuntime`——**证明 `Brain`/`BrainFactory` Protocol 的可替换性**。
- 用 `patch.dict(os.environ, {}, clear=True)` 保证 env 纯净（`self_check.py:105/121`）。

### 输出

- 成功：末行打印 `self-check passed`（`self_check.py:425`），退出码 0。
- 失败：任一 `assert` 抛 `AssertionError`（或被测代码抛其它异常），非零退出。

## 3. 运行命令

`backend/` 是扁平顶层模块（非包），**不能用** `python -m backend.self_check`（无 `backend` 包）。可用入口：

```bash
# 自检（推荐，无需真实 LLM / provider 可达）
python backend/self_check.py
# 或：cd backend && uv run python self_check.py

# 单次会话冒烟（需真实 MiniMax key 与网络，发 LLM 请求）
python backend/session.py

# HTTP 服务（需真实 MiniMax key 与网络）
cd backend && uv run uvicorn api:app --host 0.0.0.0 --port 8000
# 或仓库根：scripts\start-backend.bat（端口 8500）
```

## 4. scripts/ 辅助脚本（辅助，非产品）

- `scripts/start-backend.bat`：切到 `backend/`，执行 `uv run uvicorn api:app --host 0.0.0.0 --port 8500`。→ 验证：`scripts/start-backend.bat`。
- `backend/tests/test_stream_typing.py`：手动 SSE 打字机客户端（连本地 8500 端口），属辅助工具，**非自动化测试**。→ 验证：见 §1。
- `scripts/ralph/`：独立的 dashboard/ralph 子项目，与 backend 无关，不属于本子项目测试范畴。

> 辅助脚本不是产品代码，变更不影响 backend 不变量；它们的存在不代表测试覆盖。

## 5. 覆盖缺口（诚实评估）

| 缺口 | 现状 | 影响 |
|------|------|------|
| **无单元测试** | 业务模块无 `test_*.py`；`tests/` 唯一文件是手动工具 | 函数级失败无法细分定位 |
| **无 CI** | 无 GitHub Actions / 预提交钩子配置 | 回归靠本地手跑 `self_check` |
| **无 provider mock** | `parse_document` 成功路径（`_submit_mineru_task`/`_wait_for_mineru_result`）仅靠自检里 `patch` 桩覆盖一次（`self_check.py:405-408`），无独立 mock 套件 | provider 协议变更难及时发现 |
| **依赖真实 env 才能 run** | `self_check` 通过 `patch.dict` 注入 env；`run_session`/`api.py` 需真实 `MINIMAX_*` + 网络 | 集成验证需密钥与可达端点 |
| **HTTP 端点无集成测试** | `self_check` 用 `TestClient` 覆盖主路径，但无独立 HTTP 集成套件；边界（如 404、并发释放、上传异常）无专项用例 | 传输层回归靠自检一次性走查 |

> 当前里程碑以"最小可运行 demo"为目标，测试薄弱属预期。上述为后续增量方向，不要求立即实施。

## 6. 验证入口清单（每个不变量如何在 self_check 里复核）

| 不变量 | 复核位置 |
|--------|---------|
| append-only 事件不可改 | `self_check.py:138-148`（emit→get round-trip） |
| `context_window` 是派生视图 | `self_check.py:144-147`（投影非存储） |
| 错误透传（model） | `self_check.py:170-179` |
| 错误透传（tool） | `self_check.py:180-189` |
| fail-fast 缺 env | `self_check.py:121-129` |
| 失败 run 不写 assistant_message | `self_check.py:355-365` |
| 启动清理 fail_incomplete_runs | `self_check.py:209-216/368-389` |
| 409 并发冲突锁 | `self_check.py:294-308` |
| 超大 payload 外溢 JSON 指针 | `self_check.py:217-226` |
| Brain Protocol 可替换 | `self_check.py:191-207`（`_FakeBrainFactory`） |
