# 命令与入口

## 安装

```powershell
cd backend
uv sync
```

## 自检

```powershell
python backend/self_check.py
```

## 启动 HTTP

```powershell
cd backend
uv run uvicorn api:app --host 0.0.0.0 --port 8000
```

## 调用 HTTP

```powershell
curl -X POST http://127.0.0.1:8000/runs ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"帮我解析 /artifacts/uploads/demo.pdf\",\"session_id\":null}"
```

```powershell
curl "http://127.0.0.1:8000/runs/<run_id>"
```

## 程序内调用

仓库不再提供 `from session import run_session`。

如需程序内调用，用：

```python
from resources import AgentResources, ResourceConfig
from harness import create_harness
```

然后显式创建 run、执行 `execute_run(...)`、再从 `resources.runs.get_run(run_id)` 读取结果。
