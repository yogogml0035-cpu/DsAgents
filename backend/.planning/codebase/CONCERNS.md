# CONCERNS — backend/

> 事实来源：backend/ 源码 + .env.example + git（2026-07-03 刷新）
> 本文件只引用配置键名，不写任何真实凭据/连接串。

状态图例：`已接受` = 有意的设计取舍；`待修` = 真正技术债；`需确认` = 信息不足，待人工裁决。

---

## 1. 外部依赖

### MinerU 内网 + 明文 HTTP，无重试/无降级 · `已接受`
- 是什么：`parse_document` 通过 `MINERU_BASE_URL`（`.env.example:11` 默认 `http://10.11.0.110:6006`）调用内网 MinerU；`.env.example:11` + `tools.py:43-49`。
- 影响：仅内网可达；明文 `http://` 传输（含上传文档）不加密；网络抖动直接硬失败。
- 验证入口：`grep -n "http://" backend/.env.example`；`tools.py:91-110`（`requests.post`）、`tools.py:113-134`（`requests.get` 轮询）。

### `_required_env` 缺失抛 `RuntimeError`，延迟到工具调用路径 · `需确认`
- 是什么：`MINERU_BASE_URL/BACKEND/EFFORT/TIMEOUT_SECONDS` 缺失时 `tools.py:84-88` 抛 `RuntimeError`；`MINERU_TIMEOUT_SECONDS` 非法时 `int(...)` 暴露原生 `ValueError`。
- 影响：普通聊天和 `create_harness(...)` 不预检；配置错误推迟到首次 `parse_document` 才暴露。
- 验证入口：`tools.py:43-46`；`self_check.py:121-129` 仅断言缺 `MINERU_BASE_URL` 时的 fail-fast。

### MinerU 协议字段写死 + `_find_value` 递归模糊匹配 · `需确认`
- 是什么：`return_md="true"`/`response_format_zip="false"` 硬编码（`tools.py:99-101`）；`_find_value`（`tools.py:144-158`）递归匹配 `task_id/taskId/id`、`status/state`、`md/markdown/md_content/markdown_content`。
- 影响：MinerU 返回结构若稳定，模糊匹配会掩盖真实字段名错误；任何协议字段变更直接破坏解析。
- 验证入口：`tools.py:144-158`、`tools.py:161-167`。

### MiniMax 配置 fail-forward，诊断推迟到首次调用 · `待修`
- 是什么：`DeepAgentsBrainFactory.__init__`（`harness.py:55-60`）执行 `init_chat_model(f"anthropic:{os.getenv('MINIMAX_MODEL')}", api_key=os.getenv("MINIMAX_API_KEY"), base_url=os.getenv("MINIMAX_BASE_URL"), thinking={"type":"adaptive"})`，无默认值、无 fallback。
- 影响：三键任一缺失 → `os.getenv` 返回 `None` 原样传入，失败行为由 provider 决定（通常延迟到首次模型调用），启动期无明确校验。凭据单源、未复制到 `OPENAI_*`，泄漏面已收窄。
- 验证入口：`harness.py:54-60`；`self_check.py:105-119` 仅在 env 已设置时验证接线。

### LangSmith 默认关闭，但开启即上传 trace · `需确认`
- 是什么：`.env.example:22-25` 含 `LANGSMITH_TRACING=false` + `LANGSMITH_ENDPOINT/API_KEY/PROJECT`；backend 源码无显式 LangSmith 接入代码，由 `deepagents`/`langchain` 运行时按 env 自动开启。
- 影响：误置 `LANGSMITH_TRACING=true` 会把 trace 上传 `api.smith.langchain.com`。
- 验证入口：`grep -rin "langsmith" backend/*.py`（零命中）；`.env.example:22-25`。

---

## 2. 安全

### 无鉴权 · `已接受`（当前定位为内网/单租户）
- 是什么：`api.py` 全部端点无 `Depends`/`Authorization`/`api_key` 校验。
- 影响：任何能访问端口的客户端可调用 `/sessions/messages`、`/files` 上传、读取 `/runs/{run_id}`。
- 验证入口：`grep -n "Depends\|Authorization\|api_key" backend/api.py`（零命中）。

