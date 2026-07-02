# SYSTEM_MAP — DsAgents Agent Harness 系统地图

> 系统层跨子项目理解手册。综合根级 `AGENTS.md` 与子项目 `backend/.planning/codebase/` 事实文档生成。本地图保持在系统层，链接到子项目事实文档，不复制底层实现细节。所有说明性文字为简体中文，代码标识符、路径、命令、配置键保留原文。

## 1. 系统目的与仓库形态

DsAgents Agent Harness 是一个**保持纤薄的 Agent 运行时基座（Harness）**。核心目标是把 `Session`、`Harness`、`Hands`、`Resources`、`Tools` 稳定为五个模块边界，能力不硬编码进单一 runner、container、model 或 workflow。DeepAgents 在其中扮演可插拔的 `Brain`（子 Harness），本地确定性分析器可作为可插拔执行器；项目自身拥有 Session、事件、资源、工具路由和运行时状态。

仓库形态：单子项目 monorepo。唯一产品子项目是 `backend/`（Python 包）。仓库根还有 `scripts/ralph/`（自动化工具，非产品事实，不入系统地图）与 `.agents/`（本地 agent 工具，被忽略）。

第一里程碑范围（`AGENTS.md` 第 23-30 行）：最小可运行 DeepAgents demo = 一个 MinerU 解析工具 + 一个 DeepAgents 工厂 + 一个 `CompositeBackend` 配置 + 一个最小 session runner。

## 2. 子项目职责表

| 子项目 | 路径 | 主要职责 | 是否可独立维护 |
|---|---|---|---|
| backend | `backend/` | 整个 Harness 运行时：五大模块边界实现（Session/Harness/Hands/Resources/Tools）、MinerU 工具、DeepAgents Brain 工厂、CLI session runner、离线自检 | 是（唯一产品子项目，当前无前端/无独立 API 服务） |

## 3. 跨子项目调用链与数据流

由于当前只有 `backend` 一个产品子项目，跨项目链路即 backend 内部的端到端流，外加与外部 MinerU 服务的交互：

```
用户(CLI) ──message──▶ backend/__main__.py ──▶ session.main
   │
   ▼
run_session ──(在 AgentResources 上下文内)──▶ create_mineru_harness(resources)
   │
   ▼
HarnessRuntime.run_turn  (read-derive-request-write 循环)
   │
   ├─ Session.ensure_session / emit_event("user_message")   [SQLite: data/dsagents_sessions.db]
   ├─ Session.context_window  (派生视图，非真相)
   ├─ DeepAgentsBrainFactory.create ──▶ create_deep_agent(model, tools, middleware, backend, checkpointer, store)
   │       │
   │       ├─ tools: parse_document_with_mineru  ──HTTP──▶ MinerU 服务 (http://10.11.0.110:6006)
   │       │       POST /tasks  ─▶  轮询 GET /tasks/{id}  ─▶  GET /tasks/{id}/result
   │       │       (固定 backend=hybrid-engine, effort=high)
   │       │
   │       ├─ middleware: TraceHands.TraceMiddleware  (model/tool trace + 错误透传)
   │       └─ backend: CompositeBackend
   │              default: StateBackend
   │              /memories/ /conversation_history/ /logs/ ──▶ StoreBackend [data/dsagents_store.db]
   │              /artifacts/ /large_tool_results/ ──▶ FilesystemBackend(virtual_mode=True) [data/artifacts/]
   │
   └─ Session.emit_event("assistant_message") / trace 事件 (全部 append-only)
```

入口链逐行溯源见 `backend/.planning/codebase/STRUCTURE.md`。

## 4. 后端到前端的接口边界

**当前不存在前端 / Web 服务层。** 唯一对外接口是 CLI：

```
python -m backend "<message>" --session-id <可选>
```

离线验证入口（不触达网络/LLM）：

```
python -m backend.self_check
```

模型默认 `openai:gpt-5.5`，可经环境变量 `DSAGENTS_MODEL` 覆盖。具体 provider 可达性与鉴权当前源文档未确认。

## 5. 共享状态、存储、事件、产物、认证与 provider 边界

- **共享状态 / 事件**：以 `Session` 的 append-only 事件为唯一真相源（表 `sessions` / `session_events`）。摘要或裁剪视图可作为事件追加，但**不得替换原始事件**（`AGENTS.md` 第 19 行）。详见 `backend/.planning/codebase/ARCHITECTURE.md` 第 1 节。
- **存储分层**（均在 `data/` 下）：
  - `data/dsagents_sessions.db` — 自建 `SqliteSessionStore`。
  - `data/dsagents_store.db` — DeepAgents `SqliteStore`（memories/history/logs）。
  - `data/dsagents_checkpoints.db` — LangGraph `SqliteSaver`（线程检查点）。
