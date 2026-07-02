# backend 测试现状（TESTING）

记录 backend 子项目当前的实际测试/验证机制。结论先行：**当前无正式测试套件**，`backend/self_check.py` 是唯一的验证入口。

## 1. 是否存在 tests 目录 / pytest 配置

- 全仓库无 `tests/` 或 `test/` 目录。
- 无 `pytest.ini`、`pyproject.toml`、`setup.cfg`、`tox.ini` 等 pytest 配置文件。
- 无任何 `test_*.py` 或 `*_test.py` 文件。
- 当前代码未确认：CI 配置（`.git/` 之外未见 CI 定义文件）。

因此 **pytest 不是当前的验证路径**——无测试可被 pytest 收集。

## 2. harness.py / session.py 中是否有断言 / doctest / 示例调用

- `backend/harness.py`、`backend/session.py`、`backend/tools.py`、`backend/resources.py`、`backend/hands.py` 中均**无 `assert` 语句、无 doctest（`>>>`）、无示例调用块**。
- 唯一含 `assert` 的是 `backend/self_check.py`。
- 唯一可执行的命令行入口是 `backend/session.py` 的 `main()`（`__main__.py` 转调它）。

## 3. backend/self_check.py 实际做什么

这是一个**自检/冒烟脚本**，用裸 `assert` + 临时目录验证全链路关键路径，不依赖 MinerU 或真实 LLM（用 `_FakeBrain` / `_FakeBrainFactory` 替代，`backend/self_check.py:14-23`）。`main()`（第 26-106 行）验证内容：

1. **工具辅助函数**（第 27-28 行）：`_find_value` 在嵌套 dict 中找 `task_id`；`_extract_markdown` 提取 `md_content`。
2. **资源初始化**（第 30-37 行）：`AgentResources(ResourceConfig(data_dir=...))` 进入后 `session_db`/`store_db`/`checkpoint_db` 均存在，`backend` 非空。
3. **Session 事件流**（第 38-47 行）：`ensure_session` + `emit_event`（`note`/`user_message`/`assistant_message`），`context_window` 仅返回 user/assistant 对且从首个 user 起算。
4. **TraceHands 中间件**（第 49-80 行）：`wrap_model_call`/`wrap_tool_call` 产生 `model_request`/`model_response`/`tool_request`/`tool_response` 事件；并**显式验证错误透传**——注入 `ValueError`/`RuntimeError`，断言被重抛且最后事件为 `model_error`/`tool_error`（第 61-80 行，直接对应 AGENTS.md 的"real error propagation"）。
5. **HarnessRuntime 端到端**（第 82-98 行）：用 `_FakeBrainFactory` 跑两轮 `run_turn`，断言 fake 回声、context 累积、事件序列恰好为 `["user_message","assistant_message","user_message","assistant_message"]`。
6. **大 payload 溢出落盘**（第 100-104 行）：`SqliteSessionStore(max_inline_bytes=10)` 发送 100 字节 payload，断言回读内容正确且 `artifacts/session-events/*.json` 文件存在。

全部通过则打印 `self-check passed`（第 106 行）。该脚本同时充当**回归断言集合**与**集成冒烟测试**。

## 4. 如何运行 self_check

`self_check.py` 末尾有 `if __name__ == "__main__": main()`（第 109-110 行），可作模块执行。因脚本使用相对导入（`from .hands import ...` 等，第 7-11 行），必须以包模块方式运行：

```
python -m backend.self_check
```

（直接 `python backend/self_check.py` 会因相对导入失败。）

## 5. 运行真实单次会话（CLI 入口）

`backend/__main__.py` 仅 `from .session import main; main()`，因此：

```
python -m backend "<你的消息>" --session-id <可选>
```

此入口依赖真实 DeepAgents + 模型（`backend/harness.py:41` 默认 `os.environ.get("DSAGENTS_MODEL", "openai:gpt-5.5")`），不是离线自检。

## 6. 开发安装

```
pip install -r requirements.txt
```

依赖见 `requirements.txt`：`deepagents>=0.6.12`、`langchain>=1.3.11`、`langgraph>=1.2.6`、`langgraph-checkpoint-sqlite>=3.1.0`、`requests>=2.34.0`。

## 7. 当前验证入口总结

- **离线验证**：`python -m backend.self_check`（推荐，无需网络/LLM）。
- **正式测试套件**：当前代码未确认存在；pytest 无可收集目标。
