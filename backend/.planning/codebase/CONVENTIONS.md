# CONVENTIONS — backend 编码与质量约定

> last_mapped_commit: 79f97d239243d0513de93f10224eef470fffd83c
> Analysis Date: 2026-07-24。以 `backend/` 权威源码为准；根级 `AGENTS.md` / `docs/conventions.md` 仅作交叉验证。禁止读 `.env` 值。

## 1. 源码布局与模块边界

- 产品代码只在 `backend/api.py`、`runtime/`、`integrations/`、`skills/`；包名发行为 `dsagents`（`pyproject.toml` `name`），**无**顶层 `dsagents/` 源码包、service 层、通用 workflow 引擎。
- `api.py`：四 HTTP 端点、请求/响应 Pydantic 模型、session 进程内单飞、usage 计价展示、OMS best-effort 旁路写点。
- `runtime/`：Brain 装配（`agent.py`）、Harness 执行（`execution.py`）、middleware、run ledger（`runs.py`）、资源挂载（`resources.py`）、工具目录（`tools.py`）、可观测归一化（`observability.py`）、OMS 日志（`oms_log.py`）。
- `integrations/`：artifact 路径与 MinerU HTTP 客户端等外部 I/O。
- `skills/`：业务 Skill 的下划线命名包；每个包同时含 `SKILL.md` / references、schema 与工具实现。
- 历史 setuptools 产物（如 `backend/build/`、`dist/`、`dsagents.egg-info/`）**不是**源码权威，不读进 VCS 决策。
- 能力可注入（`BrainFactory`、`ToolCatalog`、`harness_factory`），但运行时保持薄：没有真实调用方前不增加任务队列、策略框架或宽泛配置体系。

## 2. 命名风格

| 类别 | 约定 | 示例 |
|------|------|------|
| 模块 / 包 | `snake_case` | `runtime/execution.py`、`philips_wgq_inbound_recognition` |
| Skill 目录 | 下划线命名的 import 包，资源和代码同目录 | `philips_wgq_inbound_recognition/`、`tecan_import/` |
| 类 | `PascalCase` | `HarnessRuntime`、`SqliteRunLedger`、`ToolCatalog`、`DeepAgentsBrainFactory` |
| 函数 / 方法 | `snake_case` | `execute_run`、`default_tool_catalog`、`runtime_middlewares` |
| 常量 | `UPPER_SNAKE` | `WAG_WORKFLOW`、`DK_WORKFLOW`、`NO_PROGRESS_WINDOW`、`SKILLS_SOURCE` |
| 私有助手 | 单下划线前缀 | `_normalize_messages`、`_update_events`、`_WAG_EXCLUDED_TOOLS` |
| 虚拟 FS 路径 | 前导 `/` 的挂载前缀 | `/skills/`、`/artifacts/`、`/memories/` |
| workflow 字面量 | 固定大写渠道代码 | `WGQ`、`DK` |
| 工具函数名 | 与注册 callable `__name__` 一致 | `parse_documents`、`finalize_tecan_overseas_recognition` |
| 事件类型 | 固定小写下划线字符串（见 §10） | `tool_execution`、`model_usage` |
| 环境变量 | `UPPER_SNAKE` 前缀分组 | `MINIMAX_*`、`MINERU_*`、`ORACLE_*`、`DSAGENTS_*`（测试 opt-in） |

### 环境变量命名（代码只读键名，文档不写值）

