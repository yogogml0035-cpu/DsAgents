# 项目总览

> 本文件承接 `AGENTS.md` 的项目定位和源码阅读入口。稳定边界见 `ARCHITECTURE.md`，具体事实见 `backend/.planning/codebase/`。

## 当前重点

- run-first：SQLite run ledger 记录事件和投影；LangGraph checkpointer/store 分别保存图上下文和 Agent memory；三库互不共享连接。
- HTTP：`POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`，客户端轮询，无 SSE/session API；HTTP create_run 成功后写 best-effort OMS JSONL。
- 事件：固定 7 类；GET 返回快照、顶层 `workflow` / `result`、增量 events、最新内容事件与 usage。OMS JSONL 不是 run event。
- 业务：Philips 固定 workflow，Tecan 由明确 Skill 请求驱动；两者把最终 JSON 写到 `run.result`。header 各自独立，`items[]` 共用完整 24 字段，不输出 `shipment` 或 Excel。
- 工具：静态 5 个，覆盖 PDF/ZIP 通用处理、Philips 主数据、共享 XLSX inspection 和 Tecan finalizer。没有 Tecan extractor SubAgent、业务状态机或 Excel 生成器。
- middleware：集中于 `runtime/middleware.py`。Philips 使用有界 `StructuredOutputRecovery`；普通/Tecan run 不强制 Philips schema，Tecan 使用 finalizer 工具验证终态。

## 技术栈指针

完整技术栈见 [`coding_maps/SYSTEM_MAP.md`](../coding_maps/SYSTEM_MAP.md) 与 [`backend/.planning/codebase/STACK.md`](../backend/.planning/codebase/STACK.md)：Python + uv、FastAPI、DeepAgents/LangGraph、SQLite、MinerU、openpyxl（XLSX 输入）和可选 Oracle。

## 源码阅读入口

- 运行时主链：`backend/runtime/execution.py`（执行/结果投影）、`backend/runtime/agent.py`（Brain 装配）、`backend/runtime/middleware.py`（hook）。
- 共享渠道合同：`backend/skills/channel_contract.py`，业务验收见 [`channel-supply-chain-json-prd.md`](channel-supply-chain-json-prd.md)。
- Philips（成对）：`backend/skills/philips-wgq-inbound-recognition/SKILL.md` + `backend/skills/philipswgqinboundrecognition/`（`schema.py`、`scripts/tools.py`）。
- Tecan（成对）：`backend/skills/tecan-import/`（`SKILL.md`、`references/`）+ `backend/skills/tecanimport/`（`schema.py`、`scripts/tools.py`）。
- artifact 基础设施：`backend/integrations/artifacts.py`；MinerU：`backend/integrations/mineru.py`；run 持久化：`backend/runtime/runs.py`。
- 接口：[INTERFACES.md](../INTERFACES.md)；地图：[coding_maps/SYSTEM_MAP.md](../coding_maps/SYSTEM_MAP.md)；按任务阅读：[reading-order.md](reading-order.md)。
