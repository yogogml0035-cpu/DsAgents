# CONCERNS — backend/

> 事实来源：backend/ 源码、.env.example、git status、代码标注（2026-07-02 生成）
> 说明：本文件只引用 .env.example 的配置键名，不包含任何真实凭据/连接串。

---

## 1. 外部依赖与可用性风险

### 已确认
- **MinerU 服务地址硬编码内网 IP**：`tools.py:12` `MINERU_BASE_URL = "http://10.11.0.110:6006"` 为模块级常量，直接拼接到 `tools.py:64`（`POST /tasks`）、`tools.py:86`（`GET /tasks/{id}`）、`tools.py:93`（`GET /tasks/{id}/result`）。该地址仅在内网可达，部署到其它网络即不可用。
- **环境变量键名是死代码**：`.env.example` 定义了 `MINERU_BASE_URL=`、`MINERU_BACKEND=`、`MINERU_TIMEOUT_SECONDS=`，但 `tools.py` 全程未调用 `os.getenv` 去读取它们——地址、参数均硬编码。配置项与实现脱节。
- **固定参数耦合**：`tools.py:66-70` 写死 `backend="hybrid-engine"`、`effort="high"`、`return_md="true"`、`response_format_zip="false"`。根 AGENTS.md 明确声明这两个参数在本里程碑"固定且不可配置"，属刻意约束，但任何 MinerU 协议变更都会直接破坏解析。
- **服务不可用时为硬失败、无重试/降级**：`requests` 抛 `ConnectionError`/`HTTPError`（`tools.py:74,87,94`），轮询超时抛 `TimeoutError`（`tools.py:97`，默认 900s）。异常经 `TraceMiddleware` 记为 `tool_error` 事件后重新抛出（hands.py:60-66）。符合根 AGENTS.md"真实错误透传"，但对短暂网络抖动无重试、无兜底。
- **明文 HTTP**：`http://10.11.0.110:6006` 无 TLS，传输内容（含上传的文档文件）不加密。内网场景可接受，跨网段需注意。

### 需确认/疑似
- MinerU 响应字段用 `_find_value` 递归模糊匹配（`tools.py:107-121`），兼容了 `task_id/taskId/id`、`status/state`、`md/markdown/md_content/markdown_content` 多种命名。**疑似**：若 MinerU 返回结构稳定，此模糊匹配会掩盖真实字段名错误；需确认是否已对齐实际 API 文档。

---

## 2. 安全/密钥风险

### 已确认
- **`.env` 与 `.venv` 已正确忽略**：`.gitignore` 列出 `.env`、`backend/.env`、`.venv/` 等，未发现真实凭据被跟踪。本文件未读取 `backend/.env` 内容。
- **`.env.example` 仅暴露键名、值为空**：含 `DEEPSEEK_API_KEY`、`MINIMAX_API_KEY`、`LANGSMITH_API_KEY`、`ORACLE_PASSWORD` 等键名，值均为空，属标准模板做法。
- **错误事件可能携带敏感信息**：`hands.py:41,64` 把 `repr(exc)` 写入 `model_error`/`tool_error` 事件并持久化到 SQLite。`repr` 可能包含 URL/请求头片段；当前无脱敏。
- **导入即加载 .env**：`session.py:15` 在模块导入时 `load_dotenv(Path(__file__).with_name(".env"))`，把凭据写入进程环境变量；`DeepAgentsBrainFactory`（harness.py:44-47）再把 `MINIMAX_API_KEY` 复制为 `OPENAI_API_KEY`。多了一个环境变量泄漏面。**注意**：`backend/` 没有 `__init__.py`，`.env` 加载发生在 `session` 模块导入时（而非"导入 `backend` 包"时）。

### 需确认/疑似
- `LANGSMITH_API_KEY` 键存在但 `LANGSMITH_TRACING=false`（.env.example）；项目源码未见任何 LangSmith 接入代码。**疑似**未启用即可，但若误开启会把 trace 上传外部服务。

---

## 3. 稳定性/技术债

