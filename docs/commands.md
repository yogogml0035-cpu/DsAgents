# 命令与入口

> 根级 AGENTS.md 的详情文档之一。

`backend/` 不是常规包（无 `__init__.py` / `__main__.py`），故**没有** `python -m backend.*`；脚本所在目录会自动加入 `sys.path`，直接运行即可。

## 安装依赖

在 `backend/` 下，用 uv 同步：

```bash
cd backend && uv sync
```

## 端到端自检

FakeBrain，验证核心原则与约束，结尾打印 `self-check passed`：

```bash
python backend/self_check.py
# 或：cd backend && python -m self_check
```

## 启动 HTTP 服务

Windows 下可直接运行：

```bat
scripts\start-backend.bat
```

等价命令：

```bash
cd backend && uv run uvicorn api:app --host 0.0.0.0 --port 8000
```

## 通过 Python API 调用

无独立 CLI 入口，需在 `backend/` 下或加入 `PYTHONPATH`：

```bash
cd backend && python -c "from session import run_session; run_session('帮我解析 xxx.pdf')"
```

> 导入入口是 `from session import run_session`（扁平顶层，无 `backend.` 前缀）。从仓库根直接 `import session` 会失败，需先把 `backend/` 加入 `PYTHONPATH` 或在该目录下运行。