### 无 CORS 中间件 · `需确认`
- 是什么：`.env.example:27` 预置 `CORS_ORIGINS`，但 `api.py` 未 `add_middleware(CORSMiddleware...)`。
- 影响：浏览器前端跨域请求被浏览器拦截；预留键表明前端在规划中，但当前未生效。需确认是否应有 CORS。
- 验证入口：`grep -n "CORSMiddleware\|add_middleware" backend/api.py`（零命中）；`.env.example:27`。

### 无 TLS（MinerU 明文） · 见 §1。

### `repr(exc)` 含敏感信息无脱敏，持久化到 SQLite · `待修`
- 是什么：`hands.py:43,67` 把 `repr(exc)` 写入 `model_error`/`tool_error` 事件；`harness.py:172` 失败状态 `raw={"status":"failed","error":repr(exc)}`；经 `session.emit_*` 落 `dsagents_sessions.db`。
- 影响：`repr` 可能含 URL、请求头、部分凭据片段；`raw` 字段也会随 `emit_run_event` 一同持久化。当前无脱敏/截断。
- 验证入口：`hands.py:42-46`、`hands.py:66-71`；`harness.py:167-173`；`session.py:447-464`（`_store_blob` 落库/外溢）。

### 无上传大小限制 · `待修`
- 是什么：`/files`（`api.py:145-156`）用 `shutil.copyfileobj` 直接写盘，无 `MAX_UPLOAD_SIZE` / content-length 校验。
- 影响：可上传任意大小文件，磁盘/内存耗尽风险。
- 验证入口：`api.py:145-156`。

---

## 3. 稳定性

### 后台 run 进程内单飞，重启即 fail · `已接受`
- 是什么：`/sessions/messages/runs`（`api.py:106-130`）起 `threading.Thread(daemon=True)` 跑 `_run_background`；进程崩溃/重启时线程丢失，依赖 `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)`（`api.py:40`，`session.py:356-378`）在启动期把 `queued/running` 标记为 `failed`。
- 影响：后台 run 不会无限悬挂，但也无法跨进程恢复；任务丢失。
- 验证入口：`api.py:118-130`、`api.py:37-40`；`session.py:356-378`。

### 409 锁进程级（仅 app.state 内存） · `已接受`
- 是什么：`_acquire_session_run`/`_release_session_run`（`api.py:197-213`）用 `app.state.session_locks` + `active_runs`，单进程内存。
- 影响：多 worker / 多进程部署时锁失效（同一 session 可并发跑）。当前单 worker 下正确。
- 验证入口：`api.py:197-213`、`api.py:43-44`。

### append-only 非完整回放 · `已接受`
- 是什么：`context_window`（`session.py:200-213`）仅从 `user_message`/`assistant_message` 重建消息（`session.py:549-557`），工具/模型中间事件不回放；`CONTEXT_MESSAGE_LIMIT=20` 且裁剪到首个 user 消息。
- 影响：长会话被截断；工具结果不进入上下文重建（靠 LangGraph `SqliteSaver` checkpointer 兜底中间状态）。
- 验证入口：`session.py:200-213`、`session.py:549-557`；`resources.py:50-53`（checkpointer）。

### 超大 payload 外溢到文件 · `已接受`
- 是什么：`_store_blob`（`session.py:447-459`）超 `max_inline_bytes`（默认 256KB）写 `artifacts/{session,run}-events/*.json`，DB 存 `artifact_path`，读回透明还原。
- 影响：DB 不膨胀；但外溢文件无清理/无上限，长期累积。
- 验证入口：`session.py:117-124`、`session.py:447-464`。

---

## 4. 可观测性

### trace 仅模型可见层 · `已接受`（符合根 AGENTS.md 边界）
- 是什么：`TraceMiddleware`（`hands.py:25-82`）记录 `model_request/response`、`tool_request/response`、`model_error/tool_error`，不触碰隐藏思维链。
- 验证入口：`hands.py:32-77`。

