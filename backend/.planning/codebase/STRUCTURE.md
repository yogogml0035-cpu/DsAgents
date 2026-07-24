# STRUCTURE — backend（dsagents）

> Analysis Date: 2026-07-22。以下是权威源码布局；`backend/build/`、`backend/dist/`、`dsagents.egg-info/` 不是源码，不读也不提交。

## 目录树

```text
backend/
├── api.py                          # FastAPI 应用：四 HTTP 端点 + usage 计价
├── pyproject.toml                  # 包元数据、依赖、package-data（Skill 资源）
├── uv.lock                         # uv 锁定依赖（包管理以 uv 为准）
├── .env.example                    # 环境变量模板（勿把真实 .env 当文档源）
│
├── runtime/                        # 运行时核心包
│   ├── __init__.py                 # 稳定导出：AgentResources、create_harness、ledger
│   ├── agent.py                    # Brain/BrainFactory Protocol、DeepAgentsBrainFactory、denylist
│   ├── execution.py                # HarnessRuntime.execute_run、stream 投影、cancel
│   ├── middleware.py               # Recovery / Telemetry / NoProgress / Compatibility / Memory
│   ├── tools.py                    # ToolCatalog + default_tool_catalog（五工具）
│   ├── resources.py                # ResourceConfig、AgentResources、CompositeBackend
│   ├── runs.py                     # SqliteRunLedger、RunSnapshot、RunEvent、七类事件写入
│   ├── observability.py            # 纯 chunk 抽取（thinking/text/usage/tool_calls）
│   └── oms_log.py                  # OMS run_created JSONL 旁路索引
│
├── integrations/                   # 外部集成（无业务 schema）
│   ├── __init__.py
│   ├── artifacts.py                # /artifacts 解析、上传命名、JSON artifact 读写
│   └── mineru.py                   # parse_documents、extract_archives（MinerU HTTP）
│
├── skills/                         # 业务 Skill：下划线命名包内同时放资源与代码
│   ├── __init__.py
│   ├── channel_contract.py         # 共享 OrderItem(24)、RecognitionProblem、outcome 校验
│   ├── philips_wgq_inbound_recognition/   # 可 import 包，也是 /skills/ 资源目录
│   │   ├── __init__.py             # 导出 WAG_WORKFLOW（值为 WGQ）、PhilipsWgqRecognitionResult
│   │   ├── SKILL.md
│   │   ├── references/
│   │   │   └── freight-forwarders.md      # DHL / DSV / FedEx / UPS / 康捷空版式提示
│   │   ├── schema.py               # OrderHeader、RecognitionData、result
│   │   └── scripts/
│   │       ├── __init__.py
│   │       └── tools.py            # lookup_philips_wgq_master_data（共享 12NC；WGQ Tracking + Oracle）
│   └── tecan_import/               # 可 import 包，也是 /skills/ 资源目录
│       ├── __init__.py
│       ├── SKILL.md
│       ├── references/
│       │   ├── fields.md
│       │   └── rules.md
│       ├── schema.py               # TecanHeader、TecanOverseasRecognitionResult
│       └── scripts/
│           ├── __init__.py
│           └── tools.py            # inspect_supply_chain_workbooks、finalize_tecan_...
│
├── tests/                          # 可执行 assert 脚本（python -m tests.<name>，非 pytest）
│   ├── __init__.py
│   ├── test_support.py             # 测试辅助
│   ├── test_tools.py
│   ├── test_run_ledger.py
│   ├── test_harness.py
│   ├── test_api.py
│   ├── test_workflow_setup.py
│   ├── test_philips_wgq_inbound_recognition.py
│   ├── test_tecan_import.py
│   ├── test_minimax_cache_baseline.py
│   └── test_real_*.py              # 真实模型 / MinerU / 外部依赖（与本地回归分开）
│
├── .planning/
│   └── codebase/                   # 本目录：实现事实文档（Analysis Date 见各文件）
│       ├── ARCHITECTURE.md
│       ├── STRUCTURE.md
│       ├── STACK.md
│       ├── CONVENTIONS.md
│       ├── INTEGRATIONS.md
│       ├── TESTING.md
│       └── CONCERNS.md
│
├── data/                           # 运行时数据（非源码）
│   ├── dsagents_runs.db
│   ├── dsagents_checkpoints.db
│   ├── dsagents_store.db
│   ├── artifacts/
│   │   ├── uploads/
│   │   └── downloads/
│   └── internal/
│       └── run-events/             # 超大事件 payload 落盘
│
└── log/                            # 运行时日志（非源码）
    └── oms_log.log
```

## 目录用途