| 前缀 / 键 | 用途 |
|-----------|------|
| `MINIMAX_MODEL` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` | Brain 默认模型（`runtime/agent.py` `load_dotenv`） |
| `MINERU_BASE_URL` / `MINERU_BACKEND` / `MINERU_EFFORT` / `MINERU_TIMEOUT_SECONDS` | MinerU HTTP 客户端 |
| `ORACLE_DSN` / `ORACLE_USERNAME` / `ORACLE_PASSWORD` / `ORACLE_CLIENT_LIB_DIR` / `ORACLE_TIMEOUT_SECONDS` | WGQ / DK 共享 12NC 主数据；Windows 随仓库 Instant Client 为 thick mode 默认回退，缺失优雅降级 |
| `DSAGENTS_*` | 真实集成测试开关与路径（见 `TESTING.md`） |

- 新文件优先 `from __future__ import annotations`。
- 公共符号用 `__all__` 显式导出（如 `runtime/agent.py`、`runtime/middleware.py`、Skill `__init__`）。
- 文档与长期注释用**简体中文**；标识符、路径、命令、配置键、API 名保留英文原文。
- **禁止**在文档或代码注释中写入密钥、`.env` 值、私有连接串。

## 3. 代码风格与类型

### Python 版本

- `requires-python = ">=3.11,<4.0"`；使用 `str | None`、`list[...]` 等 3.10+ 语法，不写 `Optional`/`List` 旧式别名（除非第三方 API 要求）。

### typing.Protocol **只**用于 Brain / BrainFactory

```python
# runtime/agent.py — 唯一 Protocol 用途
class Brain(Protocol):
    def stream(self, payload, config=None, **kwargs) -> Iterator: ...

class BrainFactory(Protocol):
    def create(self, *, resources, middleware, tools, workflow=None) -> Brain: ...
```

- `typing.Protocol` **只**用于 `Brain` / `BrainFactory`。
- 单实现不新建 Protocol/ABC；`DeepAgentsBrainFactory` 是具体类。
- 测试通过 `FakeBrain` / `FakeBrainFactory` 满足同一 `stream` / `create` 形状，无需继承 Protocol。

### 工具：callable + ToolCatalog

- 工具是普通 Python callable（可被 LangChain 包装为工具），在 `runtime/tools.py` **静态**注册：

```python
@dataclass(frozen=True)
class ToolCatalog:
    handlers: tuple[ToolHandler, ...]
    def as_list(self) -> list[ToolHandler]: ...

def default_tool_catalog() -> ToolCatalog:
    return ToolCatalog((
        parse_documents,
        extract_archives,
        lookup_philips_wgq_master_data,
        inspect_supply_chain_workbooks,
        finalize_tecan_overseas_recognition,
    ))
