# 测试与验证 (TESTING)

> 事实来源：backend/ 源码、backend/pyproject.toml + uv.lock（2026-07-03 本轮刷新：新增 FastAPI lifespan / run API / 并发锁 / 启动清理自检）

本文档如实反映"首个里程碑早期"的测试现状。当前**没有正式的自动化测试套件**，唯一的验证手段是 `self_check.py` 自检脚本。

## 1. 测试框架与依赖

- **backend/pyproject.toml 的 `[project.dependencies]` 不包含任何测试/lint/类型工具**：无 `pytest`、`unittest`(显式依赖)、`ruff`、`black`、`mypy`、`coverage` 等。亦无 `[project.optional-dependencies]` / `[dependency-groups]` 声明 dev 工具。
- 仓库内无 `pytest.ini` / `setup.cfg` / `tox.ini` 等测试配置文件。`backend/pyproject.toml` 仅定义运行时依赖与打包元数据，未含 `[tool.pytest]` / `[tool.ruff]` 等测试/lint 配置节。
- backend 业务源码中无 `test_*.py` / `*_test.py` / `conftest.py`。
- 唯一引入的"测试相关"导入是标准库 `unittest.mock.patch`（`self_check.py`）和 `types.SimpleNamespace`（`self_check.py`），用于构造假对象，不走任何测试框架 runner。

结论：**当前没有正式测试，自检脚本用裸 `assert` 实现，不依赖 pytest。**

## 2. self_check.py 的角色

`backend/self_check.py` 是一个**端到端冒烟自检脚本**（不是 pytest 测试），覆盖了除 `tools.parse_document` 真实网络成功路径外的几乎所有核心逻辑。它通过 `python backend/self_check.py`（仓库根，已激活环境）或 `cd backend && uv run python self_check.py` 运行（`if __name__ == "__main__": main()`）。

### 它验证什么（`main()` 函数，均通过裸 `assert`）

| 验证项 | 覆盖的代码 |
|--------|-----------|
| `_find_value` 递归取值 / `_extract_markdown` 提取 md | `tools._find_value` / `_extract_markdown` |
| brain factory 模型接线：注入 `MINIMAX_API_KEY` / `MINIMAX_BASE_URL=https://minimax.example/anthropic` / `MINIMAX_MODEL=test-minimax`，断言产物为 `ChatAnthropic`、`model == "test-minimax"` 且 `thinking == {"type": "adaptive"}` | `harness.DeepAgentsBrainFactory.__init__` |
| 缺失 `MINERU_BASE_URL` 时 fail-fast（抛 `RuntimeError`） | `tools.parse_document` / `_required_env` |
| `AgentResources` 初始化：建库、建目录、CompositeBackend 就绪 | `resources.AgentResources` |
| Session 事件 append + 读取 round-trip | `session.SqliteSessionStore.emit_event/get_events` |
| 上下文窗口派生（20 上限裁剪、剔除 leading 非 user 消息） | `session.context_window` |
| TraceMiddleware 记录 model/tool trace，存在 `run_id` 时同步写 run 事件，且异常被 re-raise | `hands.TraceMiddleware` |
| `HarnessRuntime.run_turn` 单轮 + 多轮事件序列 `["user_message","assistant_message","user_message","assistant_message"]` | `harness.HarnessRuntime.run_turn` |
| `HarnessRuntime.execute_run` 统一 streaming 路径：`messages/custom/values`→`run_events`→成功/失败终态 | `harness.HarnessRuntime.execute_run` |
| HTTP lifespan 共享资源：多个请求复用同一个 `HarnessRuntime`，启动时清理遗留 queued/running run | `api.py::create_app(lifespan=...)` |
| HTTP 阻塞接口：自动生成 `session_id` / `run_id`、返回 `status` / `reply|error` | `api.py::POST /sessions/messages` |
| SSE 接口：首个 `session` 事件携带 `session_id/run_id/status`，后续统一 `run_event`，末尾 `done` | `api.py::POST /sessions/messages/stream` |
| 后台 run：提交即返回 queued，随后可经 `GET /runs/{run_id}` 轮询 | `api.py::POST /sessions/messages/runs` |
| `GET /runs/{run_id}` 全量/增量 cursor 查询；`GET /sessions/{session_id}/runs` 列表 | `api.py::GET /runs/{run_id}` / `GET /sessions/{session_id}/runs` |
| 同 session 并发锁：运行中第二个 Agent POST 返回 409，但上传不受影响 | `api.py::_acquire_session_run` / `_release_session_run` |
| 失败语义：failed run 不写 `assistant_message`，但会保留 run 错误与过程事件 | `harness.HarnessRuntime.execute_run` + `api.py` |
| 上传接口：basename 清理、保存到 `artifacts/uploads/`、返回 `/artifacts/uploads/...` | `api.py::POST /files` |
| `/artifacts/...` 虚拟路径映射与 `..` 逃逸拒绝 | `tools.parse_document` / `_resolve_document_path` |
| 超大 payload 落盘 round-trip（`max_inline_bytes=10` → `artifacts/session-events/*.json` / `artifacts/run-events/*.json`） | `session.SqliteSessionStore`（`max_inline_bytes`） |