| 目录 / 文件 | 用途 |
|-------------|------|
| `api.py` | 唯一 HTTP 入口：上传、创建/查询/取消 run；进程内 session 单飞；usage 聚合计价 |
| `runtime/` | run-first 执行核心：Brain 装配、harness、middleware、ledger、资源、工具目录 |
| `integrations/` | 外部 I/O：artifact 虚拟路径、MinerU HTTP 解析与解压 |
| `skills/` | 渠道业务 Skill（资源 + 代码同包）与共享 JSON 合同 |
| `tests/` | 本地 assert 门禁与可选真实集成脚本 |
| `.planning/codebase/` | backend 实现事实文档（本文件所在处） |
| `data/` | 运行时三 SQLite、artifacts、超大事件落盘（非 VCS 权威源码） |
| `log/` | OMS JSONL 等运行日志 |
| `pyproject.toml` + `uv.lock` | 发行名 `dsagents`、依赖锁定、Skill package-data |

## 关键文件位置

| 路径 | 职责 |
|------|------|
| `api.py` | `POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`；session 单飞；usage 聚合计价 |
| `runtime/agent.py` | `Brain` / `BrainFactory` Protocol；`DeepAgentsBrainFactory`；WGQ `ToolStrategy`；WGQ/DK denylist |
| `runtime/execution.py` | `HarnessRuntime`：归一化消息、stream → 七类事件、Philips structured_response、Tecan finalizer 捕获、协作 cancel |
| `runtime/middleware.py` | `StructuredOutputRecovery`、`ToolTelemetry`、`NoProgressMiddleware`、`StructuredOutputCompatibility`、`runtime_middlewares` |
| `runtime/tools.py` | 五工具静态注册唯一入口 |
| `runtime/resources.py` | 三 SQLite 路径、`CompositeBackend` 路由、`/memories/AGENTS.md` baseline |
| `runtime/runs.py` | run 状态机、append-only `run_events`、投影 `runs` |
| `runtime/observability.py` | 无 I/O 的 stream 元数据抽取 |
| `runtime/oms_log.py` | best-effort `run_created` JSONL |
| `integrations/artifacts.py` | 虚拟路径、`clean_filename` / `make_timestamped_name`、`write_json_artifact` |
| `integrations/mineru.py` | MinerU 提交/轮询/下载；ZIP 解压 |
| `skills/channel_contract.py` | 渠道共用 24 字段 item 与 outcome 语义 |
| `skills/philips_wgq_inbound_recognition/schema.py` | `WAG_WORKFLOW`（值为 `WGQ`）、`PhilipsWgqRecognitionResult` |
| `skills/philips_wgq_inbound_recognition/scripts/tools.py` | 共享 12NC 主数据（WGQ Tracking + Oracle） |
| `skills/tecan_import/schema.py` | `DK_WORKFLOW`、`TecanOverseasRecognitionResult` |
| `skills/tecan_import/scripts/tools.py` | XLSX 检查 + finalizer（无 Excel 生成） |
| `pyproject.toml` | `dsagents` 发行、`package-data` 打包各 Skill 包内 `SKILL.md` / references |

## 命名约定

### Skill 单目录（硬约束）

每个业务 Skill 只保留一个下划线命名、可 import 的 Python 包；Agent 资源和运行时代码放在同一目录：

| 目录 | 挂载 / 导入 | 内容 |
|------|-------------|------|
| `skills/philips_wgq_inbound_recognition/` | `/skills/` / `from skills.philips_wgq_inbound_recognition import ...` | `SKILL.md`、`references/`、`schema.py`、`scripts/tools.py` |
| `skills/tecan_import/` | `/skills/` / `from skills.tecan_import import ...` | `SKILL.md`、`references/`、`schema.py`、`scripts/tools.py` |

`pyproject.toml` `[tool.setuptools.package-data]` 必须按包列出 `SKILL.md` 与 `references/*.md`，否则 wheel 中 Agent 读不到资源：

```toml
[tool.setuptools.package-data]
"skills.philips_wgq_inbound_recognition" = [
    "SKILL.md",
    "references/*.md",
]
"skills.tecan_import" = [
    "SKILL.md",
    "references/*.md",
]
```

新增 Skill 步骤：

1. 新建下划线命名 Python 包，并在包内放置 `SKILL.md`（及 references）、schema / tools
2. 在 `runtime/tools.py` **静态**注册新工具
3. 更新 `package-data`、tests、codebase 文档

禁止：把同一业务拆成资源目录和代码包、自动扫描注册、把业务-only 工具做成 workflow allowlist。

### 其它命名

