# 系统架构 (ARCHITECTURE)

> 事实来源：backend/.planning/codebase/ 与 coding_maps/SYSTEM_MAP.md（2026-07-02 生成）

本文件是 DsAgents 仓库的**系统级架构总览**，描述系统边界、子系统职责、推荐理解路径与稳定目录职责。底层实现细节请直接查阅 `backend/.planning/codebase/` 下的对应事实文档，本文不复制。接口与集成边界见根级 `INTERFACES.md`；跨项目导航见 `coding_maps/SYSTEM_MAP.md`；全局入口与原则见根级 `AGENTS.md`。

---

## 1. 系统边界

- **形态**：单子项目仓库，当前唯一产品子项目为 Python 项目 `backend/`。五大模块边界（Session / Harness / Hands / Resources / Tools）全部落在该项目内。
- **对外形态**：`backend/` 暴露的是 **Python 导入 API**（`run_session`、`create_mineru_agent` 等），**不是 HTTP 服务**。无 FastAPI / uvicorn / Flask 等 web 框架，无 HTTP server、无健康检查端点、无 SSE / WebSocket 通道。
- **模块形态**：`backend/` **不是常规 Python 包**——没有 `__init__.py` / `__main__.py`，而是以**扁平顶层模块**形式（`pyproject.toml` 的 `[tool.setuptools] package-dir = {"" = "."}` + `py-modules = [...]`）安装；模块间用绝对导入（`from session import ...`），**不带** `backend.` 前缀。
- **无前端**：当前无前端子项目。`.env.example` 中的 `CORS_ORIGINS` 属预留边界，需确认（详见 `INTERFACES.md` §2）。
- **里程碑**：交付最小可运行的 DeepAgents 解析演示——一个 MinerU 解析工具 + 一个 DeepAgents 工厂 + 一个 `CompositeBackend` 配置 + 一个最小 session runner。刻意不引入服务层、容器、鉴权、策略框架或工作流引擎。

---

## 2. 子系统职责（backend 内部五大模块）

`backend/` 是 Harness 级 agent 运行时底座。核心设计是把能力（Brain、执行器、工具）做成可插拔，而项目自身拥有 Session、事件、资源、工具路由与运行时状态。

| 模块 | 文件 | 核心职责 | 公开接口（类/函数） |
|------|------|----------|----------------------|
| **Session** | `backend/session.py` | 以 append-only 事件存完整持久任务事实；从历史派生上下文窗口（不等于上下文窗口本身） | `SessionStore`、`SqliteSessionStore`、`SessionRecord`、`SessionEvent`、`ContextWindow`、`run_session` |
| **Harness** | `backend/harness.py` | 读 Session 历史 → 派生上下文 → 请求 Brain 执行 → 写回事件；保持薄 | `Brain`、`BrainFactory`、`DeepAgentsBrainFactory`、`HarnessRuntime`、`HarnessTurn`、`create_mineru_harness`、`create_mineru_agent` |
| **Hands** | `backend/hands.py` | 通过 middleware 暴露模型/工具执行 trace，并把真实错误透传 | `Hands`、`TraceHands`、`TraceMiddleware` |
| **Resources** | `backend/resources.py` | 持有持久存储（SQLite store/checkpointer）、检查点、产物路径、`CompositeBackend` 路由 | `ResourceConfig`、`AgentResources` |
| **Tools** | `backend/tools.py` | 暴露可调用能力，不绑定单一 runner | `ToolCatalog`、`ToolHandler`、`parse_document_with_mineru`、`default_tool_catalog` |

> DeepAgents 在此仓库是**可插拔的 Brain / 子 Harness**，由 `BrainFactory` Protocol 注入，`self_check.py` 用 `_FakeBrain` 证明其可被替换。没有 `backend/__init__.py` 装配层。

### 五大边界协作（运行时数据流）

一次完整运行（入口 `run_session` → `HarnessRuntime.run_turn`）：

```
run_session(message, session_id)
  └─ with AgentResources() 装配资源 (三 SQLite 库 + CompositeBackend)
     └─ create_mineru_harness(resources).run_turn(message, session_id)
        ├─① ensure_session(session_id)              确保会话存在
        ├─② emit_event("user_message")              写入用户事件（append-only）
        ├─③ context_window(session_id)              从事件历史派生最近 20 条消息
        ├─④ brain_factory.create(...)               注入 middleware + tools + 后端
        ├─⑤ brain.invoke(...)                       请求执行（Hands 的 middleware 透传 trace 并写回事件）
        └─⑥ emit_event("assistant_message")         写回助手事件
        → return HarnessTurn(session_id, context, result)
```

要点：上下文窗口（步骤③）是从 append-only 事件历史**派生**的视图，派生前先写入了用户事件（步骤②），执行 trace 由 Hands 的 middleware 产生（步骤⑤内 emit），最终助手回复再写回事件（步骤⑥）。`brain.invoke` 前用 `RemoveMessage(REMOVE_ALL_MESSAGES)` 重置 langgraph 内部消息，再用 Session 派生的上下文重建——Session 是"单一事实源"而非 langgraph thread 状态。完整调用链与字段细节见 `backend/.planning/codebase/ARCHITECTURE.md` §4 与 `coding_maps/SYSTEM_MAP.md` §3。

