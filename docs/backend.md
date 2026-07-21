# backend 概览

`backend/` 是 DsAgents 的唯一产品子项目，发行名为 `dsagents`。它提供 run-first Agent runtime 和两个内置渠道供应链 Skill，不含前端。

## 快速理解

1. 从 `backend/api.py` 看四个 HTTP 端点与 run/session 边界。
2. 从 `backend/runtime/execution.py` 看 `HarnessRuntime.execute_run()` 如何把 Agent stream 投影到 ledger。
3. 从 `backend/runtime/agent.py` 看 DeepAgents 装配、Philips workflow 和工具 denylist。
4. 从 `backend/skills/channel_contract.py` 看 Philips/Tecan 共用的 24 字段商品合同。
5. 从两个 Skill 目录看材料归集、最终 schema 和工具。

## 当前能力

- PDF/XLSX 同票材料由 Agent 动态识别角色、归集、抽取和裁决；PDF 使用 MinerU，XLSX 只读为 JSON artifact。
- Philips 使用固定 `workflow=philips_wgq_inbound_recognition` 和 Pydantic structured response。
- Tecan 用明确 Skill 请求和 `finalize_tecan_overseas_recognition` 最终工具；没有 Tecan HTTP workflow、A/B/C SubAgent 或 Excel 生成器。
- 两渠道都将唯一、干净的业务 JSON 写入 `run.result`。header 各自独立，`items[]` 共享完整 24 字段，不输出 `shipment`。
- run 的业务 outcome 为 `success` / `partial_success` / `input_problems`；业务问题是 `succeeded` run，执行异常才是 `failed`。

## 运行时主要部件

| 位置 | 内容 |
|---|---|
| `runtime/runs.py` | SQLite run ledger 与 append-only events |
| `runtime/resources.py` | artifacts、checkpointer、store、memory 资源 |
| `runtime/execution.py` | 执行、stream 归一化、结果捕获、cancel |
| `runtime/middleware.py` | Philips recovery、tool telemetry、loop guard、compatibility |
| `runtime/tools.py` | 5 工具静态目录 |
| `integrations/` | MinerU、artifact、Oracle、OMS JSONL |
| `skills/` | 渠道 Skill 资源/实现双目录和共享合同 |

## 约束与命令

使用 `uv`：

```powershell
cd backend
uv sync
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
```

不要用 `pip install -e .`。详细事实、风险和测试说明见 `backend/.planning/codebase/`；系统接口见根级 `INTERFACES.md`。
