---
last_mapped_commit: 555bca7
analysis_date: 2026-07-20
focus: quality
---

# CONVENTIONS — backend 编码与质量约定

> 本文件记录 `backend/` **实际代码**体现的命名、风格、错误处理与中间件约定。
> 全局硬约束摘要见根级 `Agents.md` / `docs/conventions.md`；接口与架构边界见根级 `INTERFACES.md` / `ARCHITECTURE.md`。

## Naming

### 模块与包

| 区域 | 约定 | 示例 |
|------|------|------|
| 顶层入口 | 单文件 `api.py`（`py-modules`） | `backend/api.py` |
| 运行时包 | 小写蛇形目录 + 模块 | `runtime/execution.py`、`runtime/middleware.py` |
| 集成包 | 小写蛇形 | `integrations/mineru.py`、`integrations/artifacts.py` |
| Skill 资源目录 | **kebab-case**，挂载 `/skills/` | `skills/philips-wgq-inbound-recognition/`、`skills/tecan-import/` |
| Skill Python 包 | **可 import 合法包名**（无连字符） | `skills/philipswgqinboundrecognition/`、`skills/tecanimport/` |
| 测试模块 | `tests/test_<area>.py`；真实集成 `tests/test_real_*.py` | `test_harness.py`、`test_real_philips_wgq_inbound_recognition.py` |

**Skill 成对目录（硬约定）**：每个内置业务必须同时存在

1. kebab-case 资源树：`SKILL.md`、可选 `references/`、`assets/`（写入 `pyproject.toml` 的 `[tool.setuptools.package-data]`）
2. 可 import 包：`schema.py` / `scripts/tools.py` 等

新增 Skill 时两套目录一起建；禁止只建一边或把资源塞进 Python 包却不挂 `/skills/`。

### 函数与类

- 公开 API / 类：`PascalCase` 类型（`HarnessRuntime`、`SqliteRunLedger`、`ToolCatalog`、`AgentResources`）
- 工厂与入口：`create_harness`、`create_app`、`default_tool_catalog`、`runtime_middlewares`、`workflow_subagents`
- 私有辅助：单下划线前缀 `_normalize_messages`、`_now_text`、`_empty_shell_fallback_result`
- 工具 handler：可调用对象，**以 `__name__` 作为工具名**（`parse_documents`、`lookup_philips_wgq_master_data`）
- 测试入口统一 `run()`；模块可 `if __name__ == "__main__": run()`

### 常量

- 全大写蛇形：`RUN_STATUSES`、`WORKFLOW`、`EMPTY_DATA_SHELL_HINT`、`PHILIPS_MINIMAL_DATA_SKELETON`、`NO_PROGRESS_WINDOW`
- 路径/环境锚定常量：`BACKEND_ENV_PATH`、`RUNTIME_AGENTS_PATH`、`SKILLS_SOURCE`
- workflow 工具 denylist：`_PHILIPS_EXCLUDED_TOOLS`（`frozenset`）

### Protocol 边界

- **`typing.Protocol` 仅**用于 `Brain` 与 `BrainFactory`（`runtime/agent.py`）
- 工具：**callable + `ToolCatalog`**，不为工具表建 Protocol/ABC
- 资源、ledger、harness：**具体类**（`AgentResources`、`SqliteRunLedger`、`HarnessRuntime`）
- 不为单实现代码新增泛化 Protocol

## Code style

- 文件普遍 `from __future__ import annotations`
- 公开函数与数据类带类型注解；返回 `Iterator` / 生成器在 harness 流式路径上显式标注
- 不可变配置/快照优先 `@dataclass(frozen=True)`：`ToolCatalog`、`RunEvent`、`RunSnapshot`、`ResourceConfig`
- 业务契约用 **Pydantic** `BaseModel` + `ConfigDict(extra="forbid")`（如 Philips `schema.py` 中 `_ContractModel`）
- 避免过度抽象：无服务层/策略框架；工具**静态注册**于 `default_tool_catalog()`，不做目录扫描 loader
- 运行时保持薄：`HarnessRuntime.execute_run` 负责驱动 Brain、规范化 stream、写事件；业务逻辑在 Skill 包
- 中文 docstring / 模块注释用于说明意图；标识符保持英文

## Import 组织

典型顺序（与现有模块一致）：

1. 标准库（`json`、`pathlib`、`dataclasses`、`typing`…）
2. 第三方（`fastapi`、`langchain_*`、`langgraph`、`pydantic`、`deepagents`…）
3. 本仓库包：`runtime.*`、`integrations.*`、`skills.*`、`tests.*`
4. **局部 import** 仅用于打破环依赖，并写明原因，例如：

```python
# runtime/execution.py create_harness
from runtime.agent import DeepAgentsBrainFactory
from runtime.tools import default_tool_catalog
```

