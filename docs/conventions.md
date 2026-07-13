# 核心原则与维护规则

> 根级 AGENTS.md 的详情文档之一。这些原则对全仓库每次任务都适用，须严格遵守。

## 核心原则（全局人工约束）

- **能力可插拔**：Brain（如 DeepAgents）、执行器、工具做成可插拔；项目自身拥有 run、事件、资源、工具路由与运行时状态，不被硬编码到某个 runner、容器、模型或工作流。
- **Protocol 不泛化**：`typing.Protocol` 只用于运行时注入的能力边界（当前 `Brain` / `BrainFactory`）。工具用普通 callable + `ToolCatalog`，资源 / ledger 用具体类；除非出现真实替换点，不为单实现代码新增 Protocol/ABC。读默认实现时从 `create_harness(...)` 进入。
- **run 是事件源**：`run_events` 表 append-only 存完整规范化事件与 raw chunk；`runs` 表是事件投影出的快照。外部消费规范化事件；最终 `assistant_message.payload` 可携带最后一个 `thinking` 文本和最终 `text`。短期上下文不再自建回放，统一交给 LangGraph `checkpointer` + `thread_id=session_id`。
- **保持运行时薄**：提交 run、驱动 Brain、规范化 stream chunk、写回 run 事件。在真实 caller 需要前，不增加服务层、策略框架、工作流引擎、容器或宽泛的安全/配置系统。
- **真实错误透传**：暴露模型/工具执行 trace 并把真实错误向上传，不吞异常、不包装失真。
- **简单性约束**：优先删减范围而非增加旋钮。

> 完整阐述与"为什么"见 `ARCHITECTURE.md` §5（关键约束/当前风险）与 `backend/.planning/codebase/ARCHITECTURE.md`（内部架构与核心运行时原则）。

## 维护规则

- **事实层在子项目**：`backend/.planning/codebase/` 是事实来源；根级只放导航和稳定全局原则。
- **改代码后同步事实层**：修改 `backend/` 实现后，先更新对应事实文档，再视影响回看 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`。
- **文档用简体中文**：保留代码标识符、文件路径、命令、配置键、API 名称、IP/端口原文。
- **不外泄密钥**：文档不写入任何密钥 / token / 连接串。
- **证据不足标注**：用"需确认 / 初步判断"表达，不写成硬规则。
- **新增部署依赖须同步风险**：新增运行时/部署依赖（如外部客户端库、系统级组件）时，必须把对应的部署前提、缺失时的降级行为、验证步骤同步到 `backend/.planning/codebase/CONCERNS.md`（Oracle thick client 即为范例，见该文件 §8），并在根级 `ARCHITECTURE.md` §7 风险清单同步一条系统级条目。
