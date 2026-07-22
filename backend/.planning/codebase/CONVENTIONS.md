# CONVENTIONS — backend 编码与质量约定

> Analysis Date: 2026-07-22。以 `backend/` 源码为准；根级 `AGENTS.md` / `docs/conventions.md` 仅作交叉验证。

## 1. 源码布局与模块边界

- 产品代码只在 `backend/api.py`、`runtime/`、`integrations/`、`skills/`；包名发行为 `dsagents`，**无**顶层 `dsagents/` 源码包、service 层、通用 workflow 引擎。
- `api.py`：四 HTTP 端点、请求/响应 Pydantic 模型、session 进程内单飞、usage 计价展示、OMS best-effort 旁路写点。
- `runtime/`：Brain 装配、Harness 执行、middleware、run ledger、资源挂载、工具目录、可观测归一化、OMS 日志。
- `integrations/`：artifact 路径与 MinerU HTTP 客户端等外部 I/O。
- `skills/`：业务 schema、工具实现、kebab-case 资源目录（`SKILL.md` / references）。
- 历史 setuptools 产物（如 `backend/build/`、`dist/`、`dsagents.egg-info/`）**不是**源码权威，不读进 VCS 决策。
- 能力可注入（`BrainFactory`、`ToolCatalog`、`harness_factory`），但运行时保持薄：没有真实调用方前不增加任务队列、策略框架或宽泛配置体系。

## 2. 命名风格

| 类别 | 约定 | 示例 |
|------|------|------|
| 模块 / 包 | `snake_case` | `runtime/execution.py`、`philipswgqinboundrecognition` |
| Skill 资源目录 | kebab-case，与 import 包成对 | `philips-wgq-inbound-recognition/` ↔ `philipswgqinboundrecognition/` |
| 类 | `PascalCase` | `HarnessRuntime`、`SqliteRunLedger`、`ToolCatalog` |
| 函数 / 方法 | `snake_case` | `execute_run`、`default_tool_catalog`、`runtime_middlewares` |
| 常量 | `UPPER_SNAKE` | `WORKFLOW`、`NO_PROGRESS_WINDOW`、`_PHILIPS_EXCLUDED_TOOLS` |
| 私有助手 | 单下划线前缀 | `_normalize_messages`、`_update_events` |
| 虚拟 FS 路径 | 前导 `/` 的挂载前缀 | `/skills/`、`/artifacts/`、`/memories/` |
| workflow 字面量 | 固定 snake 字符串 | `philips_wgq_inbound_recognition` |
| 工具函数名 | 与注册 callable `__name__` 一致 | `parse_documents`、`finalize_tecan_overseas_recognition` |

- 新文件优先 `from __future__ import annotations`。
- 公共符号用 `__all__` 显式导出（如 `runtime/agent.py`、`runtime/middleware.py`、Skill `__init__`）。
- 文档与长期注释用**简体中文**；标识符、路径、命令、配置键、API 名保留英文原文。
- **禁止**在文档或代码注释中写入密钥、`.env` 值、私有连接串。

## 3. 抽象边界：Protocol / 工具 / 资源

### Protocol 仅 Brain

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

- 当前静态 **5** 个工具：MinerU 2、Philips 主数据 1、共享 XLSX 检查 1、Tecan finalizer 1。
- 新增工具：在 Skill 包 `scripts/tools.py` 实现 → `default_tool_catalog` 追加一行 import + 注册；**禁止**目录扫描或动态 loader。
- 业务工具只接受本轮消息中的显式 artifact 路径，禁止搜索“最近文件”或历史任务。

### 资源与 ledger：具体类

- `ResourceConfig`（dataclass）、`AgentResources`、`SqliteRunLedger`、`RunSnapshot`、`RunEvent` 均为具体类型。
- 不为单一实现引入策略树、ABC 或宽配置开关。

## 4. run-first 与状态归属

- **run** 是唯一执行与查询单位。
- `run_events` **append-only**；`runs` 表是投影快照（status / reply / error / result_json）。
- `session_id` 只作 LangGraph `thread_id` 与进程内单飞锁（`api.py` 的 `session_locks` / `active_runs`）；**无** session CRUD API。
- 最终业务 JSON **只**写 `run.result`；不得从 `reply`、thinking、候选 tool 文本或 Excel 推断正式结果。
- 同票渠道抽取在**单一 run** 内完成材料归集与终态裁决；不新建消息表、任务状态表、跨 run 中间态或生产业务 SubAgent。
- run 状态机：`queued` → `running` → `succeeded` | `failed` | `cancelled`；取消路径 `queued` → `cancelled` 或 `running` → `cancelling` → `cancelled`。
- 时间戳统一 **UTC+8** 本地 `YYYY-MM-DD HH:MM:SS`（ledger 与 OMS JSONL）。

## 5. HTTP 与注入点

