# ARCHITECTURE — DsAgents Agent Harness 系统级架构

> 根级系统架构图。承接系统边界、子系统职责与推荐理解路径。详细实现事实见 `backend/.planning/codebase/`，跨子项目系统地图见 `coding_maps/SYSTEM_MAP.md`，导航与阅读顺序见 `AGENTS.md`。说明性文字为简体中文，代码标识符/路径/命令保留原文。

## 系统定位

DsAgents 是一个**纤薄的 Agent 运行时基座（Harness）**，不是框架追逐项目。它把 `Session`、`Harness`、`Hands`、`Resources`、`Tools` 稳定为五个模块边界；DeepAgents 作为可插拔的 `Brain`（子 Harness），本地确定性分析器可作为可插拔执行器。项目自身拥有 Session、事件、资源、工具路由与运行时状态。

设计原则（`AGENTS.md` Harness Principles）：
- Stabilize interfaces, not implementations（稳定接口，而非实现）。
- Keep the Harness thin（保持 Harness 纤薄）。
- Session is not the context window（Session 不是上下文窗口；原始 append-only 事件是唯一真相）。
- Prefer real error propagation, auditable tool results, recoverable events, simple tool registration。

## 系统边界

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI 入口                              │
│   python -m backend "<message>"   /   python -m backend.self_check  │
└───────────────────────────┬─────────────────────────────────┘
                            │
                ┌───────────▼───────────┐
                │      Harness          │  read-derive-request-write 循环
                │  (HarnessRuntime)     │  保持 thin，不持业务逻辑
                └───┬───────┬───────┬───┘
        ┌───────────┘       │       └───────────┐
        ▼                   ▼                   ▼
   ┌─────────┐        ┌──────────┐         ┌──────────┐
   │ Session │        │  Hands   │         │  Tools   │
   │(events) │        │(trace+err)│        │ (MinerU) │
   └────┬────┘        └────┬─────┘         └────┬─────┘
        │                  │                   │ HTTP
        ▼                  ▼                   ▼
   ┌─────────────────────────────┐      ┌──────────────────┐
   │        Resources            │      │  MinerU 服务      │
   │ SessionStore/StoreBackend/  │      │ http://10.11.0.110:6006  │
   │ FilesystemBackend/checkpoint│      └──────────────────┘
   └─────────────┬───────────────┘
                 ▼
            data/  (SQLite .db + artifacts/)
```

外圈约束：无鉴权、无容器、无 Web 服务层、无 policy/workflow 引擎（Simplicity Constraint，在真实调用方需要前一律不加）。

## 子系统职责（五大稳定边界）

| 边界 | 职责 | 稳定方式 | 详见 |
|---|---|---|---|
| `Session` | append-only 事件存储完整持久任务事实；非上下文窗口 | `SessionStore` Protocol + `SqliteSessionStore` 实现 | `backend/.planning/codebase/ARCHITECTURE.md` §1 |
| `Harness` | 读历史→派生 context window→请求执行→写回事件 | `Brain`/`BrainFactory` Protocol，`HarnessRuntime.run_turn` 不 catch 异常 | §2 |
| `Hands` | 暴露 model/tool 执行 trace，透传真实错误 | `Hands` Protocol + `TraceMiddleware`，错误先记录再 `raise` | §3 |
| `Resources` | 拥有 durable stores / checkpointers / artifact 路径 | `AgentResources` 装配 `CompositeBackend`（State/Store/Filesystem） | §4 |
| `Tools` | 暴露 callable 能力，不绑定 runner | `ToolCatalog` + `ToolHandler`，MinerU 为首个工具 | §5 |

## 推荐理解方式

1. 先读 `AGENTS.md`（导航、原则、运行时规则）。
2. 读本文件掌握系统边界与子系统职责。
3. 读 `coding_maps/SYSTEM_MAP.md` 理解端到端调用链与外部集成。
4. 进入 `backend/.planning/codebase/` 按需阅读实现事实：先 `ARCHITECTURE.md` + `STRUCTURE.md`，再 `STACK.md` / `INTEGRATIONS.md` / `CONVENTIONS.md` / `TESTING.md` / `CONCERNS.md`。
5. 修改前对照 `coding_maps/SYSTEM_MAP.md` 的「按任务分类的阅读指南」与「集成风险检查清单」。

## 推荐目录职责

| 路径 | 职责 | 维护层 |
|---|---|---|
| `backend/` | 唯一产品子项目，五大边界实现 | 子项目事实层 |
| `backend/.planning/codebase/` | backend 的事实文档（实现细节） | 子项目事实层 |
| `coding_maps/` | 跨子项目系统地图 | 系统级补充层 |
| `data/` | 运行期产物（SQLite + artifacts，不入库） | 运行期生成 |
| `scripts/ralph/` | 独立自动化工具，**非产品事实** | 不入文档分层 |
| `.agents/` | 本地 agent 工具与 skills（被忽略） | 工具层 |

## 系统层面的维护建议

- 五边界是稳定契约；改动应落在某一个边界内，勿把能力硬编码进单一 runner。
- 新增抽象必须保护五边界之一，否则删除（Simplicity Constraint）。
- 子项目内部实现变化写进 `backend/.planning/codebase/`；跨项目/系统级变化更新本文件与 `coding_maps/SYSTEM_MAP.md`；导航与阅读顺序维护在 `AGENTS.md`。
- 证据不足时用「当前源文档未确认」表达，不发明依赖关系。