### 无独立 logger、无 metrics · `已接受`（早期）
- 是什么：仅 `print(f"[model] ...")`/`print(f"[tool] ...")`（`hands.py:50,76`），无 `logging`、无级别、无耗时/字节量指标。
- 影响：难定位慢调用（上传/轮询/拉取各自耗时不可见，除 `parse_document` 返回 `markdown_bytes`，`tools.py:58`）。
- 验证入口：`grep -n "import logging\|logger" backend/*.py`（零命中）。

### 无 `/health` 端点 · `需确认`
- 是什么：`api.py` 无 `/health` 或 `/ready`。
- 影响：容器编排/负载均衡无探针。
- 验证入口：`grep -n "/health\|/ready" backend/api.py`（零命中）。

---

## 5. 范围蔓延

### Oracle 预埋 instantclient + 5 键，零引用 · `需确认`
- 是什么：`.env.example:16-20` 含 `ORACLE_DSN/USERNAME/PASSWORD/CLIENT_LIB_DIR/TIMEOUT_SECONDS`；`backend/instantclient/`（Oracle 19c Windows 客户端，`git ls-files` 命中 37 个文件）已入库；但 Python 源码零引用、`pyproject.toml` 未列 `oracledb`/`cx_Oracle`。
- 影响：配置键 + 二进制先于实现进仓库，属疑似范围蔓延前兆；clone 体积增大。
- 验证入口：`grep -rin "oracle\|cx_Oracle\|oracledb" backend/*.py`（零命中）；`grep -n "oracle" backend/pyproject.toml`（零命中）；`git ls-files backend/instantclient/ | wc -l` → 37。

### DeepSeek 预留 · `需确认`
- 是什么：`.env.example:2-4` 含 `DEEPSEEK_API_KEY/BASE_URL/MODEL`，但生产 brain 走 MiniMax→Anthropic 路径，源码未引用 DeepSeek。
- 验证入口：`grep -rin "deepseek" backend/*.py`（零命中）。

### CORS 预留前端 · `需确认`（见 §2 无 CORS）。

---

## 6. 跨平台

### instantclient Windows 二进制 · `需确认`
- 是什么：`backend/instantclient/instantclient_19_31/` 全 `.dll`/`.exe`/`.jar`（Windows）；无 Python import。
- 影响：非 Windows 环境下不可用（当前也无代码引用，故无运行时影响）；路径假设为 Windows。
- 验证入口：`git ls-files backend/instantclient/instantclient_19_31/ | head -5`。

### 路径/存储跨平台友好 · `已接受`
- 是什么：`resources.py`、`tools.py` 统一 `pathlib.Path` + `.resolve()`；三个 SQLite 库用本地文件（`resources.py:22-31`）。
- 验证入口：`resources.py:14`、`tools.py:68-82`。

---

## 7. 低风险已确认项

- **无 TODO/FIXME/XXX 残留**：`grep -n "TODO\|FIXME\|XXX" backend/*.py` 零命中。
- **`.env` / `.venv` / `data/` 正确忽略**：根 `.gitignore` 列出 `.env`、`backend/.env`、`.venv/`、`backend/data/`、`__pycache__/`、`*.pyc`。
- **`.env.example` 仅键名、值空**：标准模板，未跟踪真实凭据。
- **纯同步一致性 OK**：全同步 `requests` + 阻塞轮询，无 async/sync 混用陷阱；`api.py` 的同步端点用 `threading.Thread` 隔离后台 run。
- **错误处理一致**：`TraceMiddleware` 统一 try/except 记事件后 re-raise，无吞异常（`hands.py:40-46,64-71`）；`self_check.py:171-189` 断言"错误必须穿透"。
- **`/files` 文件名清洗防穿越**：`_clean_filename`（`api.py:262-266`）取 basename；`tools.py:71-77` 校验 `/artifacts` 路径拒绝 `..`（`self_check.py:418-423` 验证）。
- **无残留未用 import**：旧文档记载的 `session.py import argparse` 已清除（当前 `session.py:3` 为 `import json`）。
