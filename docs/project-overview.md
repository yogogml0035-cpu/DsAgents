# 项目总览

> 根级 AGENTS.md 的详情文档之一。事实来源：`backend/.planning/codebase/` 与 `coding_maps/SYSTEM_MAP.md`（2026-07-02，本轮刷新）。

## 仓库定位

DsAgents 是一个 **agent 运行时底座**：把能力（Brain、执行器、工具）做成可插拔，而不被硬编码到某个 runner、容器、模型或工作流。当前为单子项目仓库，唯一产品子项目是后端模块 `backend/`。

## 模块组织与导入

`backend/` 采用**扁平顶层模块**（无包前缀），模块间用绝对导入（`from hands import ...`）。直接运行 `backend/` 内的脚本即可，例如 `python backend/session.py`；或把 `backend/` 加入 `PYTHONPATH` 后 `from session import run_session`。

## 技术栈

- **Python**（≥3.11）；**DeepAgents**（可插拔 Brain）+ **LangGraph**（图运行时/checkpoint/store）+ **LangChain**（中间件/消息，MiniMax 通过 Anthropic 兼容协议接入）。
- 持久化用本地 **SQLite 三库** + 文件系统产物目录。
- 通用文档解析工具通过 `requests` 调当前 provider。
- 配置用 `python-dotenv` 加载 `.env`。
- 依赖与版本见 `backend/.planning/codebase/STACK.md`。

## 包管理器

`uv`（项目元数据在 `backend/pyproject.toml`，锁文件 `backend/uv.lock`；`backend/` 是可安装包 `dsagents`）。仓库根的 `requirements.txt` 已废弃删除。

## 文档分层规则

本仓库文档分四层，各司其职，避免把实现细节堆进导航层：

| 层级 | 文件 | 职责 |
|------|------|------|
| 导航 / 入口 | 根 `AGENTS.md` | 全局原则、阅读顺序、维护规则、推荐入口（**不堆实现细节**） |
| 系统架构 | `ARCHITECTURE.md` | 系统边界、子系统职责、理解路径、稳定目录职责、系统级维护约定 |
| 接口 / 集成 | `INTERFACES.md` | 已确认接口边界、未证实跨系统关系、任务排查建议、可扩展集成入口 |
| 跨项目地图 | `coding_maps/SYSTEM_MAP.md` | 子项目职责表、完整调用链、provider 边界、按任务阅读指南、集成风险清单 |
| 子项目事实 | `backend/.planning/codebase/` | backend 内部架构事实、目录与模块、集成明细、技术栈、约定、风险 |
| 根级详情 | `docs/*.md` | 本目录：把 AGENTS.md 中过长但仍有用的全局说明沉淀为独立详情文件 |

> 原则：根级只放导航与稳定全局原则；实现细节、接口明细、目录清单留在更下层文档。改代码后同步事实层。

## 运行时规则

- 文档解析工具在调用 `parse_document` 时读取 `MINERU_BASE_URL`、`MINERU_BACKEND`、`MINERU_EFFORT`、`MINERU_TIMEOUT_SECONDS`；当前 provider 细节见 `INTERFACES.md` §1。
- 持久历史走 StoreBackend + 本地 SQLite；大产物落 `backend/data/artifacts/`。
- middleware 只记录模型可见层，**不得碰隐藏思维链**。

> 完整调用流程、三 SQLite 库与 provider 边界见 `INTERFACES.md` §1 与 `coding_maps/SYSTEM_MAP.md` §3/§5。
