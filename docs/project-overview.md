# 项目总览

> 本文件承接 `AGENTS.md` 的项目定位和源码阅读入口。稳定边界见 `ARCHITECTURE.md`，具体事实见 `backend/.planning/codebase/`。

## 当前重点

- run-first：SQLite run ledger 记录事件和投影；LangGraph checkpointer/store 分别保存图上下文和 Agent memory；三库互不共享连接。
- HTTP：`POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`，客户端轮询，无 SSE/session API；HTTP create_run 成功后写 best-effort OMS JSONL（非 `run_events`、无查询 API、失败不阻塞 run）。
- 事件：固定 7 类；GET 返回快照、顶层 `workflow` / `result`、增量 events、最新内容事件与 usage。OMS JSONL 不是 run event。
- 业务：`WGQ` 路由 Philips 外高桥，`DK` 路由 Tecan 境外供应链；两者把最终 JSON 写到 `run.result`。header 各自独立，`items[]` 共用完整 24 字段，不输出 `shipment` 或 Excel。
- Skill：**单目录**下划线可 import 包（资源与代码同目录）；Agent 侧以连字符 `/skills/<kebab-case>/` 别名加载（与 `SKILL.md` `name` 一致）；新增须更新 `package-data` 与 skills 路由。Tecan 不携带 Excel 模板或生成器。
- 工具：静态 5 个，覆盖 PDF/ZIP 通用处理、WGQ / DK 共享的 12NC 主数据、共享 XLSX inspection 和 Tecan finalizer。WGQ denylist 排除 Tecan finalizer，DK 当前为空以保留共享 lookup；禁止业务-only allowlist。没有 Tecan extractor SubAgent 或业务状态机。
- middleware：集中于 `runtime/middleware.py`。WGQ 使用有界 `StructuredOutputRecovery`（`can_jump_to` 含 `"end"`，耗尽显式 `jump_to: "end"`）；DK/普通 run 不强制 Philips schema，DK 使用 finalizer 工具验证终态。
- 源码权威：仅 `backend/api.py`、`runtime/`、`integrations/`、`skills/`；**不要**把 `backend/build/` 当源码。

## 技术栈指针

完整技术栈见 [`coding_maps/SYSTEM_MAP.md`](../coding_maps/SYSTEM_MAP.md) 与 [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)：Python + uv、FastAPI、DeepAgents/LangGraph、SQLite、MinerU、openpyxl（XLSX 输入）和可选 Oracle。

## 源码阅读入口

- 运行时主链：`backend/runtime/execution.py`（执行/结果投影）、`backend/runtime/agent.py`（Brain 装配与 denylist）、`backend/runtime/middleware.py`（hook）。
- 共享渠道合同：`backend/skills/channel_contract.py`，业务验收见 [`channel-supply-chain-json-prd.md`](channel-supply-chain-json-prd.md)。
- Philips：`backend/skills/philips_wgq_inbound_recognition/`（`SKILL.md`、货代版式 `references/`、`schema.py`、`scripts/tools.py`）。
- Tecan：`backend/skills/tecan_import/`（`SKILL.md`、`references/`、`schema.py`、`scripts/tools.py`；无 Excel 资产）。
- artifact 基础设施：`backend/integrations/artifacts.py`；MinerU：`backend/integrations/mineru.py`；run 持久化：`backend/runtime/runs.py`。
- 接口：[INTERFACES.md](../INTERFACES.md)；地图：[coding_maps/SYSTEM_MAP.md](../coding_maps/SYSTEM_MAP.md)；按任务阅读：[reading-order.md](reading-order.md)。
