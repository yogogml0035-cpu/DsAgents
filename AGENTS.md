# AGENTS — DsAgents

> 事实来源：backend/.planning/codebase/ 与 coding_maps/SYSTEM_MAP.md（2026-07-02 生成）

## 1. 仓库定位

DsAgents 是一个 **agent 运行时底座**：把能力（Brain、执行器、工具）做成可插拔，而不被硬编码到某个 runner、容器、模型或工作流。当前为单子项目仓库，唯一产品子项目是后端模块 `backend/`。

**模块组织**：`backend/` 采用**扁平顶层模块**（无包前缀），模块间用绝对导入（`from hands import ...`）。直接运行 `backend/` 内的脚本即可，例如 `python backend/session.py`；或把 `backend/` 加入 `PYTHONPATH` 后 `from session import run_session`。

**技术栈**：Python（≥3.11）；DeepAgents（可插拔 Brain）+ LangGraph（图运行时/checkpoint/store）+ LangChain（中间件/消息，含 `langchain-openai` 走 OpenAI 兼容协议接入 MiniMax）；持久化用本地 SQLite 三库 + 文件系统产物目录；HTTP 走 `requests` 调 MinerU；配置用 `python-dotenv` 加载 `.env`。依赖与版本见 `backend/.planning/codebase/STACK.md`。

**包管理器**：`uv`（项目元数据在 `backend/pyproject.toml`，锁文件 `backend/uv.lock`；`backend/` 是可安装包 `dsagents`）。仓库根的 `requirements.txt` 已废弃删除。

## 2. 文档分层规则

本仓库文档分四层，各司其职，避免把实现细节堆进导航层：

| 层级 | 文件 | 职责 |
|------|------|------|
| 导航 / 入口 | 本文件 `AGENTS.md` | 全局原则、阅读顺序、维护规则、推荐入口（**不堆实现细节**） |
| 系统架构 | `ARCHITECTURE.md` | 系统边界、子系统职责、理解路径、稳定目录职责、系统级维护约定 |
| 接口 / 集成 | `INTERFACES.md` | 已确认接口边界、未证实跨系统关系、任务排查建议、可扩展集成入口 |
| 跨项目地图 | `coding_maps/SYSTEM_MAP.md` | 子项目职责表、完整调用链、provider 边界、按任务阅读指南、集成风险清单 |
| 子项目事实 | `backend/.planning/codebase/` | backend 内部架构事实、目录与模块、集成明细、技术栈、约定、风险 |

> 原则：根级只放导航与稳定全局原则；实现细节、接口明细、目录清单留在更下层文档。改代码后同步事实层。

## 3. 核心原则（全局人工约束）

以下原则对全仓库每次任务都适用，须严格遵守。

- **能力可插拔**：Brain（如 DeepAgents）、执行器、工具做成可插拔；项目自身拥有 Session、事件、资源、工具路由与运行时状态，不被硬编码到某个 runner、容器、模型或工作流。
- **Session 是事件源，不是上下文窗口**：Session 以 append-only 事件存完整持久任务事实。摘要或裁剪视图可作为事件追加，但不得替代原始事件作为事实源。
- **保持运行时薄**：读 Session 历史、派生上下文窗口、请求执行、写回结果事件。在真实 caller 需要前，不增加服务层、策略框架、工作流引擎、容器或宽泛的安全/配置系统。
- **真实错误透传**：暴露模型/工具执行 trace 并把真实错误向上传，不吞异常、不包装失真。
- **简单性约束**：优先删减范围而非增加旋钮。

> 完整阐述与"为什么"见 `ARCHITECTURE.md` §5 与 `backend/.planning/codebase/ARCHITECTURE.md` §4。

## 4. 首个里程碑

最小可运行 DeepAgents 解析演示已交付（MinerU 工具 / DeepAgents 工厂 / CompositeBackend / 最小 session runner）。实现状态详见 `backend/.planning/codebase/ARCHITECTURE.md` §5。

## 5. 运行时规则

MinerU 走 `http://10.11.0.110:6006` 异步任务三步 API，固定参数 `backend=hybrid-engine`、`effort=high`（本里程碑不可配）；持久历史走 StoreBackend + 本地 SQLite；大产物落 `backend/data/artifacts/`；middleware 只记录模型可见层，**不得碰隐藏思维链**。

> 完整调用流程、三 SQLite 库与 provider 边界见 `INTERFACES.md` §1。

## 6. 按任务分类的阅读顺序

每个任务先读对应事实文档（路径相对仓库根），再到系统层定位边界：

| 任务类型 | 先读 |
|----------|------|
| **改 backend 代码（业务/存储/runner）** | `backend/.planning/codebase/ARCHITECTURE.md`、`STRUCTURE.md`；改持久化回看本文件 §3 的 Session 原则 |
| **改 MinerU 工具 / DeepAgents Brain** | `backend/.planning/codebase/INTEGRATIONS.md`、`STACK.md`；协议字段冲击见 `INTERFACES.md` §1 |
| **改集成 / Provider** | `INTERFACES.md`、`backend/.planning/codebase/INTEGRATIONS.md`；未证实关系见 `INTERFACES.md` §2 |
| **加新子项目（如 frontend）** | 本文件 §3、`ARCHITECTURE.md` §1、`coding_maps/SYSTEM_MAP.md` §4/§6 |

## 7. 维护规则

- **事实层在子项目**：`backend/.planning/codebase/` 是事实来源；根级只放导航和稳定全局原则。
- **改代码后同步事实层**：修改 `backend/` 实现后，先更新对应事实文档，再视影响回看 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`。
- **文档用简体中文**：保留代码标识符、文件路径、命令、配置键、API 名称、IP/端口原文。
- **不外泄密钥**：文档不写入任何密钥 / token / 连接串。
- **证据不足标注**：用"需确认 / 初步判断"表达，不写成硬规则。

## 8. 命令与入口

`backend/` 采用扁平顶层模块（无包前缀），模块间绝对导入（`from hands import ...`）。因为脚本所在目录会自动加入 `sys.path`，直接运行 `backend/` 内脚本即可让这些绝对导入正确解析。

```bash
# 安装依赖（在 backend/ 下，用 uv 同步）
cd backend && uv sync

# 端到端自检（FakeBrain，验证核心原则与约束，结尾打印 self-check passed）
python backend/self_check.py
# 或：cd backend && python -m self_check

# 通过 Python API 调用（无独立 CLI 入口）
cd backend && python -c "from session import run_session; run_session('帮我解析 xxx.pdf')"
```

> 注意：导入入口是 `from session import run_session`（扁平顶层，无 `backend.` 前缀）。`self_check` / `run_session` 等模块内部用绝对导入；从仓库根直接 `import session` 会失败，需先把 `backend/` 加入 `PYTHONPATH` 或在该目录下运行。