```python
# runtime/agent.py _extractor
from runtime.tools import default_tool_catalog
```

- Skill 工具在 `runtime/tools.py` **顶层静态 import** 后注册进 `ToolCatalog`
- 测试可从 `tests.test_support` 引用 `FakeBrain` / fixture helpers

## Error handling

### Run 状态机

`runtime/runs.py` 中 `RUN_STATUSES`：

`queued` → `running` → `succeeded` | `failed` | `cancelled`
取消路径：`queued` → `cancelled`，或 `running` → `cancelling` → `cancelled`

- `emit_run_status` 校验 status；未知 status 抛 `ValueError`
- 未知 `run_id`：`get_run` 抛 `KeyError`
- 启动恢复：`fail_incomplete_runs` 将 `queued`/`running`/`cancelling` 标为 `failed`（`INTERRUPTED_RUN_ERROR`）

### 执行路径（harness）

`HarnessRuntime.execute_run`：

| 情况 | 结果 |
|------|------|
| 正常结束 | `succeeded`，可选 `reply` / `result` |
| `GraphDrained`（取消） | `cancelled` |
| `NoProgressLoop` | `failed`，error 文本 |
| 其它 `Exception` | `failed`，`_error_text(exc)` + raw `repr` |
| Philips workflow 且无 `structured_response` | `ValueError` → `failed`（`structured_response missing`） |

**业务问题 ≠ run 失败**：

- Philips：`run.result.outcome` 为 `success` / `partial_success` / `input_problems`；`input_problems` 时 `data=null` 且至少一条 problem，**run 仍为 `succeeded`**
- Tecan：工具返回 `code: "input_problems"` + `problems` 列表；不把业务校验失败伪装成未捕获异常
- 不要用 `reply` 解析业务 JSON

### 工具错误

- `ToolTelemetry.wrap_tool_call`：工具异常时 emit `status: "error"` 后 **原样 re-raise**（真实错误透传）
- MinerU 等：缺环境变量快速失败（如 `Missing required environment variable: MINERU_BASE_URL`）
- Oracle：`ORACLE_CLIENT_LIB_DIR` / 连接失败时 **优雅降级**（主数据部分字段为空），不拖垮整次 lookup（见 Philips tools + 本地 mock 测试）
- OMS 旁路索引：`append_run_created_log` 在 HTTP `create_run` 成功后 best-effort；写失败不阻塞已创建 run（非 `run_events`）

### 事件类型（固定 7 类）

`status` / `tool_execution` / `tool_progress` / `thinking` / `text_delta` / `assistant_message` / `model_usage`
禁止重新引入已删除的 `tool_call` / `tool_status` / `tool_result`。

## Middleware 约定

实现集中在 `runtime/middleware.py`；`runtime_middlewares(memory_backend=...)` 产出**新实例**列表。

### 顺序（洋葱模型）

1. `StructuredOutputRecovery`（最前 → `after_model` 尽量最后执行，便于回填 `structured_response`）
2. `ToolTelemetry`
3. `NoProgressMiddleware`
4. `StructuredOutputCompatibility`
5. （仅主 Agent）`MemoryMiddleware`：`sources=[RUNTIME_AGENTS_PATH]`，受限 `RUNTIME_MEMORY_SYSTEM_PROMPT`，`add_cache_control=True`

- 主 Agent：约 **5** 个 middleware（含 memory）
- SubAgent：`runtime_middlewares()` **无** memory → **4** 个；声明式 SubAgent **不继承**主 Agent middleware

### StructuredOutputRecovery（关键）

- `after_model` + `@hook_config(can_jump_to=["model", "end"])` — **`can_jump_to` 必须含 `"end"`**
- 默认 `max_retries = DEFAULT_STRUCTURED_RECOVERY_MAX_RETRIES`（2）
- 已有 `structured_response` → 直接 `None`
- 文本 fenced JSON 合法 → 写入 `structured_response`（可 `jump_to: "end"`）
- **空 data 壳**（`success`/`partial_success` 但 `data:{}` 或缺嵌套）：
  1. 优先用 `ToolMessage.tool_call_id` 对齐同一 AIMessage 的 schema call；若该 AI **文本 JSON 合法** → 直接写 `structured_response` + `jump_to: "end"`
  2. 否则 `EMPTY_DATA_SHELL_HINT` + 形状提示（`PHILIPS_MINIMAL_DATA_SKELETON`）+ `jump_to: "model"`
- **空壳重试耗尽**：schema 合法的 **all-null nested data** + `partial_success` + runtime problem（**不**编造业务字段；**不是** `data:{}` / `data:null` / `input_problems`）+ `jump_to: "end"`
- **其它失败耗尽**：`{"jump_to": "end"}` **无** `structured_response` → harness 可 `failed`；**禁止**只返回 `None`（否则 ToolStrategy 下 model↔model 死循环）
- 正常路径要求结构化 **工具 args**；文本 JSON 仅作无法调用工具时的后备
- Philips 工厂在 workflow 路径上还会插入/确保 `StructuredOutputCompatibility` + `StructuredOutputRecovery`；`response_format=ToolStrategy(..., handle_errors=philips_structured_output_error_message)`