```

- 当前静态 **5** 个工具：MinerU 2、共享 12NC 主数据 1、共享 XLSX 检查 1、Tecan finalizer 1。
- 新增工具：在 Skill 包 `scripts/tools.py` 实现 → `default_tool_catalog` 追加一行 import + 注册；**禁止**目录扫描或动态 loader。
- 业务工具只接受本轮消息中的显式 artifact 路径，禁止搜索“最近文件”或历史任务。

### 资源与 ledger：具体类

- `ResourceConfig`（dataclass）、`AgentResources`、`SqliteRunLedger`、`RunSnapshot`、`RunEvent` 均为具体类型。
- 不为单一实现引入策略树、ABC 或宽配置开关。

### Pydantic 合同

- HTTP 与业务结果广泛使用 Pydantic v2：`model_config = ConfigDict(extra="forbid")`（API 与 `ContractModel`）。
- 请求体 `extra="forbid"`，避免静默吞掉未知字段。

## 4. 导入组织

典型顺序（与现有模块一致）：

1. `from __future__ import annotations`（若使用）
2. 标准库
3. 第三方（fastapi / langchain / deepagents / pydantic 等）
4. 本项目包：`integrations.*` → `runtime.*` → `skills.*`

约定：

- 包管理用 **`uv`**（`cd backend && uv sync`）；以 `backend/uv.lock` 为准；不要用 `pip install -e .` 绕过 lock。
- 避免循环导入：`create_harness` 内对 `DeepAgentsBrainFactory` / `default_tool_catalog` 做局部 import。
- 环境：`runtime/agent.py`、`integrations/mineru.py` 从 `backend/.env` `load_dotenv`；代码只读 `os.getenv` 键名。
- Skill 包对外再导出：`skills.<name>/__init__.py` 只暴露 workflow 常量与主结果类型（`__all__`）。

## 5. run-first 与状态归属

- **run** 是唯一执行与查询单位。
- `run_events` **append-only**；`runs` 表是投影快照（status / reply / error / result_json）。
- `session_id` 只作 LangGraph `thread_id` 与进程内单飞锁（`api.py` 的 `session_locks` / `active_runs`）；**无** session CRUD API。
- 最终业务 JSON **只**写 `run.result`；不得从 `reply`、thinking、候选 tool 文本或 Excel 推断正式结果。
- 同票渠道抽取在**单一 run** 内完成材料归集与终态裁决；不新建消息表、任务状态表、跨 run 中间态或生产业务 SubAgent。
- run 状态机：`queued` → `running` → `succeeded` | `failed` | `cancelled`；取消路径 `queued` → `cancelled` 或 `running` → `cancelling` → `cancelled`。
- 时间戳统一 **UTC+8** 本地 `YYYY-MM-DD HH:MM:SS`（ledger 与 OMS JSONL；`timezone(timedelta(hours=8))`，无夏令时）。
- 大 payload 可外置到 `run_events_dir` artifact，ledger 存路径（`SqliteRunLedger.max_inline_bytes`，默认 262144）。

## 6. HTTP 与注入点

- 仅四端点：`POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`（轮询，**无 SSE**）。
- `RunRequest.workflow` 当前仅允许 `WGQ`、`DK` 或省略；workflow run **禁止**客户端复用 `session_id`（422）。
- 程序内入口：`AgentResources` + `create_harness(...).execute_run(...)`；`create_app(harness_factory=...)` 便于测试注入 FakeBrain。
- OMS 旁路：`create_run` 成功后 best-effort 写 JSONL（`runtime/oms_log.py`，默认 `backend/log/oms_log.log`）；失败吞掉，不阻塞已创建 run；**非** `run_events`、无查询 API。

## 7. 错误处理与 `input_problems` 约定

| 场景 | 层 | 行为 |
|------|----|------|
| 业务 `input_problems` | 渠道 schema / finalizer | 合法 `run.result`，status **`succeeded`** |
| 缺 Philips `structured_response` | Harness（WGQ） | `ValueError` → status `failed` |
| DK 缺 Tecan finalizer 终态 | Harness（DK） | `ValueError` → status `failed` |
| `NoProgressLoop`（同 tool+args 连续 `NO_PROGRESS_WINDOW=3`） | middleware → Harness | status `failed`，error 文本携带原因 |
| 其它 Exception | Harness | status `failed`，`error` 文本 + raw repr |
| `GraphDrained`（cancel） | Harness | status `cancelled` |
| 进程重启未完成 run | API lifespan | `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` |
| OMS / 旁路索引异常 | API | 吞掉，不影响 run |
| Oracle / 主数据缺失 | Skill 工具 | 优雅降级（problems / 无数据），不拖垮进程 |
| MinerU / 环境变量缺失 | 工具层 | 明确错误信息（如 `Missing required environment variable: MINERU_BASE_URL`） |
| 未知 run | HTTP | 404 |
| 终态再 cancel | HTTP | 409 |
| workflow/session 校验失败 | HTTP | 422 |
| session 单飞冲突 | HTTP | 409 |
| cancel 已接受 | HTTP | 202（或 200 若已 cancelling/cancelled） |

### 分层原则

- **HTTP**：校验请求、投影状态码与 JSON body；不在 API 层做业务字段裁决。
- **工具**：返回可序列化内容或明确错误字符串；MinerU 通过 custom stream 发进度。
- **run 事件**：状态变更与内容流写入 `run_events`；业务问题统一 outcome `input_problems`，**不是**新事件类型。
- 真实模型/工具/图异常 → Harness 投影 `failed`；协作 cancel → `cancelled`。

### `input_problems` 硬规则

- 渠道终态 `outcome == "input_problems"` 时：`data` 仍须完整 `header` + 已证实 `items`（可为 `[]`）+ `problems` 至少一条（`source` / `location` / `issue` / `action`）。
- run.status 仍为 **`succeeded`**；业务问题不通过 `error` 字段或失败状态表达。
- `success` / `partial_success` 与 `input_problems` 的升降级由 `validate_channel_outcome`（`skills/channel_contract.py`）裁决。

## 8. 日志与可观测性

- **主路径不是 stdlib logging**：对外可观测性是 **7 类 run 事件**（§10）+ GET 轮询。
- `runtime/observability.py`：纯函数，从 langgraph stream chunk 提取 thinking / text_delta / model_usage / assistant_message；**无 I/O、不改 run 状态**。
- `ToolTelemetry`：`wrap_tool_call` → `get_stream_writer()` custom payload → Harness 映射为 `tool_execution`。
- MinerU 工具：custom 进度 → `tool_progress`（仅 `parse_documents` / `extract_archives`）。
- **model_usage**：在 subagent 文本过滤**之前**提取，subagent 文本不外泄但 **usage 仍记账**；模型名常量 `MAIN_AGENT_MODEL`（当前 `MiniMax-M3`）。
- API usage 计价：趋势估算（`api.py` 中 `_PRICING_TIERS`），最终账单以供应商为准。
- OMS：`event: "run_created"` JSONL 一行，含 `run_id` / `session_id` / `workflow` / `files[]`；与 ledger 同 UTC+8 时间格式。
- Memory 手册：`/memories/AGENTS.md`（StoreBackend）；受限 system prompt，禁止写业务数据/密钥；仅工具误用模式可追加。

## 9. 工具注册与 workflow denylist

### 静态目录

- 全部工具经 `default_tool_catalog()` 一次注册；生产构图 `subagents=[]`，`GeneralPurposeSubagentProfile(enabled=False)`。
- `/skills/**` 写权限 deny；Skill 资源只读挂载（`FilesystemPermission`）。

### denylist（禁止业务-only allowlist）

WGQ / DK 在 `DeepAgentsBrainFactory.create` 均用 **denylist** 排除**其他业务**工具：

```python
_WAG_EXCLUDED_TOOLS = frozenset({"finalize_tecan_overseas_recognition"})
_DK_EXCLUDED_TOOLS = frozenset()
# 两渠道均保留 parse_documents / extract_archives /
# lookup_philips_wgq_master_data / inspect_supply_chain_workbooks；DK 另保留 finalizer
```

- **禁止**业务-only allowlist（避免共享 MinerU / XLSX 工具从模型工具表消失，导致 `/memories/AGENTS.md` ZIP 指引失效）。
- 当前无其它业务工具可排除时，DK denylist 为空集合仍须走 denylist 路径，不得改写成 allowlist。
- 验证：`python -m tests.test_workflow_setup`（工具名集合断言）。

## 10. 事件模型（固定 7 类）

Harness / ledger 对外事件类型仅：

1. `status`
2. `tool_execution`
3. `tool_progress`（仅 `parse_documents` / `extract_archives` 的 custom 进度）
4. `thinking`
5. `text_delta`
6. `assistant_message`
7. `model_usage`

- 不重新引入 SSE、旧 `tool_call` / `tool_status` / `tool_result` 事件类型。
- stream 契约：`stream_mode=["messages", "custom", "updates"]`，`version="v2"`，`subgraphs=True`。
- subagent 文本过滤但 usage 仍记账（`FakeBrain` 可模拟 subagent 元数据；**不**表示生产会创建业务 SubAgent）。

## 11. Skill 单目录约定

每个业务 Skill 只保留一个下划线命名、可 import 的包，并在 `pyproject.toml` `[tool.setuptools.package-data]` 按包打包资源；运行时以同一目录的连字符别名满足 Agent Skills `name` 与目录一致的要求：

| 路径模式 | 内容 |
|----------|------|
| `skills/<snake_case_name>/` | `SKILL.md`（建议 ≤100 行）、按需 `references/*.md`、`schema.py`、`scripts/tools.py`、`__init__.py` |
| 虚拟挂载 | `/skills/<kebab-case-name>/`（必须与 `SKILL.md` 的 `name` 一致） |

当前 Skill：

| 包 | workflow | 终态路径 |
|----|----------|----------|
| `philips_wgq_inbound_recognition` | `WGQ` | `ToolStrategy(PhilipsWgqRecognitionResult)` + `structured_response` |
| `tecan_import` | `DK` | `finalize_tecan_overseas_recognition` 工具返回值 |

- 不做 Skill 目录自动发现；`skills: [SKILLS_SOURCE]` 固定 `/skills/`。
- 新增 Skill：新建包 → 更新 `package-data` → 静态注册工具 → 更新 denylist（若跨业务互斥）→ 同步 codebase 文档与测试。
- Tecan **不**携带 Excel 模板或生成器；XLSX inspection 写中间 JSON artifact 供 Agent 读取，不是 OMS 合同。
- 渠道 Skill 材料边界：解析 PDF（`parse_documents`）与 XLSX（`inspect_supply_chain_workbooks`）；ZIP/DOCX/图片内容不解析，材料足够时写入 `problems` 后继续。

## 12. 渠道 JSON 合同要点

共用层：`skills/channel_contract.py`。

### `items[]` 完整 24 字段（`OrderItem`）

`invoice_number`、`invoice_date`、`so_item`、`product_id`、`new_or_used`、`chinese_name`、`specification`、`quantity`、`unit`、`currency`、`unit_price`、`total_price`、`trade_terms`、`origin_country`、`customs_code`、`declaration_elements`、`legal_quantity_1`、`legal_unit_1`、`legal_quantity_2`、`legal_unit_2`、`gross_weight`、`net_weight`、`business_unit`、`pre_or_post_sales`。

规则：

- 未知值为 **`null`**（空字符串 validator 规范为 `null`）。
- 数量/金额/重量等为**非科学计数法**十进制字符串（可带千分位输入，输出规范化）；日期序列化为 `YYYY-MM-DD`。
- 货币 ISO 三位大写；`new_or_used` ∈ {新, 旧}；`pre_or_post_sales` ∈ {售前, 售后}。
- Philips header 与 Tecan header **各自独立**；`items[]` 共用 `OrderItem`。
- `RecognitionProblem`：`source` / `location` / `issue` / `action`（均非空）。
- **不**输出 `shipment`、Excel 终态文件、候选噪声、置信度或审计细节。

### outcome 语义（`validate_channel_outcome`）

| outcome | 含义 | run.status |
|---------|------|------------|
| `success` | 最终业务字段无未解决缺失 | `succeeded` |
| `partial_success` | 核心商品事实已确认，补充字段缺失 | `succeeded` |
| `input_problems` | 票次身份或核心事实无法确认；仍须完整 `data.header` + 已证实 `items`（可为 `[]`）+ 至少一条 problem | **`succeeded`** |

业务裁决原则：

- 票据事实优先于主数据；主数据仅唯一非语义标识匹配的标准化/补齐，不覆盖本票数量、金额、重量、编号或运输事实。
- WGQ / DK 均将确认的唯一 12NC 批量传给 `lookup_philips_wgq_master_data`；WGQ 可传唯一 Tracking，DK 不传 Tracking，只使用共享 Oracle。
- 发票行按上传顺序与原行顺序；同 12NC 默认不合并；同票多发票/运单字段按材料顺序英文逗号连接。

### Philips vs Tecan 终态路径

- **WGQ**：`ToolStrategy(PhilipsWgqRecognitionResult)` + stream `updates` 中的 `structured_response`；缺失则 run `failed`（`structured_response missing for WGQ`）。
- **DK**：`finalize_tecan_overseas_recognition` 工具返回校验后的 JSON；Harness 从对应 `ToolMessage` 投影到 `run.result`；缺失则 run `failed`；无 `response_format` / 无 Philips recovery。
- 普通 run：`structured_schema=None`，不强制按 Philips schema 恢复；若仍调用 Tecan finalizer 可投影 result。

## 13. Middleware 与 StructuredOutputRecovery 规则

- 横切能力集中在 `runtime/middleware.py`；**不要**把 Philips/Tecan 字段业务裁决塞进全局 middleware。
- `runtime_middlewares(memory_backend=..., structured_schema=...)` 每次构图返回**新实例**列表。
- 默认顺序（洋葱模型：`before_*` 正序、`after_*` 逆序、`wrap_*` 外层先入后出）：

  1. `StructuredOutputRecovery`（仅当 `structured_schema` 非空；列表**最前**，使 `after_model` 最后执行）
  2. `ToolTelemetry`（`wrap_tool_call` → custom stream）
  3. `NoProgressMiddleware`（`before_model`；状态派生自消息，**无**实例可变跨调用泄漏）
  4. `StructuredOutputCompatibility`（ToolStrategy 请求关闭 thinking）
  5. 可选 `MemoryMiddleware`（主 Agent 挂 `/memories/AGENTS.md`）

- Harness 对 WGQ 传 `structured_schema=PhilipsWgqRecognitionResult`；DK / 普通 run 传 `None`。
- `DeepAgentsBrainFactory` 在 WGQ 路径若缺少 Compatibility/Recovery 会补装；Recovery `insert(0, ...)`。

### StructuredOutputRecovery（硬约束）

- class-based `after_model` + state 扩展 `structured_recovery_attempts`。
- **`@hook_config(can_jump_to=["model", "end"])` 必须含 `"end"`**。
- 失败重试：`jump_to: "model"`，默认 `max_retries=2`（`DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES`；约 `1 + max_retries` 次模型调用量级）。
- 耗尽时必须 **`jump_to: "end"`**，**禁止**只返回 `None`（否则 ToolStrategy 可能无限 model→model）。
- 空 data 壳（`success`/`partial_success` 且 `data:{}` 或缺 header/items）：
  - 优先同回合 `tool_call_id` 匹配 AI 文本合法 JSON 恢复；
  - 否则 `EMPTY_DATA_SHELL_HINT` + 可选 `PHILIPS_MINIMAL_DATA_SKELETON` 纠错；
  - 空壳耗尽 → all-null nested data + `partial_success` + runtime problem（可 `succeeded`）；
  - **其它**失败耗尽 → 无 `structured_response`（可 `failed`）。
- `input_problems` **不**视为 empty-data shell。
- Philips schema 对 runtime recovery skeleton 有 validator 豁免路径（`_is_runtime_recovery_skeleton`）。
- 验证：`python -m tests.test_harness`。

## 14. 时间戳约定

| 写入点 | 格式 | 时区 |
|--------|------|------|
| `SqliteRunLedger`（`created_at` / `updated_at` / 事件） | `YYYY-MM-DD HH:MM:SS` | UTC+8 固定偏移（`timezone(timedelta(hours=8))`） |
| OMS JSONL（`runtime/oms_log.py`） | 同上 | 同上 |

- 不使用 UTC `Z` 后缀、不使用 ISO 带偏移字符串作为 ledger/OMS 权威格式。
- 文档与断言可用正则或样例匹配该格式；测试见 `tests.test_run_ledger`。

## 15. 禁止重新引入

| 禁止项 | 原因 |
|--------|------|
| session CRUD API | `session_id` 仅 thread_id + 进程内单飞 |
| SSE / 流式 HTTP 响应 | 契约为四端点轮询 |
| 生产业务 SubAgent | `subagents=[]`；同票归集在单 Agent 单 run |
| 业务-only 工具 allowlist | 会丢掉共享 MinerU/XLSX |
| 第 8 类事件类型 | 事件固定 7 类 |
| 消息/任务状态表、跨 run 业务 middleware | run-first + 渠道合同已覆盖 |
| Tecan Excel 模板/生成器 | 终态仅为 JSON |
| 输出 `shipment` / 候选噪声 / 审计字段 | 渠道 PRD 合同 |
| Protocol 滥用（工具/资源） | 仅 Brain 形状可插拔 |
| 动态 Skill/工具扫描 | 静态 package-data + ToolCatalog |

## 16. 文档与变更同步

- 改 backend 代码后：先同步 `backend/.planning/codebase/` 事实文档，再按影响更新根级 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`。
- 文档 diff 至少：`git diff --check`（仓库根目录）。
- 验证命令与门禁见同目录 `TESTING.md` 与根级 `docs/commands.md`。

## 17. 快速自检清单（编码时）

- [ ] 未新增 Protocol（除非 Brain 形状变更）
- [ ] 新工具已静态注册，且 WGQ / DK denylist 仍只排除其他业务工具
- [ ] 业务终态只进 `run.result`；`input_problems` 仍 `succeeded`
- [ ] 事件类型落在 7 类之内
- [ ] Skill 单目录 + `package-data`；虚拟路径用连字符名
- [ ] `StructuredOutputRecovery` 的 `can_jump_to` 含 `end`，耗尽显式 `jump_to: "end"`
- [ ] 时间戳为 UTC+8 `YYYY-MM-DD HH:MM:SS`
- [ ] 无 SSE / session API / 生产业务 SubAgent
- [ ] 对应 `python -m tests.*` 已跑通
