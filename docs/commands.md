# 命令与入口

## 安装

```powershell
cd backend
uv sync
```

不要用 `pip install -e .` 绕过 `uv.lock`。

## 本地回归测试（FakeBrain / mock，默认门禁）

```powershell
cd backend
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
```

backend 测试统一放在 `backend/tests/test_*.py`，以可执行 assert 脚本方式运行（**非 pytest**）。必须使用 `python -m tests.<name>`，不要直接 `python tests/test_xxx.py`（绝对顶层导入会失败）。

业务工作流改动至少覆盖 `test_workflow_setup`、对应 Philips/Tecan 业务脚本，并复跑 `test_tools`、`test_run_ledger`、`test_harness`、`test_api`。

改 workflow 工具收窄逻辑时务必跑 `python -m tests.test_workflow_setup`：Philips 工具名集合须**含** `parse_documents` / `extract_archives`（及本业务主数据工具），**不含**帝肯业务工具；禁止业务-only allowlist 导致共享 MinerU 工具从模型工具表消失。

改 `StructuredOutputRecovery` / `after_model` / `jump_to` / 空 data 壳纠错时务必跑 `python -m tests.test_harness`，确认重试次数封顶（约 `1 + max_retries` 次模型调用）且耗尽时 `jump_to: "end"`（禁止只返回 `None`）。

`python -m tests.test_run_ledger` — run ledger 事件存储、UTC ISO-8601 毫秒时间戳、状态机与 usage 聚合的本地 assert 脚本。

## 真实外部集成（默认跳过，不进普通门禁）

真实图片 HTTP / 模型集成：

```powershell
$env:DSAGENTS_RUN_REAL_IMAGE_TEST="1"
python -m tests.test_real_image_run
```

真实多 PDF + MinerU：

```powershell
$env:DSAGENTS_RUN_REAL_MULTI_PDF_TEST="1"
python -m tests.test_real_multi_pdf_run --pdf-dir <dir>
```

真实 Philips 外高桥进境 HTTP 验收（DHL、DSV、FedEx、UPS、康捷空；需要已启动服务、样例目录、真实模型/MinerU，Oracle 按部署配置）：

```powershell
$env:DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST="1"
# 可选：$env:DSAGENTS_PHILIPS_WGQ_SAMPLE_ROOT="<进境样例目录>"
python -m tests.test_real_philips_wgq_inbound_recognition

# 只验收 UPS 普货测试用例一的两个 PDF（不上传 Tracking）
# 默认流式打印 thinking / text_delta / tool_execution / tool_progress / assistant_message
# 可选：$env:DSAGENTS_PHILIPS_WGQ_UPS_CASE_DIR="<UPS 样例目录>"
# 可选：$env:DSAGENTS_REAL_PHILIPS_WGQ_POLL_SECONDS="0.2"
python -m tests.test_real_philips_wgq_ups
```

MiniMax prompt-cache 基线（无开关，直接 `-m` 执行；非发布门禁）：

```powershell
python -m tests.test_minimax_cache_baseline
```

## 启动 HTTP

```powershell
cd backend
uv run uvicorn api:app --host 0.0.0.0 --port 8500
```

或运行根级脚本 `scripts/start-backend.ps1`：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1
```

脚本会打开一个独立的 PowerShell 窗口，并在其中自动切到 `backend/` 启动服务。

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

Philips 外高桥进境识别必须省略 `session_id` 并传固定 workflow：

```powershell
curl -X POST http://127.0.0.1:8500/runs ^
  -H "Content-Type: application/json" ^
  -d "{\"workflow\":\"philips_wgq_inbound_recognition\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"artifact\",\"path\":\"/artifacts/uploads/waybill.pdf\"},{\"type\":\"artifact\",\"path\":\"/artifacts/uploads/invoice.pdf\"},{\"type\":\"artifact\",\"path\":\"/artifacts/uploads/tracking.xlsx\"}]}]}"
```

该请求仍立即返回 `queued`。轮询终态后从 GET 顶层 `result` 读取经 schema 校验的业务 JSON；不要解析 `reply`。未知 workflow 或给该 workflow 传非空 `session_id` 均返回 `422`。

```powershell
curl "http://127.0.0.1:8500/runs/<run_id>"
```

轮询增量事件时可加 `?after_event_id=<event_id>`；该游标只影响返回的 `events[]`，不影响 `latest_content_event` 与顶层 `usage`。

取消一个活跃 run：

```powershell
curl -X POST http://127.0.0.1:8500/runs/<run_id>/cancel
```

活跃 run 返回 `202`（协作 drain，最终投影为 `cancelled`）；已 `cancelling`/`cancelled` 返回 `200`；已 `succeeded`/`failed` 返回 `409`；不存在返回 `404`。

常见办公文件和任意图片都可以先用 `POST /upload` 保存；能否被解析或理解取决于 DeepAgents `read_file`、`parse_documents`、MinerU 和模型的多模态能力。

## 程序内调用

仓库不再提供 `from session import run_session`，也不再提供顶层 `from harness import ...` 等旧导入。

如需程序内调用，用：

```python
from runtime import AgentResources, ResourceConfig, create_harness
```

然后显式创建 `messages`、写入 run、执行 `execute_run(...)`、再从 `resources.runs.get_run(run_id)` 读取结果。例如：

```python
import json

from runtime import AgentResources, ResourceConfig, create_harness

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

## 仅文档变更

```powershell
git diff --check
```