- **产物（artifacts）**：大 artifact 与大 tool/model 日志落文件系统于 `data/artifacts/`；超大事件 payload（>`max_inline_bytes`=256KB）溢出到 `data/artifacts/session-events/*.json`，DB 仅存 stub（`AGENTS.md` 第 40-41 行）。MinerU 默认输出 `data/mineru_outputs/{stem}.md`。
- **虚拟文件系统**：复用 DeepAgents 内建虚拟文件系统（`FilesystemBackend(virtual_mode=True)`），**不得另加虚拟文件系统包装**（`AGENTS.md` 第 41 行）。
- **认证**：当前**无认证/鉴权**（MinerU 与模型调用均无 auth）。按 Simplicity Constraint，在真实调用方需要前不引入。
- **provider 边界**：模型经 `Brain` / `BrainFactory` Protocol 抽象，DeepAgents 是当前默认实现，可替换而不动五边界。MinerU 经 `ToolCatalog` 注册，Harness 不感知其实现。

## 6. 子项目之间的依赖与归属规则

- 当前仅 `backend` 一个产品子项目，无子项目间依赖。
- 归属规则：所有运行时能力归属到五模块边界之一——状态→Session，编排→Harness，trace/错误→Hands，持久化→Resources，可调用能力→Tools。新增抽象必须保护其中一边界，否则删除（Simplicity Constraint，`AGENTS.md` 第 44-46 行）。
- `scripts/ralph/` 是独立自动化工具，**不属于产品事实**，不纳入系统地图与子项目依赖。

## 7. 按任务分类的阅读指南

| 任务类型 | 先读 |
|---|---|
| 后端业务、事件、Session、Runner 修改 | `backend/.planning/codebase/ARCHITECTURE.md`（第 1、2 节）→ `STRUCTURE.md` → `CONVENTIONS.md` |
| MinerU 工具、HTTP 调用、轮询逻辑修改 | `backend/.planning/codebase/INTEGRATIONS.md`（MinerU 契约）→ `CONCERNS.md`（第 4 节运行时耦合） |
| 存储 / CompositeBackend / checkpointer 修改 | `backend/.planning/codebase/ARCHITECTURE.md`（第 4 节 Resources）→ `INTEGRATIONS.md`（持久化位置）→ `STACK.md` |
| trace / middleware / 错误传播修改 | `backend/.planning/codebase/ARCHITECTURE.md`（第 3 节 Hands）→ `CONVENTIONS.md`（第 3、4 节）→ `CONCERNS.md`（第 6、7 节） |
| 跨系统接口（未来前端 / 外部服务）修改 | 本文件（第 4 节）→ `AGENTS.md`（Runtime Rules） |
| 模型 / provider 替换 | `backend/.planning/codebase/STACK.md`（模型配置）→ `ARCHITECTURE.md`（第 2、5 节 Protocol 边界） |

## 8. 集成风险检查清单

- [ ] MinerU 服务 `http://10.11.0.110:6006` 是否可达？（不可达则全链路失败，无 fallback）
- [ ] `DSAGENTS_MODEL` 指向的 provider 是否可达、是否有鉴权？（当前源文档未确认）
- [ ] 进程是否在仓库根运行？（`data_dir=Path("data")` 为相对路径，移动/重命名 `data/` 会使 `artifact_path` 绝对路径失效）
- [ ] 修改是否守住了五边界？（勿把 MinerU URL / 模型名 / 超时写死进 `HarnessRuntime`）
- [ ] 是否引入了禁止范围？（service layer / container / auth / policy framework / workflow engine，在真实调用方需要前一律不加）
- [ ] trace middleware 是否打印或持久化了隐藏思维链？（禁止，`AGENTS.md` 第 42 行）
- [ ] 是否新增了第二套虚拟文件系统？（禁止，`AGENTS.md` 第 41 行）

## 9. 验证入口

- **离线自检（推荐，无需网络/LLM）**：`python -m backend.self_check`（覆盖工具辅助函数、资源初始化、Session 事件流、TraceHands 错误透传、HarnessRuntime 端到端、大 payload 溢出落盘）。
- **真实单次会话**：`python -m backend "<message>"`（依赖真实 DeepAgents + 模型 + 可达 MinerU）。
- 文档型变更：`git diff --check`（本仓库规则）。

## 10. 源文档索引

| 类别 | 文件 |
|---|---|
| 根级导航 | `AGENTS.md` |
| 子项目事实 | `backend/.planning/codebase/ARCHITECTURE.md` |
| 子项目事实 | `backend/.planning/codebase/STRUCTURE.md` |
| 子项目事实 | `backend/.planning/codebase/STACK.md` |
| 子项目事实 | `backend/.planning/codebase/INTEGRATIONS.md` |
| 子项目事实 | `backend/.planning/codebase/CONVENTIONS.md` |
| 子项目事实 | `backend/.planning/codebase/TESTING.md` |
| 子项目事实 | `backend/.planning/codebase/CONCERNS.md` |

> 证据不足处已用「当前源文档未确认」表达。本地图不发明依赖关系；随子项目事实文档更新而刷新。