- 仅四端点：`POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`（轮询，**无 SSE**）。
- `RunRequest.workflow` 当前仅允许 `philips_wgq_inbound_recognition` 或省略；workflow run **禁止**客户端复用 `session_id`（422）。
- 程序内入口：`AgentResources` + `create_harness(...).execute_run(...)`；`create_app(harness_factory=...)` 便于测试注入 FakeBrain。
- OMS 旁路：`create_run` 成功后 best-effort 写 JSONL；失败吞掉，不阻塞已创建 run；**非** `run_events`、无查询 API。

## 6. 类型、schema 与渠道 JSON

### Pydantic 合同

- HTTP 与业务结果广泛使用 Pydantic v2：`model_config = ConfigDict(extra="forbid")`（API 与 `ContractModel`）。
- 渠道共用 `skills/channel_contract.py`：
  - `OrderItem`：**完整 24 字段**；未知为 `null`，空字符串在 validator 中规范为 `null`。
  - 数量/金额/重量等为**非科学计数法**十进制字符串（可带千分位输入，输出规范化）；日期序列化为 `YYYY-MM-DD`。
  - `RecognitionProblem`：`source` / `location` / `issue` / `action`。
  - `validate_channel_outcome`：统一 `success` / `partial_success` / `input_problems` 语义。
- Philips header 与 Tecan header **各自独立**；`items[]` 共用 `OrderItem`。
- 不输出 `shipment`、Excel 终态文件、候选噪声、置信度或审计细节。

### outcome 语义

| outcome | 含义 | run.status |
|---------|------|------------|
| `success` | 最终业务字段无未解决缺失 | `succeeded` |
| `partial_success` | 核心商品事实已确认，补充字段缺失 | `succeeded` |
| `input_problems` | 票次身份或核心事实无法确认；仍须完整 `data.header` + 已证实 `items`（可为 `[]`）+ 至少一条 problem | **`succeeded`**（业务问题，非运行失败） |

- 真实模型/工具/图异常 → Harness 投影 `failed`；`NoProgressLoop` → `failed`；协作 cancel → `cancelled`。
- 票据事实优先于主数据；主数据仅唯一非语义标识匹配的标准化/补齐，不覆盖本票数量、金额、重量、编号或运输事实。
- 发票行按上传顺序与原行顺序；同 12NC 默认不合并；同票多发票/运单字段按材料顺序英文逗号连接。

### Philips vs Tecan 终态路径

- **Philips**：`ToolStrategy(PhilipsWgqRecognitionResult)` + stream `updates` 中的 `structured_response`；缺失则 run `failed`（`structured_response missing`）。
- **Tecan**：`finalize_tecan_overseas_recognition` 工具返回校验后的 JSON；Harness 从对应 `ToolMessage` 投影到 `run.result`；无 `response_format` / 无 Philips recovery。
- 普通 run：`structured_schema=None`，不强制按 Philips schema 恢复。

## 7. 工具收窄：denylist

- Philips workflow 在 `DeepAgentsBrainFactory.create` 用 **denylist** 排除**其他业务**工具：

```python
_PHILIPS_EXCLUDED_TOOLS = frozenset({"finalize_tecan_overseas_recognition"})
# 保留 parse_documents / extract_archives / lookup_... / inspect_supply_chain_workbooks
```

- **禁止**业务-only allowlist（避免共享 MinerU / XLSX 工具从模型工具表消失）。
- 生产 `subagents=[]`；`GeneralPurposeSubagentProfile(enabled=False)`。
- `/skills/**` 写权限 deny；Skill 资源只读挂载。

## 8. Skill 成对目录

每个业务 Skill 必须两套目录，并更新 `pyproject.toml` `[tool.setuptools.package-data]`：

| 角色 | 路径模式 | 内容 |
|------|----------|------|
| 资源 | `skills/<kebab-name>/` | `SKILL.md`（建议 ≤100 行）、按需 `references/*.md`；挂载到 `/skills/` |
| 代码 | `skills/<importpkg>/` | `schema.py`、`scripts/tools.py`、包 `__init__.py` |

当前对：

- `philips-wgq-inbound-recognition` ↔ `philipswgqinboundrecognition`
- `tecan-import` ↔ `tecanimport`

- 不做 Skill 目录自动发现；`skills: [SKILLS_SOURCE]` 固定 `/skills/`。
- Tecan **不**携带 Excel 模板或生成器；XLSX inspection 写中间 JSON artifact 供 Agent 读取，不是 OMS 合同。
- 渠道 Skill 材料边界：解析 PDF（`parse_documents`）与 XLSX（`inspect_supply_chain_workbooks`）；ZIP/DOCX/图片内容不解析，材料足够时写入 `problems` 后继续。

## 9. Middleware 模式

