# CONCERNS — backend 可操作警告

> Analysis Date: 2026-07-22。这里列出当前实现的真实边界、脆弱点和验证动作，不把尚不存在的需求变成框架。

## 高风险改动点

### Philips `StructuredOutputRecovery`

- 位置：`runtime/middleware.py`。它是 Philips ToolStrategy 的 `after_model` recovery，不是通用业务校验器。
- `@hook_config(can_jump_to=["model", "end"])` 必须保留 `end`；重试耗尽时必须显式 `jump_to: "end"`，不能只返回 `None`，否则 ToolStrategy 图可能 model↔model 循环。
- 空 `data: {}`/缺 `header` 或 `items` 时，先按同回合 `ToolMessage.tool_call_id` 验证同一 AIMessage 的文本 JSON；不能扫描任意历史文本。
- 空壳恢复耗尽沿用既有 all-null `partial_success` + runtime problem 技术 fallback；不要编造业务值。其他未恢复的 structured response 使 Philips run `failed`。
- 改动后必须运行 `python -m tests.test_harness`。

### Philips workflow 工具 denylist

- 位置：`runtime/agent.py` `_PHILIPS_EXCLUDED_TOOLS`。
- 当前只排除 `finalize_tecan_overseas_recognition`，保留共享 `parse_documents`、`extract_archives`、`inspect_supply_chain_workbooks` 和 Philips lookup。
- 新增其他业务工具时，判断其是否要加入 denylist；禁止以业务-only allowlist 替代。运行 `python -m tests.test_workflow_setup`。

### Tecan finalizer 捕获

- 位置：`runtime/execution.py` `_tecan_finalized_response`。
- 执行层只信任名为 `finalize_tecan_overseas_recognition` 的 ToolMessage，避免把普通工具文本误认为 OMS 结果。
- Tecan 是通用 Skill 路径：若 Agent 未遵循 Skill 调用 finalizer，run 可作为普通阅读 run 成功但 `result=null`。真实模型验收应覆盖“明确 Tecan 请求必调用 finalizer”。
- 不要用新的 HTTP workflow、SubAgent 状态或候选表补救这一点；先改 Skill 提示词/工具描述，并用真实回归观察。

### 共享 JSON 合同

- `OrderItem` 是两个渠道共用的 24 字段合同；新增字段会同时影响 Philips、Tecan、recovery skeleton 和 tests。
- 空白文本会规范化为 `null`，数值接受规范十进制或标准千分位输入并输出无千分位字符串；畸形分组、科学计数法、非有限数会被拒绝。
- 正常业务 `partial_success` 应只表示核心商品事实已确认且补充字段缺失。Philips recovery 的 all-null 技术 fallback 是运行时兜底，不得被业务 Skill 当作正常裁决模板。

## 设计取舍（不是缺失）

| 取舍 | 当前理由 |
|---|---|
| 不新增业务状态机/消息表 | run ledger 已记录终态，checkpointer 已保存 thread 上下文；同票归集在一次 run 完成 |
| 不设 Tecan SubAgent | 单一 Agent 已可读同票材料；子代理会引入候选合并、消息隔离与额外状态管理 |
| 不加 Tecan 业务 middleware | 字段证据与 outcome 是特定合同，专用 finalizer 更窄、更可测试 |
| 不生成 Excel | PRD 只交付 OMS JSON；保留 `openpyxl` 仅为 XLSX 输入 |
| 无 SSE/session API | 调用方轮询 `run_id`，不增加长期连接或 session CRUD |

出现跨 run 人工续办、可恢复任务队列或并行独立材料处理的真实产品需求时，才重新评估这些取舍并先定义唯一持久化归属。

## 外部依赖与运行风险

| 依赖 | 风险/降级 | 验证 |
|---|---|---|
| MinerU | PDF 解析失败会导致材料事实不足；不应伪造字段 | local fake + opt-in 实样 |
| Oracle | 需要配置与可能的 thick client；失败时返回 problems/null | `test_philips_wgq_inbound_recognition` + 部署机 smoke |
| XLSX | 密码保护、损坏或非 `.xlsx` 会被 inspection 记录为 problem | `test_tecan_import` |
| 模型 tool call | 可能不调用最终 schema/finalizer 或输出不完整 | `test_harness` + 真实模型回归 |
| 单进程锁/cancel | 不跨 worker，cancel 为协作式 drain | `test_api` / `test_harness` |

`ORACLE_CLIENT_LIB_DIR` 只在需要 thick mode 时由部署提供；不要在文档或代码中写入连接串。运行数据、JSON artifacts 和 OMS log 会累积，生产需要由外部运维安排保留/清理策略。

## 测试证据缺口

- 默认 assert 脚本证明 schema、工具和投影路径，不证明复杂真实 PDF/XLSX 的语义识别准确率。
- 尚需用实际多发票、多运单、多币种、重复 12NC、冲突金额和不支持附件样本验证 Skill 行为。
- 模型/外部服务回归必须和本地门禁分开，避免 CI 因供应商可用性或个人样本路径波动。
- `FakeBrain` 对 subagent 元数据的模拟仅覆盖 event filtering/usage 历史路径，生产工厂没有 Tecan subagent。

## 修改前速查

1. 改最终 JSON 或 schema：同时读 `skills/channel_contract.py`、两渠道 schema、Skill/references 和两套测试。
2. 改 Philips recovery：保持 `end` jump、同回合 `tool_call_id`、fallback 语义，并跑 `test_harness`。
3. 改工具表或 Skill 挂载：跑 `test_tools`、`test_workflow_setup`，检查 package-data。
4. 改 HTTP/run：回看 `INTEGRATIONS.md`，不要添加 SSE/session API，跑 `test_api` 和 `test_run_ledger`。
5. 改外部依赖：同步本文件、根级架构风险和真实验证步骤。
