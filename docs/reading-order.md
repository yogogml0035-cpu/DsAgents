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
2. `backend/skills/channel_contract.py`（共享 `OrderItem` 24 字段与 outcome）
3. 对应下划线 Skill 包内的 `SKILL.md` 和 `references/`
4. 对应 `schema.py` / `scripts/tools.py`（Tecan 无 Excel 模板/生成器）
5. `backend/tests/test_philips_wgq_inbound_recognition.py` 或 `backend/tests/test_tecan_import.py`
6. `runtime/execution.py`（最终 `run.result` 投影）

## middleware 或 DeepAgents 任务

1. `backend/runtime/middleware.py`（`StructuredOutputRecovery`：`can_jump_to` 含 `"end"`，耗尽 `jump_to: "end"`）
2. `backend/runtime/agent.py`（WAG/DK **denylist**，禁止业务-only allowlist）
3. `backend/.planning/codebase/CONVENTIONS.md` 与 `CONCERNS.md`
4. `backend/tests/test_harness.py`、`backend/tests/test_workflow_setup.py`

## 新增 Skill

1. `backend/.planning/codebase/STRUCTURE.md`（Skill 单目录与放置规则）
2. 新建下划线可 import 包：`SKILL.md` / references、schema、`scripts/tools.py`
3. `runtime/tools.py` 静态注册；跨业务互斥时更新 denylist
4. `pyproject.toml` `[tool.setuptools.package-data]`
5. 对应 tests + codebase 事实文档；本地回归见 `docs/commands.md`

当前架构不使用 Tecan SubAgent 或业务状态机；Tecan HTTP workflow 为 `DK`。**不要**把 `backend/build/` 当源码。若某项需求确实需要跨 run 暂停/恢复，先在架构文档定义唯一状态归属与查询合同，再编码。