- 横切能力集中在 `runtime/middleware.py`；**不要**把 Philips/Tecan 字段业务裁决塞进全局 middleware。
- `runtime_middlewares(memory_backend=..., structured_schema=...)` 每次构图返回**新实例**列表。
- 默认顺序（洋葱模型：`before_*` 正序、`after_*` 逆序、`wrap_*` 外层先入后出）：

  1. `StructuredOutputRecovery`（仅当 `structured_schema` 非空；列表**最前**，使 `after_model` 最后执行）
  2. `ToolTelemetry`（`wrap_tool_call` → custom stream）
  3. `NoProgressMiddleware`（`before_model`；状态派生自消息，**无**实例可变跨调用泄漏）
  4. `StructuredOutputCompatibility`（ToolStrategy 请求关闭 thinking）
  5. 可选 `MemoryMiddleware`（主 Agent 挂 `/memories/AGENTS.md`；受限 system prompt，禁止写业务数据/密钥）

- Harness 对 Philips workflow 传 `structured_schema=PhilipsWgqRecognitionResult`；普通/Tecan 传 `None`。
- `DeepAgentsBrainFactory` 在 Philips 路径若缺少 Compatibility/Recovery 会补装；Recovery `insert(0, ...)`。

### StructuredOutputRecovery（硬约束）

- class-based `after_model` + state 扩展 `structured_recovery_attempts`。
- **`@hook_config(can_jump_to=["model", "end"])` 必须含 `"end"`**。
- 失败重试：`jump_to: "model"`，默认 `max_retries=2`。
- 耗尽时必须 **`jump_to: "end"`**，**禁止**只返回 `None`（否则 ToolStrategy 可能无限 model→model）。
- 空 data 壳（`success`/`partial_success` 且 `data:{}` 或缺 header/items）：
  - 优先同回合 `tool_call_id` 匹配 AI 文本合法 JSON 恢复；
  - 否则 `EMPTY_DATA_SHELL_HINT` + 可选 `PHILIPS_MINIMAL_DATA_SKELETON` 纠错；
  - 空壳耗尽 → all-null nested data + `partial_success` + runtime problem（可 `succeeded`）；
  - **其它**失败耗尽 → 无 `structured_response`（可 `failed`）。
- `input_problems` **不**视为 empty-data shell。
- Philips schema 对 runtime recovery skeleton 有 validator 豁免（`_is_runtime_recovery_skeleton`）。

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
- subagent 文本过滤但 **usage 仍记账**（`FakeBrain` 可模拟 subagent 元数据；**不**表示生产会创建 Tecan SubAgent）。
- 大 payload 可外置到 `run_events_dir` artifact，ledger 存路径。

## 11. 错误处理模式

| 场景 | 行为 |
|------|------|
| 业务 `input_problems` | 合法 `run.result`，status `succeeded` |
| 缺 Philips `structured_response` | `ValueError` → status `failed` |
| `NoProgressLoop`（同 tool+args 连续 `NO_PROGRESS_WINDOW=3`） | status `failed`，error 文本携带原因 |
| 其它 Exception | status `failed`，`error` 文本 + raw repr |
| `GraphDrained`（cancel） | status `cancelled` |
| 进程重启未完成 run | lifespan `fail_incomplete_runs(INTERRUPTED_RUN_ERROR)` |
| OMS / 旁路索引异常 | 吞掉，不影响 run |
| Oracle / 主数据缺失 | Skill 内优雅降级（problems / 无数据），不拖垮进程 |
| MinerU / 环境变量缺失 | 工具层明确错误信息；测试用 mock HTTP |

- API 层：未知 run 404；终态再 cancel 409；workflow/session 校验失败 422；session 冲突 409。
- 请求体 `extra="forbid"`，避免静默吞掉未知字段。

## 12. 导入与包管理

- 包管理用 **`uv`**（`cd backend && uv sync`）；以 `backend/uv.lock` 为准；不要用 `pip install -e .` 绕过 lock。
- Python `>=3.11`。
- 避免循环导入：`create_harness` 内对 `DeepAgentsBrainFactory` / `default_tool_catalog` 做局部 import。
- 环境：`runtime/agent.py` 从 `backend/.env` `load_dotenv`；代码只读 `os.getenv` 键名，文档不记录值。

## 13. 文档与变更同步

- 改 backend 代码后：先同步 `backend/.planning/codebase/` 事实文档，再按影响更新根级 `ARCHITECTURE.md` / `INTERFACES.md` / `coding_maps/SYSTEM_MAP.md`。
- 文档 diff 至少：`git diff --check`（仓库根目录）。
- 验证命令与门禁见同目录 `TESTING.md` 与根级 `docs/commands.md`。

## 14. 快速自检清单（编码时）

- [ ] 未新增 Protocol（除非 Brain 形状变更）
- [ ] 新工具已静态注册，且 Philips denylist 仍只排除其他业务工具
- [ ] 业务终态只进 `run.result`；`input_problems` 仍 `succeeded`
- [ ] 事件类型落在 7 类之内
- [ ] Skill 双目录 + package-data
- [ ] `StructuredOutputRecovery` 的 `can_jump_to` 含 `end`，耗尽显式 `jump_to: "end"`
- [ ] 无 SSE / session API / 生产业务 SubAgent
- [ ] 对应 `python -m tests.*` 已跑通