---

## 3. 推荐理解方式

按下面顺序阅读，可最快建立对系统的整体认知（路径相对仓库根）：

1. **先看全局原则**：`AGENTS.md`（五大边界、运行时规则、简洁约束）——理解 Harness 设计意图与不可破坏的边界。
2. **再看系统架构**：本文件 §2——理解五大模块如何协作、调用链走向。
3. **接着看目录与模块清单**：`backend/.planning/codebase/STRUCTURE.md`——理解每个文件做什么、入口怎么用、资源目录如何约定。
4. **深入内部数据流与设计决策**：`backend/.planning/codebase/ARCHITECTURE.md`——理解 append-only 事件、Session≠上下文窗口、可恢复事件、薄 Harness、真实错误透传等关键决策的"为什么"。
5. **接口与集成**：`INTERFACES.md` + `backend/.planning/codebase/INTEGRATIONS.md`——理解 MinerU / DeepAgents / SQLite / Provider 边界。
6. **风险与维护**：`backend/.planning/codebase/CONCERNS.md`——理解外部依赖、安全、稳定性、范围蔓延等已知风险。
7. **跨系统全景**：`coding_maps/SYSTEM_MAP.md`——理解调用链全貌、provider 边界、按任务分类的阅读指南与集成风险清单。

---

## 4. 推荐目录职责

`backend/` 内每个文件的一句话职责（细节见 `backend/.planning/codebase/STRUCTURE.md` §2）：

| 文件 | 职责 |
|------|------|
| `backend/session.py` | append-only 事件存储 + 上下文窗口派生 + 最小 runner；导入时 `load_dotenv` 加载 `backend/.env` |
| `backend/harness.py` | 读历史 → 派生上下文 → 请求执行 → 写回事件（Brain 工厂 + 单轮运行时） |
| `backend/hands.py` | 用 middleware 暴露 model/tool trace 并透传真实错误 |
| `backend/resources.py` | 持有 SQLite store/checkpointer + `CompositeBackend` 路由；`data_dir` 锁定在 `backend/data/` |
| `backend/tools.py` | MinerU 解析工具与工具注册（`ToolCatalog`） |
| `backend/self_check.py` | 端到端自检五大边界与约束（FakeBrain，可作 `python self_check.py` 运行） |

**非产品知识目录**（不纳入架构理解，修改时不必联动本文档）：

- `scripts/ralph/` —— 被 `.gitignore` 忽略的自动化脚本。
- `backend/instantclient/` —— Oracle Instant Client 19.31（Windows 二进制依赖产物，已提交进 git，无任何 Python 代码 import 它，与 MinerU 里程碑无关）。
- `.agents/`、`.codex/`、`.review-push/` —— 本地 agent / 工具配置元数据。

**运行时产物**（`.gitignore` 忽略，首次运行自动创建，不入库）：`backend/data/`（三 SQLite 库 + artifacts + MinerU 输出）、`.venv/`、`.env`、`__pycache__/`。

---

## 5. 系统级维护建议

改动 `backend/` 时须保护以下系统级边界与约定（提炼自根 `AGENTS.md` 与事实文档）：

- **保护五大边界**：任何新增抽象必须保护 Session / Harness / Hands / Resources / Tools 之一，否则应删除。不要把能力硬编码到某个 runner、容器、模型或工作流。
- **Session 是 append-only 单一事实源**：`emit_event` 只做 `insert`，从不 update/delete。任何派生视图（上下文窗口、摘要）都可从原始事件重建，但不可替代 raw events。改动持久化时不得破坏"事件不修改/不删除"。
- **Session ≠ 上下文窗口**：`context_window()` 是给模型看的裁剪视图，不是真相。不要把裁剪视图当事实源。
- **保持 Harness 薄**：`HarnessRuntime.run_turn` 维持少量清晰步骤。在真实 caller 需要前，不增加服务层、容器、鉴权、策略框架或工作流引擎。
- **真实错误透传**：`TraceMiddleware` 在 `except` 中 emit `*_error` 事件后 `raise`，`self_check.py` 断言错误必须穿透。改动错误处理时不得吞掉异常或包装失真。
- **Tools 不绑定 runner**：工具能力可被任意 Brain 复用，经 `ToolCatalog` 注入。新增工具应走同一注册机制。
- **Brain 可替换**：`BrainFactory` 是 Protocol，DeepAgents 并非硬绑定。改动 Brain 相关代码时保持 Protocol 边界，不要把 DeepAgents 耦合成唯一实现。
- **资源路径与 CWD 无关**：`resources.py` 用 `_BACKEND_DIR = Path(__file__).resolve().parent` 把数据目录锁定在 `backend/data/`，脚本可从任意工作目录运行。
- **改代码后同步事实层**：修改 `backend/` 实现后，应同步更新 `backend/.planning/codebase/` 下对应事实文档，再视影响范围回看本文件与 `INTERFACES.md`、`coding_maps/SYSTEM_MAP.md`。

> 接口明细、Provider 边界、未证实关系与扩展入口见 `INTERFACES.md`。
