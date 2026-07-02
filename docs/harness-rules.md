# Harness 原则与运行时规则（详情）

> 由根级 `AGENTS.md` 抽出的全局硬约束详情。本文件承载跨任务、稳定、不易从代码一眼看出的项目规则。说明性文字为简体中文，代码标识符/路径/命令/配置键保留原文。

## Harness 原则

稳定接口，而非实现（Stabilize interfaces, not implementations）。五个模块边界为稳定契约，每个用 `Protocol` 定义接口 + 具体实现，二者解耦——可换实现而不动边界：

| 边界 | 职责 |
|---|---|
| `Session` | 以 append-only 事件存储完整持久任务事实（真相源） |
| `Harness` | 读 Session 历史 → 派生 context window → 请求执行 → 写回结果事件 |
| `Hands` | 暴露 model/tool 执行 trace 并透传真实错误 |
| `Resources` | 拥有 durable stores、checkpointers、artifact 路径 |
| `Tools` | 暴露 callable 能力，不绑定单一 runner |

### 关键约束

- **Session 不是上下文窗口**。摘要或裁剪视图可作为事件追加，但**不得替换原始事件**作为真相源。
- **错误必须透传**。`Hands` 与 Tools 均 `try/except ... raise`（先记录事件再重抛），不得吞错误。
- **保持 Harness 纤薄**。优先真实错误传播、可审计的工具结果、可恢复事件、简单工具注册。在真实调用方需要之前，不加 service layer、policy framework、workflow engine、container、auth 或宽泛安全/配置系统。
- **Simplicity Constraint**。优先删减作用域而非增加旋钮。每个新抽象必须保护五边界之一，否则删除。

### 第一里程碑范围

交付最小可运行 DeepAgents demo：一个 MinerU 解析工具、一个 DeepAgents 工厂、一个 `CompositeBackend` 配置、一个最小 session runner。在真实调用方需要之前，不加 service layer、container、auth、policy framework 或 workflow engine。

## 运行时规则（本里程碑固定）

- MinerU 调用走 `http://10.11.0.110:6006` 异步任务 API：`POST /tasks` → 轮询 `GET /tasks/{task_id}` → 取 `GET /tasks/{task_id}/result`。
- MinerU 参数 `backend=hybrid-engine` 与 `effort=high` **固定不可由用户配置**。
- DeepAgents 文件系统默认保持 `StateBackend`。
- 持久历史与记忆路由走 `StoreBackend`（本地 SQLite `.db`）。
- 大 artifact 与大 tool/model 日志落文件系统于 `data/artifacts/`。
- 复用 DeepAgents 内建虚拟文件系统；**不得另加虚拟文件系统包装**。
- Middleware 可记录 model-visible 消息、tool calls、tool results、final answers；**不得打印或持久化隐藏思维链**。

## 相关文档

- 系统级架构与边界：`ARCHITECTURE.md`
- 接口边界与调用关系：`INTERFACES.md`
- 跨子项目系统地图：`coding_maps/SYSTEM_MAP.md`
- backend 实现事实与陷阱：`backend/.planning/codebase/`（尤其 `CONVENTIONS.md`、`CONCERNS.md`）