### 它如何替身真实 Brain

- `_FakeBrain`：断言首条消息是 `RemoveMessage(REMOVE_ALL_MESSAGES)`（对应 harness 的 `_reset_messages`），返回 `echo: {原文}`，绕开真实 LLM 调用。
- `_FakeBrainFactory`：返回 `_FakeBrain`，注入 `HarnessRuntime`。
- 用 `patch.dict(os.environ, {}, clear=True)` 保证环境变量测试的纯净（env-purity）。

### 输出

- 成功：`print("self-check passed")`，退出码 0。
- 失败：任一 `assert` 抛 `AssertionError`（或被测代码抛其它异常），非零退出。

## 3. 运行命令

`backend/` 是扁平顶层模块（不是包），所以**不能用** `python -m backend.self_check`（没有 `backend` 包）。可用入口：

```bash
# 自检（推荐，不需要真实 LLM / 当前文档解析 provider 可达）
python backend/self_check.py
# 或：cd backend && uv run python self_check.py

# 单次会话冒烟（需真实 MiniMax key 与网络，会发 LLM 请求）
python backend/session.py
# 或：cd backend && python -m session

# HTTP 服务（需真实 MiniMax key 与网络；run API + 上传端点）
cd backend && uv run uvicorn api:app --host 0.0.0.0 --port 8000
```

`session.main()`（`session.py`）硬编码 `message = "你是谁"` + 随机 `session_id`，调用 `run_session` 后打印最后一条消息内容。

> 没有 `python -m backend`（无 `__main__.py`）；也没有 `python -m backend.self_check` / `python -m backend.session`（无 `backend` 包）。

## 4. 验证入口（如何确认 backend 可运行）

1. **依赖就位**：在仓库根执行 `cd backend && uv sync`，由 uv 按 `backend/pyproject.toml` + `backend/uv.lock` 创建/同步 `backend/.venv`。
2. **纯逻辑自检（无需外部服务）**：
   ```bash
   python backend/self_check.py
   ```
  看到末行 `self-check passed` 即表示 Session / Harness / Hands / Resources / Tools / HTTP run / SSE run / 后台 run / upload（除真实外部网络）链路正常。
3. **带真实模型的集成验证**：调用编程入口而非冒烟 `main()`：
   ```python
   # 需在 backend/ 目录下，或把 backend/ 加入 PYTHONPATH
   from session import run_session
   result = run_session("你好")
   print(result["messages"][-1].content)
   ```
   前提：`backend/.env` 中配置了有效的 `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` / `MINIMAX_MODEL`。

> 当前没有独立的 `/health` 端点。验证以 `self_check`、`run_session` 与 `api.py` 的本地 `TestClient` 契约检查为主。

## 5. 当前测试覆盖缺口（初步建议，不夸大）

**已覆盖（self_check.py）：** `session`、`harness`、`hands`、`resources` 的纯逻辑路径，以及 `tools` 中 `_find_value` / `_extract_markdown` 纯函数。

**未覆盖 / 缺口：**

| 缺口 | 说明 | 建议 |
|------|------|------|
| 当前 provider 的真实网络交互 | `tools.parse_document` / `_submit_mineru_task` / `_wait_for_mineru_result` 的成功路径未测试（需 mock `requests`） | 引入 `requests` mock（如 `responses`/`unittest.mock`）覆盖成功/失败状态/超时分支 |
| 缺乏正式测试框架 | 无 pytest、无 fixture、无 CI | 可选：引入 `pytest` + `tests/` 目录，把 `self_check.py` 的断言拆为 pytest 用例以便细分失败定位 |
| 无类型检查 / lint | 未配置 mypy/ruff | 可选：加 `mypy`/`ruff` 到 dev 依赖，CI 中跑（代码已全量类型注解，mypy 成本低） |
| 跨进程恢复 | 当前只测“进程重启后 queued/running 被启动清理追加 failed”，不做后台线程恢复 | 若未来引入真正队列/多进程 worker，再补恢复/幂等等验证 |
| 回放/重放事件 | append-only 事件可重放，但无对应测试 | 可补"从事件流重建上下文"的测试 |

> 当前里程碑以"最小可运行 demo"为目标，测试薄弱属预期。上述建议为后续增量，不要求立即实施。
