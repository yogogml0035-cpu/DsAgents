# 命令与入口

## 安装

```powershell
cd backend
uv sync
```

## 测试脚本

```powershell
cd backend
python -m tests.test_api
```

backend 测试统一放在 `backend/tests/test_*.py`。业务工作流改动运行 `test_workflow_setup`、`test_philips_wgq_import`、`test_tecan_import`，并复跑 `test_tools`、`test_harness`、`test_api`；真实外部集成脚本保持独立。

`python -m tests.test_run_ledger` — run ledger 事件存储、时区迁移、状态机的本地 assert 脚本。

真实图片 HTTP / 模型集成脚本默认跳过；只有显式设置 `DSAGENTS_RUN_REAL_IMAGE_TEST=1` 时才触达真实服务。

## 启动 HTTP

```powershell
cd backend
uv run uvicorn api:app --host 0.0.0.0 --port 8500
```

或直接运行根级脚本 `scripts/start-backend.bat`（等价命令，会自动切到 `backend/` 再启动）。

## 调用 HTTP

```powershell
curl -X POST http://127.0.0.1:8500/upload ^
  -F "files=@demo.pdf" ^
  -F "files=@diagram.png"
```

```powershell
curl -X POST http://127.0.0.1:8500/runs ^
  -H "Content-Type: application/json" ^
  -d "{\"session_id\":null,\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"帮我看看这个文件\"},{\"type\":\"artifact\",\"path\":\"/artifacts/uploads/demo.pdf\"}]}]}"
```

```powershell
curl "http://127.0.0.1:8500/runs/<run_id>"
```

轮询增量事件时可加 `?after_event_id=<event_id>`；该游标只影响返回的 `events[]`，不影响 `latest_content_event`。

常见办公文件和任意图片都可以先用 `POST /upload` 保存；能否被解析或理解取决于 DeepAgents `read_file`、`parse_documents`、MinerU 和模型的多模态能力。

## 程序内调用

仓库不再提供 `from session import run_session`。

如需程序内调用，用：

```python
from resources import AgentResources, ResourceConfig
from harness import create_harness
```

然后显式创建 `messages`、写入 run、执行 `execute_run(...)`、再从 `resources.runs.get_run(run_id)` 读取结果。例如：

```python
import json

from resources import AgentResources, ResourceConfig
from harness import create_harness

messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "hello"},
        ],
    }
]

with AgentResources(ResourceConfig()) as resources:
    harness = create_harness(resources)
    run = resources.runs.create_run(
        "run-id",
        "session-id",
        json.dumps(messages, ensure_ascii=False),
    )
    for _event in harness.execute_run(messages, "session-id", run.run_id):
        pass
```
