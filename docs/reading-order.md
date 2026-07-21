# 按任务阅读顺序

## 通用实现任务

1. `AGENTS.md`
2. `docs/conventions.md`
3. `backend/.planning/codebase/ARCHITECTURE.md`
4. 按改动范围阅读 `STRUCTURE.md`、`CONVENTIONS.md`、`TESTING.md`

## HTTP、run 或跨边界任务

1. `INTERFACES.md`
2. `coding_maps/SYSTEM_MAP.md`
3. `backend/.planning/codebase/INTEGRATIONS.md`
4. `backend/.planning/codebase/CONCERNS.md`

## Philips / Tecan 渠道抽取任务

1. `docs/channel-supply-chain-json-prd.md`
2. `backend/skills/channel_contract.py`
3. 对应 `SKILL.md` 和 `references/`
4. 对应 `schema.py` / `scripts/tools.py`
5. `backend/tests/test_philips_wgq_inbound_recognition.py` 或 `backend/tests/test_tecan_import.py`
6. `runtime/execution.py`（最终 `run.result` 投影）

## middleware 或 DeepAgents 任务

1. `backend/runtime/middleware.py`
2. `backend/runtime/agent.py`
3. `backend/.planning/codebase/CONVENTIONS.md` 与 `CONCERNS.md`
4. `backend/tests/test_harness.py`、`backend/tests/test_workflow_setup.py`

当前架构不使用 Tecan SubAgent、业务状态机或 Tecan HTTP workflow。若某项需求确实需要跨 run 暂停/恢复，先在架构文档定义唯一状态归属与查询合同，再编码。
