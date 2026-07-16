# 核心原则与维护规则

> 根级 AGENTS.md 的详情文档之一。这些原则对全仓库每次任务都适用，须严格遵守。

## 核心原则（全局人工约束）

- **能力可插拔**：Brain（如 DeepAgents）、执行器、工具做成可插拔；项目自身拥有 run、事件、资源、工具路由与运行时状态，不被硬编码到某个 runner、容器、模型或工作流。
- **Protocol 不泛化**：`typing.Protocol` 只用于运行时注入的能力边界（当前 `Brain` / `BrainFactory`）。工具用普通 callable + `ToolCatalog`，资源 / ledger 用具体类；除非出现真实替换点，不为单实现代码新增 Protocol/ABC。读默认实现时从 `create_harness(...)` 进入。
- **run 是事件源**：`run_events` 表 append-only 存完整规范化事件与 raw chunk；`runs` 表是事件投影出的快照。外部消费规范化事件；最终 `assistant_message.payload` 可携带最后一个 `thinking` 文本和最终 `text`。短期上下文不再自建回放，统一交给 LangGraph `checkpointer` + `thread_id=session_id`。
- **保持运行时薄**：提交 run、驱动 Brain、规范化 stream chunk、写回 run 事件。在真实 caller 需要前，不增加服务层、策略框架、工作流引擎、容器或宽泛的安全/配置系统。
- **真实错误透传**：暴露模型/工具执行 trace 并把真实错误向上传，不吞异常、不包装失真。
- **简单性约束**：优先删减范围而非增加旋钮。
- **源码顶层布局稳定**：产品代码落在 `backend/` 的 `api.py` + `runtime/` + `integrations/` + `skills/`；业务逻辑归属对应 Skill 包，不要再引入已删除的顶层辅助模块或 `backend/dsagents/` 包壳。
- **middleware 与有界 structured recovery**：实现集中在 `runtime/middleware.py`；`runtime_middlewares()` 固定顺序（`StructuredOutputRecovery` 最前）；`after_model` + `jump_to: "model"` 的重试必须同时声明 `can_jump_to` 含 `"end"`，耗尽时显式 `jump_to: "end"`，禁止只返回 `None`。空 `data: {}` 壳（`success`/`partial_success` 但缺嵌套字段或 `items` 空）走专属 `EMPTY_DATA_SHELL_HINT` + `PHILIPS_MINIMAL_DATA_SKELETON` 形状纠错（优先完整 JSON 文本再同内容 tool args）；**空壳重试耗尽**时写入 schema 合法的 all-null `data` + `partial_success` + runtime problem（不是 `data:{}`，也不是 `data:null`/`input_problems`），保证能返回 JSON，不编造业务字段值。其他解析失败仍无 `structured_response` 退出。改动后跑 `python -m tests.test_harness`。
- **Philips 业务结果 vs 执行失败**：业务 JSON 走 `run.result`（`outcome` 为 `success` / `partial_success` / `input_problems`）。`input_problems` 时 `data=null` 且 **run 仍 `succeeded`**；仅 `structured_response` 缺失/非法或运行时异常才令 run **`failed`**。不要用 `reply` 解析业务 JSON。
- **workflow 工具收窄用 denylist**：为固定 workflow 收窄工具表时，用 denylist 排除**其他业务**工具（如帝肯的 `save_tecan_extraction` / `generate_tecan_import`），必须保留共享 MinerU 工具 `parse_documents` / `extract_archives` 以及本业务工具；禁止业务-only allowlist（会与 `/memories/AGENTS.md`、Skill 手册中的 ZIP/解析指引脱节）。验证：`python -m tests.test_workflow_setup`。

> 完整阐述与“为什么”见根级 `ARCHITECTURE.md` §5（run-first 执行模型）与 §7（系统级风险），以及 `backend/.planning/codebase/ARCHITECTURE.md`（内部架构与核心运行时原则）。

## 维护规则

- **事实层在子项目**：`backend/.planning/codebase/` 是事实来源；根级只放导航和稳定全局原则。
- **改代码后同步事实层**：修改 `backend/` 实现后，先更新对应事实文档，再视影响回看 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`。
- **文档用简体中文**：保留代码标识符、文件路径、命令、配置键、API 名称、IP/端口原文。
- **不外泄密钥**：文档不写入任何密钥 / token / 连接串。
- **证据不足标注**：用“需确认 / 初步判断”表达，不写成硬规则。
- **新增部署依赖须同步风险**：新增运行时/部署依赖（如外部客户端库、系统级组件）时，必须把对应的部署前提、缺失时的降级行为、验证步骤同步到 `backend/.planning/codebase/CONCERNS.md`（Oracle thick client 见该文件 **Operational Risks** / **External Dependency Risks**），并在根级 `ARCHITECTURE.md` §7 风险清单同步一条系统级条目。

## 仍成立的开发约定（来自分支落地）

以下约定已在当前代码中稳定成立，修改相关面时优先遵守：

- **工具静态注册**：新业务工具写入对应 Skill 的 `scripts/tools.py`，并在 `runtime/tools.py` 的 `default_tool_catalog()` 显式注册；不要做目录扫描或动态 loader。当前静态 5 工具：2 个 MinerU 通用（`parse_documents` / `extract_archives`）+ Philips 主数据 1 + Tecan 业务 2。
- **事件 schema 固定 7 类**：`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`。不要重新引入已删除的 `tool_call` / `tool_status` / `tool_result`。
- **artifact 路径显式传递**：HTTP 与 Skill 边界使用 `/artifacts/...`；生成文件唯一命名、不覆盖输入。业务问题统一 `input_problems`（Philips 在 `result.outcome`；Tecan 在工具返回 `code`），跨 run 不隐式恢复中间态；业务问题 ≠ run `failed`。
- **共享操作手册 seed**：`/memories/AGENTS.md` 缺失时由资源层写入含 ZIP→`extract_archives` 等基线指引；**已有文件不覆盖**。改手册文案时同步检查 Skill 与 workflow 工具表是否仍一致。
- **进程内边界**：同 `session_id` 单飞锁、`run_controls` cancel 字典均为进程内；多 worker 不提供跨进程互斥或强杀。
- **SQLite 三库分离**：`dsagents_runs.db` / `dsagents_checkpoints.db` / `dsagents_store.db` 互不共享连接；新 schema 无迁移，部署切换需整目录一致清空或替换。
- **Philips 空壳 vs 业务问题**：`data: {}` 是非法形状（recovery 纠错 / 耗尽 skeleton），不是 `input_problems`；`input_problems` 要求 `data=null` 且至少一条 problem。空壳耗尽的 all-null skeleton 使用 `partial_success` + runtime problem，**禁止**用编造业务值“凑成功”。
- **Philips consolidated 票次**：同一 HAWB/运单下多张商业发票或多个 PO/DN 视为一票；header 字段可逗号拼接，items 按发票顺序展开。规则见 Skill 与 `PHILIPS_WORKFLOW_PROMPT`，改提示词时两边同步。
