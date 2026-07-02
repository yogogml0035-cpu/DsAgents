# 测试与验证 (TESTING)

> 事实来源：backend/ 源码、backend/pyproject.toml + uv.lock（2026-07-02 生成；原仓库根 requirements.txt 已废弃）

本文档如实反映“首个里程碑早期”的测试现状。当前**没有正式的自动化测试套件**，唯一的验证手段是 `self_check.py` 自检脚本。

## 1. 测试框架与依赖

- **backend/pyproject.toml 的 `[project.dependencies]` 不包含任何测试/lint/类型工具**：无 `pytest`、`unittest`(显式依赖)、`ruff`、`black`、`mypy`、`coverage` 等（亦无 `[project.optional-dependencies]` / `[dependency-groups]` 声明 dev 工具）。
- 仓库内无 `pytest.ini` / `setup.cfg` / `tox.ini` 等测试配置文件。`backend/pyproject.toml` 仅定义运行时依赖与打包元数据，未含 `[tool.pytest]` / `[tool.ruff]` 等测试/lint 配置节。
- `find` 查找 `test_*.py` / `*_test.py` / `conftest.py` 在 backend 业务源码中**零结果**（`.venv` 内的三方库测试不计）。
- 唯一引入的“测试相关”导入是标准库 `unittest.mock.patch`（`self_check.py:7`）和 `types.SimpleNamespace`（`self_check.py:6`），用于构造假对象，不走任何测试框架 runner。

结论：**当前没有正式测试，自检脚本用裸 `assert` 实现，不依赖 pytest。**

## 2. self_check.py 的角色

`backend/self_check.py` 是一个**端到端冒烟自检脚本**（不是 pytest 测试），覆盖了除 `tools.parse_document_with_mineru` 真实网络调用外的几乎所有核心逻辑。它通过 `python -m backend.self_check` 运行（`if __name__ == "__main__": main()`，`self_check.py:126`）。

### 它验证什么（`main()` 函数，`self_check.py:30-123`）

| 验证项 | 覆盖的代码 | 行号 |
|--------|-----------|------|
| `_find_value` 递归取值 | `tools._find_value` | `self_check.py:31` |
| `_extract_markdown` 提取 md | `tools._extract_markdown` | `self_check.py:32` |
| `.env` → 环境变量映射（`MINIMAX_*` → `OPENAI_*`）与默认模型拼装 | `harness.DeepAgentsBrainFactory.__init__` | `self_check.py:36-46` |
| `AgentResources` 初始化：建库、建目录、CompositeBackend 就绪 | `resources.AgentResources` | `self_check.py:48-53` |
| Session 事件 append + 读取 | `session.SqliteSessionStore.emit_event/get_events` | `self_check.py:55-58` |
| 上下文窗口派生（仅取 user/assistant 对话） | `session.context_window` / `_event_to_message` | `self_check.py:59-64` |
| TraceMiddleware 记录 model_request/response、tool_request/response | `hands.TraceMiddleware` | `self_check.py:66-77` |
| **错误透传契约**：model/tool 异常必须被 re-raise 且记为 model_error/tool_error | `hands.TraceMiddleware` + AGENTS.md 原则 | `self_check.py:78-97` |
| `HarnessRuntime.run_turn` 单轮 + 多轮（用 `_FakeBrain` 替身）+ 事件序列正确 | `harness.HarnessRuntime.run_turn` | `self_check.py:99-115` |
| 大 payload 超阈值时落盘为 artifact 文件 | `session.SqliteSessionStore`（`max_inline_bytes`） | `self_check.py:117-121` |

### 它如何替身真实 Brain

- `_FakeBrain`（`self_check.py:18`）：断言首条消息是 `RemoveMessage(id=REMOVE_ALL_MESSAGES)`（对应 harness 的 `_reset_messages`），返回 `echo: {原文}`，绕开真实 LLM 调用。
- `_FakeBrainFactory`（`self_check.py:25`）：返回 `_FakeBrain`，注入 `HarnessRuntime`（`self_check.py:99-104`）。
- 用 `patch.dict(os.environ, {}, clear=True)`（`self_check.py:35`）保证环境变量测试的纯净。

