# backend 概览

`backend/` 是 DsAgents 的唯一产品子项目，发行名为 `dsagents`。它提供 run-first Agent runtime 和两个内置渠道供应链 Skill，不含前端。

## 快速理解

1. 从 `backend/api.py` 看四个 HTTP 端点与 run/session 边界。
2. 从 `backend/runtime/execution.py` 看 `HarnessRuntime.execute_run()` 如何把 Agent stream 投影到 ledger。
3. 从 `backend/runtime/agent.py` 看 DeepAgents 装配、WAG/DK workflow 和工具 denylist。
4. 从 `backend/skills/channel_contract.py` 看 Philips/Tecan 共用的 24 字段商品合同。
5. 从两个 Skill 目录看材料归集、最终 schema 和工具。

## 当前能力

- PDF/XLSX 同票材料由 Agent 动态识别角色、归集、抽取和裁决；PDF 使用 MinerU，XLSX 只读为 JSON artifact。
- Philips 使用 `workflow=WAG` 和 Pydantic structured response；`StructuredOutputRecovery` 的 `can_jump_to` 须含 `"end"`，耗尽显式 `jump_to: "end"`。
- Tecan 使用 `workflow=DK` 和 `finalize_tecan_overseas_recognition` 最终工具；没有 extractor SubAgent、Excel 模板或生成器。
- 两渠道都将唯一、干净的业务 JSON 写入 `run.result`。header 各自独立，`items[]` 共享 `channel_contract.OrderItem` 完整 24 字段，不输出 `shipment`。
- run 的业务 outcome 为 `success` / `partial_success` / `input_problems`；业务问题是 `succeeded` run，执行异常才是 `failed`。
- WAG / DK 用 **denylist** 排除对方业务工具，保留共享 MinerU 与 XLSX 检查器；禁止业务-only allowlist。

## 运行时主要部件

| 位置 | 内容 |
|---|---|
| `runtime/runs.py` | SQLite run ledger 与 append-only events |
| `runtime/resources.py` | artifacts、checkpointer、store、memory 资源 |
| `runtime/execution.py` | 执行、stream 归一化、结果捕获、cancel |
| `runtime/middleware.py` | Philips recovery、tool telemetry、loop guard、compatibility |
| `runtime/tools.py` | 5 工具静态目录 |
| `integrations/` | MinerU HTTP、artifact 路径与 JSON 落盘（无业务 schema） |
| `runtime/oms_log.py` | HTTP create_run 成功后的 OMS `run_created` JSONL 旁路索引（best-effort，不阻塞 run） |
| `skills/` | 下划线命名的渠道 Skill 包（`SKILL.md` / references / schema / scripts 同目录）、共享 `channel_contract`；Philips Oracle 主数据在 Skill 工具内 |

权威源码仅 `api.py` + `runtime/` + `integrations/` + `skills/` + `tests/` + `pyproject.toml`。**不要**把 `backend/build/`、`dist/`、`*.egg-info` 当源码。新增 Skill 须同步 `pyproject.toml` package-data 与静态工具注册。

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

不要用 `pip install -e .`。改 denylist 跑 `test_workflow_setup`；改 Recovery 跑 `test_harness`。详细事实、风险和测试说明见 `backend/.planning/codebase/`；系统接口见根级 `INTERFACES.md`。
