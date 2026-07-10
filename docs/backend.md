# backend 架构与约定

## 核心变化

`backend/` 已切到 run-first：

- `POST /runs` 只接收当前 `messages[]`；每条 `content` 必须是 blocks。
- 发送给 Brain 的 payload 仍只包含当前请求里的消息数组；`artifact` block 会在进入 Brain 前转成文本路径提示。
- `thread_id=session_id` 交给 LangGraph `checkpointer` 维护短期上下文。
- 本地不再维护 session 事件事实源，不再回放 `context_window`，也不再发 `RemoveMessage(REMOVE_ALL_MESSAGES)`。
- 默认 DeepAgent 从 `/skills/` 挂载 Philips 外高桥与 Tecan 进口 Skill；四个临时 extractor 只负责独立抽取，确定性工具负责投票、canonical 与 Excel。
- A/B/C、裁决、canonical 与生成文件都写成唯一 artifact；缺信息时结束当前 run，下一 run 重新显式传路径和选择。

## 现在的主链

1. `POST /upload` 可选保存一个或多个文件，返回 `/artifacts/uploads/...` 路径。
2. `POST /runs` 创建 `run_id`，把 `messages[]` 序列化写入 `resources.runs.create_run(...)`。
3. 后台线程调用 `HarnessRuntime.execute_run(messages, ...)`。
4. `brain.stream(..., stream_mode=["messages","custom","values"], version="v2")` 产出 raw chunk。
5. `harness.py` 把 chunk 规范化成七类公开事件；subagent 模型 token 按 `lc_agent_name` 过滤，task 调用/结果仍保留。
6. `run_ledger.py` 记录快照、规范化事件和完整 raw。
7. `GET /runs/{run_id}` 按需返回 run 快照、增量 `events[]` 和当前最新的 `latest_content_event`。

## 模块分工

- `run_ledger.py`：`dsagents_runs.db`
- `harness.py`：stream 规范化和 Skills/Subagents 装配
- `hands.py`：最小 `ToolStatusMiddleware`
- `resources.py`：run ledger + checkpointer + store + `/artifacts/`/`/skills/` backend
- `api.py`：薄 HTTP 适配
- `tools.py`：MinerU/解压及八个业务工具注册
- `subagents.py`：四个临时 extractor
- `philips_wgq_import.py` / `tecan_import.py`：业务合同、投票和 Excel
- `artifact_names.py` — 上传/下载文件名清洗与去重（`clean_filename` / `make_unique_name` / `make_timestamped_name` / `has_upload_suffix`），被 `api.py` 的 `/upload`、`workflow_artifacts.unique_download_path`、`tools.py` 共同使用。
- `workflow_artifacts.py`：安全路径和 immutable JSON helper

## 数据与边界

- `backend/data/` 是固定数据根；`dsagents_runs.db`、`dsagents_checkpoints.db`、`dsagents_store.db` 都由运行时按需创建。
- 上传只负责保存文件并返回 artifact 路径；常见办公文件和任意图片都可以上传。是否能被解析或理解取决于 DeepAgents `read_file`、`parse_documents`、MinerU 和模型多模态能力。
- 业务产物统一落 `/artifacts/downloads/`，不覆盖上传原件或既有 JSON/Excel；模板位于 `backend/skills/*/assets/`。
- 长期文档只记录配置键和边界，不抄录本地 `.env` 的真实值。
- Philips 侧的法定单位查询（`philips_wgq_import.py` 的 `_oracle_units`）走 `oracledb` thick mode，依赖 `ORACLE_CLIENT_LIB_DIR` 指向外部 Oracle instant client 目录；缺失或失败时优雅降级（核注清单缺法定单位字段），不阻塞 Excel 生成。详见 `../backend/.planning/codebase/CONCERNS.md` §8。

## 已删除

旧 session 模块/表/端点已移除（见 [INTERFACES.md](../INTERFACES.md) §1）；commit `8890292`。

## 并发与恢复

- 同一 `session_id` 同时只允许一个 run，靠进程内 `threading.Lock`
- 进程启动时，遗留 `queued` / `running` run 会补记为 `failed("执行已中断，请重试")`