| 类别 | 约定 | 示例 |
|------|------|------|
| 包 / 模块 | snake_case | `runtime`、`philips_wgq_inbound_recognition` |
| workflow 标识 | 大写渠道代码 | `WGQ`、`DK` |
| 工具函数 | snake_case callable `__name__` | `parse_documents`、`finalize_tecan_overseas_recognition` |
| 虚拟路径 | POSIX 风格前缀 | `/artifacts/...`、`/skills/...`、`/memories/...` |
| 主 Agent 名 | 常量 | `dsagents-main`（`MAIN_AGENT_NAME`） |
| 事件 type | snake_case 固定七类 | `status`、`text_delta`、… |
| 测试模块 | `test_<area>.py`，`python -m tests.<name>` | `tests.test_harness` |
| 数据文件 | `dsagents_*.db` | `dsagents_runs.db` |

Tecan **不再**携带 `assets/` Excel 模板或生成器；`openpyxl` 仅用于读取用户上传的 `.xlsx` 与 Philips Tracking。

## 包与模块边界

```text
api.py
  → runtime.execution (create_harness, HarnessRuntime)
  → runtime.resources (AgentResources, ResourceConfig)
  → runtime.oms_log
  → integrations.artifacts（上传命名）

runtime.execution
  → runtime.agent (BrainFactory)
  → runtime.middleware
  → runtime.tools
  → runtime.observability
  → runtime.runs
  → skills.philips_wgq_inbound_recognition / skills.tecan_import

runtime.agent
  → runtime.middleware
  → skills.philips_wgq_inbound_recognition

runtime.tools
  → integrations.mineru
  → skills.*.scripts.tools

skills.* tools
  → integrations.artifacts
  （共享 lookup 可选 oracledb；Philips / Tecan 均用 openpyxl）
```

`typing.Protocol` **只**用于 `Brain` / `BrainFactory`。工具不用 Protocol；ledger / resources 用具体类。

setuptools 布局（`pyproject.toml`）：

- `py-modules = ["api"]` — 顶层 `api.py`
- `packages.find` include：`runtime*`、`integrations*`、`skills*`
- 发行名 `dsagents`；Python `>=3.11,<4.0`

## 哪些目录不是源码

| 路径 | 说明 |
|------|------|
| `backend/build/` | setuptools 历史构建产物（已由 `.gitignore` 忽略）；**不要**当源码读入或提交 |
| `backend/dist/` | 打包 wheel 输出 |
| `backend/dsagents.egg-info/` | egg 元数据 |
| `backend/__pycache__/`、`**/__pycache__/` | 字节码缓存 |
| `backend/data/` | 运行时 SQLite 与 artifacts |
| `backend/log/` | OMS 与其它运行日志 |
| `backend/.env` | 密钥与连接串；分析文档不读取其内容 |
| `backend/.oracle/` | 随仓库分发的 Windows Oracle Instant Client 二进制；供 thick mode 默认回退 |
| `backend/.venv/` | 虚拟环境（若存在） |

权威源码入口：`backend/` 顶层 `api.py` + `runtime/` + `integrations/` + `skills/` + `tests/` + `pyproject.toml`。

## 放置新代码

| 需求 | 放置位置 |
|------|----------|
| 新渠道抽取 Skill | 单一下划线包（`SKILL.md` / references / schema / `tools.py`）+ 静态注册 |
| 共享终态 JSON 语义 | `skills/channel_contract.py` |
| 渠道专属 header / 证据规则 | 该渠道 `schema.py` / `SKILL.md` / `references/` |
| 跨模型/工具横切行为 | 评估后放 `runtime/middleware.py` |
| 单一业务终态校验 | 优先做成工具（如 Tecan finalizer），不新增 middleware state |
| 外部 HTTP / 文件 I/O 工具 | `integrations/` 或 Skill `scripts/tools.py` |
| 新 HTTP 能力 | 默认不加端点；无 SSE / session API / 业务状态表 / 任务队列 |

路径约定：模型侧只使用 `/artifacts/...` 与 `/skills/...` 虚拟路径，不向模型传递本机绝对路径。

## 快速入口

| 目标 | 入口 |
|------|------|
| HTTP 应用 | `api.create_app()` / `api.app` |
| 程序内执行 | `runtime.execution.create_harness(resources)` |
| Philips 合同 | `skills.philips_wgq_inbound_recognition.schema.PhilipsWgqRecognitionResult` |
| Tecan 合同 | `skills.tecan_import.schema.TecanOverseasRecognitionResult` |
| 共享 24 字段 | `skills.channel_contract.OrderItem` |
| 五工具注册 | `runtime.tools.default_tool_catalog` |
| 本地测试 | `cd backend && python -m tests.test_<name>` |