### 输出

- 成功：`print("self-check passed")`（`self_check.py:123`），退出码 0。
- 失败：任一 `assert` 抛 `AssertionError`（或被测代码抛其它异常），非零退出。

## 3. 运行命令

```bash
# 自检（推荐，不需要真实 LLM / MinerU 可达）
python -m backend.self_check

# 单次会话冒烟（需真实 MiniMax key 与网络，会发 LLM 请求）
python -m backend.session
#   注意：session.py:218-222 的 main() 存在未定义变量 bug
#   （引用了 args.message/args.session_id，但未构造 argparse），
#   直接运行会 NameError。需确认是否应修复或改用 run_session()。
```

`python -m backend.session` 会执行 `if __name__ == "__main__": main()`（`session.py:252-253`）。

> 历史上可能存在的 `python -m backend.__main__` **不适用**：仓库内**没有 `backend/__main__.py`** 文件（已确认）。当前可用的 `-m` 入口为 `backend.self_check` 与 `backend.session`。

## 4. 验证入口（如何确认 backend 可运行）

1. **依赖就位**：在仓库根执行 `cd backend && uv sync`，由 uv 按 `backend/pyproject.toml` + `backend/uv.lock` 创建/同步 `backend/.venv`。
2. **纯逻辑自检（无需外部服务）**：
   ```bash
   python -m backend.self_check
   ```
   看到末行 `self-check passed` 即表示 Session / Harness / Hands / Resources / Tools（除真实网络）链路正常。
3. **带真实模型的集成验证**：调用编程入口而非有 bug 的 `main()`：
   ```python
   from backend import run_session
   result = run_session("你好")
   print(result["messages"][-1].content)
   ```
   （`run_session` 在 `session.py:209`，会创建临时 `AgentResources` 并跑一轮 `run_turn`。）
   前提：`backend/.env` 中配置了有效的 `MINIMAX_API_KEY`。

> 没有独立的“健康检查”端点——本项目是库/运行时，无 HTTP server。验证以 `self_check` + `run_session` 为主。

## 5. 当前测试覆盖缺口（初步建议，不夸大）

**已覆盖（self_check.py）：** `session`、`harness`、`hands`、`resources` 的纯逻辑路径，以及 `tools` 中 `_find_value` / `_extract_markdown` 纯函数。

**未覆盖 / 缺口：**

| 缺口 | 说明 | 建议 |
|------|------|------|
| MinerU 真实网络交互 | `tools.parse_document_with_mineru` / `_submit_task` / `_wait_for_result` 未测试（需可达服务） | 引入 `requests` mock（如 `responses`/`unittest.mock`）覆盖成功/失败状态/超时分支 |
| `python -m backend.session` CLI | `main()` 存在 `args` 未定义 bug，无法运行 | 修复 `main()`（补 `argparse`）后纳入自检 |
| 缺乏正式测试框架 | 无 pytest、无 fixture、无 CI | 可选：引入 `pytest` + `tests/` 目录，把 `self_check.py` 的断言拆为 pytest 用例以便细分失败定位 |
| 无类型检查 / lint | 未配置 mypy/ruff | 可选：加 `mypy`/`ruff` 到 dev 依赖，CI 中跑（代码已全量类型注解，mypy 成本低） |
| 多 session / 并发 | 仅测单 session 顺序轮次 | 后续若引入并发或 async，需补 |
| 回放/重放事件 | append-only 事件可重放，但无对应测试 | 可补“从事件流重建上下文”的测试 |

> 当前里程碑以“最小可运行 demo”为目标，测试薄弱属预期。上述建议为后续增量，不要求立即实施。
