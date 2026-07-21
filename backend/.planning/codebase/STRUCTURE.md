# STRUCTURE — backend（dsagents）

> Analysis Date: 2026-07-22。以下是权威源码布局；`backend/build/` 不是源码，不读也不提交。

## 目录布局

```text
backend/
├── api.py                              # FastAPI 四端点
├── pyproject.toml / uv.lock             # uv 依赖与 package-data
├── runtime/
│   ├── agent.py                         # Brain factory、Philips ToolStrategy、denylist
│   ├── execution.py                     # run 执行、stream/result 投影
│   ├── middleware.py                    # recovery、telemetry、loop/compatibility
│   ├── resources.py / runs.py            # 三 SQLite 资源、ledger
│   ├── tools.py                          # 五工具静态注册
│   └── observability.py / oms_log.py
├── integrations/
│   ├── artifacts.py                      # /artifacts 路径与 JSON artifact
│   ├── mineru.py                         # PDF 解析、ZIP 解压
│   └── ...
├── skills/
│   ├── channel_contract.py               # 共享 24 字段 item、outcome/problem
│   ├── philips-wgq-inbound-recognition/  # Agent 资源：SKILL.md
│   ├── philipswgqinboundrecognition/     # schema.py、scripts/tools.py
│   ├── tecan-import/                     # Agent 资源：SKILL.md、references/
│   └── tecanimport/                      # schema.py、scripts/tools.py
├── tests/                                # 可执行 assert 脚本
└── .planning/codebase/                   # 当前事实文档
```

## 关键文件职责

| 文件 | 职责 |
|---|---|
| `api.py` | `POST /upload`、`POST /runs`、`GET /runs/{run_id}`、`POST /runs/{run_id}/cancel`；后台线程与 session 单飞 |
| `runtime/agent.py` | `DeepAgentsBrainFactory`，关闭默认子代理，Philips ToolStrategy 与工具 denylist |
| `runtime/execution.py` | 归一化 stream、写 ledger、捕获 Philips structured response 与 Tecan finalizer 结果 |
| `runtime/middleware.py` | 运行时通用 middleware；Philips 可选 `StructuredOutputRecovery` |
| `runtime/tools.py` | 五个 callable 的唯一静态注册点 |
| `skills/channel_contract.py` | `OrderItem`、`RecognitionProblem`、终态 outcome 共用校验 |
| `skills/*/schema.py` | 各渠道独立 header/data/result Pydantic schema |
| `skills/tecanimport/scripts/tools.py` | XLSX inspection 和 Tecan 最终 JSON 校验；没有 Excel 生成器 |

## Skill 成对规则

资源目录采用 kebab-case 并由 DeepAgents 挂载到 `/skills/`；Python 包用合法 import 名。两者必须成对维护：

| 资源 | 包 | 当前内容 |
|---|---|---|
| `philips-wgq-inbound-recognition/` | `philipswgqinboundrecognition/` | workflow 指引、Philips header/schema、Tracking/Oracle lookup |
| `tecan-import/` | `tecanimport/` | Skill/references、Tecan header/schema、XLSX 输入与 finalizer |

`tecan-import/assets/` 不再含模板；`pyproject.toml` 只打包两个 `SKILL.md` 和 Tecan references。`openpyxl` 仍保留，因为上传 XLSX 是支持材料。

## 放置新代码

- 新渠道抽取：先加资源目录 + import 包，再新增 schema/工具，最后在 `runtime/tools.py` 显式注册并更新 package-data、tests 和文档。
- 共享最终 JSON 语义：放 `skills/channel_contract.py`；只属于渠道的 header/证据规则：放该渠道 schema/Skill/references。
- 要跨模型调用/工具调用的运行时横切行为：评估后放 `runtime/middleware.py`。单一业务终态校验优先做成工具，不新增 middleware state。
- 新 HTTP 端点、SSE、session API、业务状态表或任务队列默认不加；需要真实调用方需求才可改变此边界。

## 依赖方向与运行数据

```text
api.py → runtime/{execution,agent,tools,resources}
runtime → integrations + skills
skills → integrations.artifacts（仅所需工具）
tests → 所有上述模块
```

`backend/data/` 保存 artifacts 与 SQLite，`backend/log/` 保存 OMS JSONL；两者是运行时数据而非源码。路径在模型侧使用 `/artifacts/...` 与 `/skills/...`，不要传递本机绝对路径。

## 快速入口

- 运行时入口：`runtime.execution.create_harness(resources)`。
- Philips 合同：`skills.philipswgqinboundrecognition.schema.PhilipsWgqRecognitionResult`。
- Tecan 合同：`skills.tecanimport.schema.TecanOverseasRecognitionResult`。
- 共享 24 字段：`skills.channel_contract.OrderItem`。
- 测试入口：`python -m tests.test_<name>`，不是 pytest。
