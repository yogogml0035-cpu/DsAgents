# CONVENTIONS — backend 编码与质量约定

> Analysis Date: 2026-07-22。这里记录当前实现已经依赖的约定；全局维护规则见根级 `docs/conventions.md`。

## 模块、命名与抽象

- 产品代码只放在 `backend/api.py`、`runtime/`、`integrations/`、`skills/`；不新建顶层 `dsagents/` 包、service 层或通用 workflow 引擎。
- `typing.Protocol` 只用于 `Brain` / `BrainFactory`。工具是普通 callable + `ToolCatalog`；资源和 ledger 使用具体类。
- 新业务 Skill 必须同时有 kebab-case 资源目录和可 import Python 包，并按需要更新 `[tool.setuptools.package-data]`；不做目录扫描或动态 loader。
- 资源、artifact 和 ledger 的类型/字段用明确 Pydantic/dataclass 合同，不为单一实现增加 ABC、策略树或配置开关。

## run-first 与状态

- run 是唯一执行和查询单位。`run_events` append-only，`runs` 是投影；`session_id` 只用于 `thread_id` 和进程内单飞。
- 业务 JSON 只写 `run.result`；不得从 `reply`、thinking、候选 tool 文本或 Excel 推断正式结果。
- 当前渠道抽取在同一个 run 中完成材料归集和最终裁决。不要为此新建消息队列、任务状态表、跨 run 中间态或 Tecan SubAgent 编排；它们会与已有 ledger/checkpointer 形成双源状态。
- 只有出现真实的暂停、人工续办或跨 run 恢复消费者时，才设计新的业务状态，并同时定义归属、持久化和查询契约。

## 渠道 JSON 语义

- `skills/channel_contract.py` 是 Philips/Tecan 共用 `items[]` 合同：完整 24 字段，未知用 `null`，不允许空字符串冒充未知。
- `success` 表示最终业务字段没有未解决缺失；`partial_success` 表示核心商品事实确认而补充字段缺失；`input_problems` 表示票次身份或核心事实无法确认。候选值只进 `problems`，不进正式字段。
- 每个 `problems[]` 项都应给出 `source`、`location`、`issue`、`action`。`input_problems` 保留完整 `data.header` 与已证实 items；无安全商品行时为 `items: []`。
- 票据事实优先于主数据；主数据只做唯一非语义标识匹配的标准化/补齐，不覆盖本票数量、金额、重量、编号或运输事实。
- 发票行按上传顺序和原行顺序；同 12NC 默认不合并；同票多发票/运单按材料出现顺序以英文逗号连接；不输出 `shipment` 或 Excel。

## 工具与材料边界

- 工具在 `runtime/tools.py` 静态注册。当前五个工具见 `ARCHITECTURE.md`；新增工具须同步测试和 Philips denylist 判断。
- Philips workflow 收窄工具必须用 denylist，只排除其他业务 finalizer，保留共享 `parse_documents` / `extract_archives` 与 `inspect_supply_chain_workbooks`。
- PDF 使用 `parse_documents`，XLSX 使用 `inspect_supply_chain_workbooks`。渠道 Skill 不解析 ZIP、DOCX、图片内容；在材料足够时将这些文件写入 `problems` 后继续。
- Tecan finalizer 只校验/返回终态 JSON，不写 Excel、候选 artifact 或 OMS 数据。XLSX inspection 的 JSON artifact 是给当前 Agent 读的材料载体，不是 OMS 合同。

## Middleware

- `runtime/middleware.py` 只承载运行时横切能力。不要把 Philips/Tecan 字段裁决塞进全局 middleware。
- `StructuredOutputRecovery` 使用 class-based `after_model`，因为它需要配对 `ToolMessage.tool_call_id`、读取当前消息并返回 state update / `jump_to`。其 hook 必须声明 `can_jump_to` 至少含 `"end"`；耗尽必须显式跳 `"end"`。
- `runtime_middlewares(structured_schema=None)` 用于普通/Tecan run，避免把非 Philips 文本按 Philips schema 恢复。传入 Philips schema 时才装 `StructuredOutputRecovery`。
- middleware 顺序遵循洋葱模型：`before_*` 正序、`after_*` 逆序、`wrap_*` 外层先入后出。需要最后处理的 Philips recovery 放在列表最前。

## 错误与事件

- 真实模型/工具错误让 `HarnessRuntime` 终止该 run 并投影 `failed`；业务 `input_problems` 仍投影 `succeeded`。
- 固定事件只可为 `status`、`tool_execution`、`tool_progress`、`thinking`、`text_delta`、`assistant_message`、`model_usage`。
- 不新增 SSE、session API、旧 `tool_call` / `tool_status` / `tool_result` 事件或隐式 artifact 路径。

## 文档与验证

- 文档使用简体中文，保留标识符、路径、命令和配置键；不写密钥、`.env` 值或私有连接串。
- 修改 backend 后，先同步 `backend/.planning/codebase/`，再更新根级架构、接口和系统地图。
- 常用门禁：`python -m tests.test_harness`（recovery）、`python -m tests.test_workflow_setup`（工具与构图）、`python -m tests.test_tecan_import`（XLSX/终态合同），最后全量七脚本和根级 `git diff --check`。