### 已确认
- **首个里程碑早期，范围刻意最小**：仅 1 个工具 `parse_document_with_mineru`（`tools.py:133-134`），1 个 Brain 工厂 `DeepAgentsBrainFactory`，无 stub/占位代码。无 `TODO/FIXME/XXX/HACK` 标注（项目源码无命中）。
- **错误处理一致**：`TraceMiddleware` 对 model/tool 调用统一 try/except，记事件后 re-raise；`self_check.py` 用断言验证"错误必须穿透"。无吞异常。
- **无异步/同步混用**：项目源码全同步（`async`/`await`/`asyncio` 在项目 py 中无命中）。MinerU 工具用同步 `requests` + 阻塞 `time.sleep` 轮询（tools.py:96）。根 AGENTS.md 称"async task APIs"，但当前实现是同步轮询——一致性 OK，但长任务（最长 900s）会阻塞调用线程。
- **遗留未使用 import**：`session.py:3` `import argparse` 未被使用（`main()` 硬编码 `message = "你好"`，不解析命令行）。属轻微噪音，不影响运行。
- **事件恢复机制存在但有边界**：
  - `context_window`（session.py:150-163）仅从 `user_message`/`assistant_message` 事件重建消息（session.py:229-237），工具/模型中间事件不回放到上下文。
  - `CONTEXT_MESSAGE_LIMIT = 20`（session.py:54）+ 裁剪到首个 user 消息（session.py:157），长会话会被截断。
  - 中间状态依赖 LangGraph `SqliteSaver` checkpointer（resources.py:50-53）。事件恢复"可恢复"但非完整回放。

### 需确认/疑似
- `_safe`（session.py:240-249、hands.py:83-92）对未知类型 `repr(value)` 兜底。**疑似**：若 payload 含不可 JSON 序列化的自定义对象，`repr` 字符串可能很大或泄露内部结构；建议确认实际 payload 类型范围。

---

## 4. 可观测性

### 已确认
- **middleware 仅记录模型可见层，符合根 AGENTS.md 边界**：`TraceMiddleware`（hands.py）记录 `model_request`/`model_response`（`request.messages`、`response.result`）、`tool_request`/`tool_response`、`model_error`/`tool_error`。**不触碰隐藏思维链**——只取模型可见消息与工具结果。
- **事件 append-only + 大负载外溢**：`emit_event`（session.py:114-148）超 `max_inline_bytes`（默认 256KB，session.py:56）时写文件、DB 存 `artifact_path`，读回时透明还原（session.py:165-179）。trace 完整性有保障。
- **控制台输出原始**：`hands.py:44,72` 仅 `print(f"[model] ...")`/`print(f"[tool] ...")`，无日志级别、无结构化、无耗时指标。
- **LangSmith tracing 默认关闭且无接入代码**（见 §2）。

### 需确认/疑似
- trace 未记录任何耗时/字节量指标（除 `parse_document_with_mineru` 返回 `markdown_bytes` 外）。**疑似**：难以定位慢调用（轮询、上传、结果拉取各自耗时不可见）。

---

## 5. 范围蔓延风险

### 已确认（当前无违规）
- **严格遵守根 AGENTS.md 简单性约束**：无 service layer、无 container、无 auth、无 policy framework、无 workflow engine。仅维护 Session/Harness/Hands/Resources/Tools 五个模块边界 + 可插拔 `Brain`/`BrainFactory` Protocol（harness.py:26-38）。无多余抽象。

### 需确认/疑似（早期漂移信号）
- **Oracle 预埋与 MinerU 里程碑无关**：`.env.example` 含 `ORACLE_DSN`/`ORACLE_USERNAME`/`ORACLE_PASSWORD`/`ORACLE_CLIENT_LIB_DIR`/`ORACLE_TIMEOUT_SECONDS` 五个键，但项目 Python 源码**零** Oracle 引用（`oracle/cx_Oracle/oracledb/ORACLE` 在项目 py 中无命中）。配置键已先于实现进入仓库——属疑似范围蔓延的前兆，需确认是否有未列入里程碑的 Oracle 数据源需求。

---

## 6. 跨平台

### 已确认
- **Windows 环境为主**：`backend/instantclient/instantclient_19_31/` 全是 `.dll`/`.exe`/`.jar`（Oracle 19c Windows 客户端）。
- **`instantclient` 与 MinerU 无关且是已跟踪的仓库膨胀**：该目录所有二进制（oci.dll、oraocci19.dll、ojdbc8.jar 等）**已提交进 git**。无任何 Python 代码 import 它（见 §5）。属无关产物，增加 clone/仓库体积，建议从版本控制移除或改 git-lfs/外部依赖。
- **路径处理跨平台友好**：`resources.py`、`tools.py` 统一用 `pathlib.Path`，`tools.py:33,37` 用 `.expanduser().resolve()`。
- **SQLite 存储跨平台 OK**：`SqliteSessionStore`/`SqliteStore`/`SqliteSaver` 均用本地文件路径（resources.py），Windows 可用。

### 需确认/疑似
- **`.venv` 启动器在沙箱/CI 中可能报错**：项目 `.venv/` 为 Windows 风格（`.venv/Scripts/`）。在非 Windows 沙箱或受限制环境里，直接调用 `.venv` 内的 python 启动器可能失败；建议用 `uv sync` 重建干净 venv 而非依赖已提交的 `.venv`。`self_check.py`、`session.py` 依赖直接运行脚本（`python session.py`）或 `python -m <module>`，需确保从正确的解释器启动。
