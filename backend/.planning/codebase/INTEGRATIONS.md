# 外部/系统集成 (INTEGRATIONS)

本文件归纳 `backend/` 与外部系统、文件系统、持久层的集成边界。事实均来自代码。

## MinerU 异步任务 API 契约
实现于 `backend/tools.py`，`MINERU_BASE_URL = "http://10.11.0.110:6006"`。流程为三步异步任务：

1. 提交 — `POST /tasks`，`requests` 以 multipart 字段 `files` 上传源文件；表单 `data` 含固定参数：
   - `backend = "hybrid-engine"`
   - `effort = "high"`
   - `return_md = "true"`
   - `response_format_zip = "false"`
   提交超时 60s。响应中用 `_find_value` 在 `{"task_id","taskId","id"}` 中查找任务 id。
2. 轮询 — `GET /tasks/{task_id}`，超时 30s。状态键取 `{"status","state"}`（忽略大小写）。
   - 成功集合 `SUCCESS_STATES = {completed, complete, done, finished, success, succeeded}`。
   - 失败集合 `FAILURE_STATES = {failed, failure, error, errored, cancelled, canceled}` → 立即 `raise RuntimeError`。
   - 轮询间隔默认 2.0s，总超时默认 900s（`timeout_seconds`）。
3. 取结果 — `GET /tasks/{task_id}/result`，超时 120s；用 `_find_value` 在 `{"md","markdown","md_content","markdown_content"}` 中提取 markdown 文本，写入本地 `.md`。

固定参数 `backend=hybrid-engine`、`effort=high` 不可由用户配置（AGENTS.md 明确约束）。

## 文件系统工件路径约定
- 工件根：`data/artifacts/`（`ResourceConfig.artifacts_dir`）。
- 会话事件溢出：当 payload > `max_inline_bytes`（默认 262144）时，落盘到 `data/artifacts/session-events/{uuid.uuid4().hex}.json`，DB 仅存 `{"artifact_path","bytes"}`（`backend/session.py`）。
- MinerU 默认输出：`data/mineru_outputs/{stem}.md`（`backend/tools.py::_default_output_path`）。

## DeepAgents 内建虚拟文件系统
`FilesystemBackend(root_dir=data/artifacts, virtual_mode=True)`，路由 `/artifacts/`、`/large_tool_results/`。AGENTS.md 要求复用该内建虚拟文件系统，不另加包装。

## 持久化存储位置
均位于 `data/`（`ResourceConfig.data_dir = Path("data")`）：
- `data/dsagents_sessions.db`（自建会话事件）
- `data/dsagents_store.db`（`SqliteStore`）
- `data/dsagents_checkpoints.db`（`SqliteSaver`）
三者均在 `AgentResources.__enter__` 中 `mkdir(parents=True, exist_ok=True)` 后建库、`setup()`。

## Provider / 模型配置
- 环境变量 `DSAGENTS_MODEL`，缺省 `openai:gpt-5.5`，传入 `create_deep_agent(model=...)`（`backend/harness.py`）。
- 调用配置：`config={"configurable": {"thread_id": session_id}}`；每轮先用 `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 清空再回放上下文（`backend/harness.py`）。
- 实际 provider 是否可达、API key 来源当前代码未确认（代码不读 `.env`，无显式鉴权字段）。

## 稳定 vs 非稳定边界
- 稳定（AGENTS.md 钦定五大模块边界）：`Session`、`Harness`、`Hands`、`Resources`、`Tools`；MinerU 固定参数；`CompositeBackend` 路由表；SQLite 三库布局；`DSAGENTS_MODEL` 约定。
- 非稳定/未确认：LLM provider 实际可用性；MinerU 内网地址 `10.11.0.110:6006` 可达性依赖网络环境。

## 明确不存在的集成
按 AGENTS.md 的 Simplicity Constraint 与代码观察：
- 无鉴权 / 认证（MinerU 与模型调用均无 auth header、无 token 处理）。
- 无容器（Docker 等）与编排。
- 无 Web 前端 / HTTP 服务层（仅 CLI 入口）。
- 无服务层、策略框架、工作流引擎。
- 中间件只记录模型可见消息、工具调用、工具结果、最终答案；不打印或持久化隐藏思维链（AGENTS.md Runtime Rules）。
