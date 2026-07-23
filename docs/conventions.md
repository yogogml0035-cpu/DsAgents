# 核心原则与维护规则

> 根级 `AGENTS.md` 的详情文档。实现事实在 `backend/.planning/codebase/`；本文放全局、长期有效的工程约束。

## 核心原则

- **能力可插拔，但运行时保持薄**：Brain、执行器、工具可注入；项目自身只拥有 run、事件、资源、工具路由与必要运行时状态。没有真实调用方前，不增加服务层、策略框架、任务队列、通用 workflow 引擎或宽泛配置体系。
- **Protocol 不泛化**：`typing.Protocol` 只用于 `Brain` / `BrainFactory`。工具用 callable + `ToolCatalog`，资源与 ledger 用具体类；单实现不新建 Protocol/ABC。
- **run-first**：`run_events` append-only，`runs` 是投影；最终业务 JSON 走 `run.result`。`session_id` 只作 LangGraph `thread_id` 与进程内单飞锁，不做 session API 或业务状态。
- **单一状态归属**：同票渠道抽取在单一 run 内完成。run ledger 已保存外部终态，checkpointer 已保存图上下文；不要额外增加消息表、任务状态、候选库、跨 run 恢复或 Tecan SubAgent 编排，除非出现明确消费者和唯一持久化归属。
- **源码布局稳定**：产品代码只在 `backend/api.py`、`runtime/`、`integrations/`、`skills/`；历史 `backend/build/` 产物不是源码，也不进入 VCS。

## 渠道供应链合同

- Philips 与 Tecan header 各自独立，`items[]` 共用完整 24 字段。每个返回商品行全字段出现，未知是 `null`，不以空字符串代替。
- 不输出 `shipment`、Excel、候选列表、置信度、审计细节或 OMS 自动保存结果。
- 本票交易/运输事实优先于主数据；主数据只能按唯一、明确的非语义标识补齐，不得覆盖本票数量、金额、重量、编号或运单。
- 发票按上传顺序和原始行顺序；同 12NC 默认不合并；多发票/多运单按材料顺序以英文逗号连接。
- `success` 无未解决业务缺失；`partial_success` 仅用于核心事实已确认、补充字段缺失；`input_problems` 用于票次/核心事实无法确认，仍返回完整 header、已证实 items（可空）与 `problems`。
- 渠道 Skill 只解析 PDF/XLSX；ZIP、DOCX、图片内容不解析，需在材料足够时列入 `problems` 后继续。

## 工具与 Skill

- 每个业务 Skill 只保留一个下划线命名、可 import 的 Python 包；包内含 `SKILL.md` / 按需 references（挂载 `/skills/`）、schema 和 scripts；新增时同步 `pyproject.toml` package-data。
- 工具在对应 import 包的 `scripts/tools.py` 定义，在 `runtime/tools.py` 静态注册；当前恰 5 个，不做扫描/动态 loader。
- WGQ workflow 缩窄工具表必须用 denylist 排除 Tecan finalizer，同时保留共享 `parse_documents`、`extract_archives`、`inspect_supply_chain_workbooks` 和 Philips lookup；DK workflow 用 denylist 排除 Philips lookup，保留共享工具和 Tecan finalizer；禁止业务-only allowlist。
- DK 终态通过 `finalize_tecan_overseas_recognition` 校验并投影到 `run.result`，不生成 Excel。XLSX inspection 只读并写中间 JSON artifact。

## middleware 约定

- 运行时 middleware 集中于 `runtime/middleware.py`。跨模型/工具的观测、循环检测、兼容性放这里；业务字段裁决和渠道 outcome 不放全局 middleware。
- `StructuredOutputRecovery` 是 WGQ 专用 class-based `after_model` hook。它必须保留 `can_jump_to` 含 `"end"`，耗尽时显式 `jump_to: "end"`，不能只返回 `None`。
- 空 data 壳只可凭同回合 `ToolMessage.tool_call_id` 匹配同一 AIMessage；合法文本 JSON 才能恢复。空壳耗尽保留 all-null `partial_success` + runtime problem 技术 fallback，其他未恢复的 Philips structured response 使 run 失败。
- DK/普通 run 用 `runtime_middlewares(structured_schema=None)`，不按 Philips schema 做文本恢复。Tecan finalizer 是更窄的业务校验边界。
- LangChain middleware 顺序：`before_*` 正序、`after_*` 逆序、`wrap_*` 外层先入后出；需最后处理 response 的 recovery 放在列表最前。

## HTTP、事件与存储

- HTTP 固定四端点，禁止重引入 SSE、session CRUD 或旧顶层辅助模块。
- 事件固定 7 类：`status`、`tool_execution`、`tool_progress`、`thinking`、`text_delta`、`assistant_message`、`model_usage`。
- OMS 旁路索引在 HTTP `create_run` 成功后 best-effort 写 JSONL；非 `run_events`、无查询 API、失败不阻塞 run。
- SQLite 的 run/checkpoint/store 三库分离；时间戳统一 UTC+8 本地 `YYYY-MM-DD HH:MM:SS`。

## 文档与验证

- backend 代码变更后，先更新 `backend/.planning/codebase/`，再更新根级 `ARCHITECTURE.md`、`INTERFACES.md`、`coding_maps/SYSTEM_MAP.md` 与相关说明。
- 长期文档用简体中文，保留标识符、路径、命令和配置键；不记录密钥、`.env` 值或私有连接串。
- 测试是 `cd backend; python -m tests.<name>` 的 assert 脚本，不是 pytest。真实模型、MinerU、Oracle、外部 HTTP 与本地回归分开。
- 修改 recovery 跑 `test_harness`；改 workflow 工具表跑 `test_workflow_setup`；改渠道最终合同跑 Philips/Tecan 测试；最后全量七脚本和根目录 `git diff --check`。

本地默认门禁（七脚本）：

```powershell
cd backend
uv sync
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
```

完整命令、真实集成开关与启动方式见 [commands.md](commands.md)。