### 其它 middleware

- `NoProgressMiddleware`：自 message 状态推导最近工具调用 token；连续 `NO_PROGRESS_WINDOW`（3）次相同调用 → `NoProgressLoop`
- `StructuredOutputCompatibility`：对 `ToolStrategy` 请求临时关闭 model `thinking`
- `ToolTelemetry`：start/complete/error + `duration_ms` + `agent_name`

## Workflow tools：denylist，禁止业务-only allowlist

固定 workflow `philips_wgq_inbound_recognition` 收窄工具时：

```python
# runtime/agent.py — 排除其他业务（Tecan），保留共享 MinerU + 本业务工具
_PHILIPS_EXCLUDED_TOOLS = frozenset({
    "save_tecan_extraction",
    "generate_tecan_import",
})
# ...
kwargs["tools"] = [
    tool for tool in tools
    if getattr(tool, "__name__", "") not in _PHILIPS_EXCLUDED_TOOLS
]
```

- 必须保留：`parse_documents`、`extract_archives`、`lookup_philips_wgq_master_data`
- 禁止 allowlist 只留业务工具（会与 `/memories/AGENTS.md` 中 ZIP→`extract_archives` 指引脱节）
- 静态全量目录 5 工具：`parse_documents` / `extract_archives` / `lookup_philips_wgq_master_data` / `save_tecan_extraction` / `generate_tecan_import`
- 验证：`python -m tests.test_workflow_setup`

非 Philips workflow：主 Agent 注册 2 个 Tecan SubAgent（`tecan-extractor-a` / `b`），各 1 工具 + 4 middleware + 只读 FS permission。

## 时间戳

- `SqliteRunLedger` 与 `oms_log` 统一 **中国标准时间 UTC+8**（无夏令时）
- 格式：`YYYY-MM-DD HH:MM:SS`（如 `2026-07-17 12:01:59`）
- 实现：`datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")`
- 改格式必须两边同步；测试用正则 `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$` 校验

## 文档与注释语言

- 长期文档、事实层、`Agents.md` 导航：**简体中文**
- **保留**标识符、路径、命令、配置键、API 名、端口原文
- **禁止**写入密钥、`.env` 值、私有连接串
- 代码注释：中英均可；用户可见 system prompt / Skill 文案以中文业务说明为主
- 改 backend 后：先更新 `backend/.planning/codebase/`，再按影响回看根级系统文档；文档变更至少 `git diff --check`

## 实际代码片段模式

### 1）frozen dataclass + 静态工具目录

```16:36:backend/runtime/tools.py
@dataclass(frozen=True)
class ToolCatalog:
    handlers: tuple[ToolHandler, ...]

    def as_list(self) -> list[ToolHandler]:
        return list(self.handlers)


def default_tool_catalog() -> ToolCatalog:
    """Static registration: MinerU 通用工具 + Philips 一个工具 + Tecan 两个工具。
    ...
    """
    return ToolCatalog(
        (
            parse_documents,
            extract_archives,
            lookup_philips_wgq_master_data,
            save_tecan_extraction,
            generate_tecan_import,
        )
    )
```

### 2）StructuredOutputRecovery：can_jump_to 含 end + 耗尽跳 end

```264:366:backend/runtime/middleware.py
    @hook_config(can_jump_to=["model", "end"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        ...
    def _retry_or_give_up(...) -> dict[str, Any]:
        attempts = _recovery_attempts(state)
        if attempts >= self.max_retries:
            # ... Returning None would infinite-loop; jump to end instead.
            if empty_shell and self.schema is PhilipsWgqRecognitionResult:
                ...
                return {
                    "structured_response": _empty_shell_fallback_result(rejected),
                    "jump_to": "end",
                    ...
                }
            return {"jump_to": "end"}
        return {
            "messages": [HumanMessage(...)],
            "jump_to": "model",
            "structured_recovery_attempts": attempts + 1,
        }
```

### 3）UTC+8 时间写入

```12:13:backend/runtime/runs.py
# 中国标准时间（UTC+8，无夏令时）；库内时间字段统一按此时区写入。
_CHINA_TZ = timezone(timedelta(hours=8))
```

```470:472:backend/runtime/runs.py
def _now_text() -> str:
    # 中国时区本地时间：YYYY-MM-DD HH:MM:SS（如 2026-07-17 12:01:59）。
    return datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
```

## 相关验证命令（摘要）

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

完整命令与真实集成开关见根级 `docs/commands.md` 与本目录 `TESTING.md`。
