# TESTING — backend 测试体系事实

> Analysis Date: 2026-07-22。测试是可直接执行的 assert 脚本，不使用 pytest 收集器。

## 运行方式

```powershell
cd backend
uv sync
python -m tests.<name>
```

不要用 `pip install -e .` 绕过 `uv.lock`。默认本地门禁使用 fake brain、临时 SQLite 和 mock；不调用真实模型、MinerU、Oracle 或外部 HTTP。

## 本地回归清单

| 命令 | 主要证据 |
|---|---|
| `python -m tests.test_tools` | 五工具静态目录与工具基础行为 |
| `python -m tests.test_run_ledger` | `runs` / append-only `run_events` 投影、查询与时区 |
| `python -m tests.test_harness` | stream 归一化、cancel、Philips structured recovery、Tecan finalizer 到 `run.result` 的捕获 |
| `python -m tests.test_api` | 四 HTTP 端点、workflow/session 规则、轮询结果与 OMS best-effort 索引 |
| `python -m tests.test_workflow_setup` | Philips denylist、五工具表、无业务 SubAgent、Skill 长度与 middleware 装配 |
| `python -m tests.test_philips_wgq_inbound_recognition` | Philips schema、24 字段商品行、格式化、Tracking/Oracle 降级 |
| `python -m tests.test_tecan_import` | XLSX 只读 artifact、Tecan 最终 JSON、24 字段、空白转 `null`、数值格式与 outcome |

建议按表中顺序全量运行；改动 `runtime/middleware.py` 时至少运行 `test_harness`，改工具注册或 Philips workflow 时至少运行 `test_workflow_setup`。

## 渠道供应链合同覆盖

- Philips 与 Tecan 的 `items[]` 都必须由共享 `OrderItem` 校验为完整 24 字段；未知值是 `null`，不是空字符串。
- 数量、金额、重量在 JSON 中为不带千分位、非科学计数法的字符串；日期序列化为 `YYYY-MM-DD`。
- `input_problems` 仍含完整 `data.header` 和已证实 `items`，可为空数组，并至少带一条 `{source, location, issue, action}`。
- Philips 的 schema 由 ToolStrategy 返回；Tecan 的 `finalize_tecan_overseas_recognition` 结果由执行层从对应 ToolMessage 投影到 `run.result`。
- 当前脚本验证合同和运行时投影，不以 mock 代替真实材料的角色识别质量。

## 真实集成与残余空白

- 真实模型、MinerU、Oracle 及本地样本回归均是 opt-in / 环境依赖测试，不能作为默认门禁的前提。
- 默认测试不证明模型必然按上传顺序识别复杂多票 PDF/XLSX；真实样本应另外验证同票归集、冲突裁决和主数据唯一匹配。
- `FakeBrain` 为事件过滤、用量统计等历史场景仍可模拟 subagent 元数据；这不表示生产 `DeepAgentsBrainFactory` 仍会创建 Tecan SubAgent。

## 文档检查

所有 backend 改动先同步本目录的事实文档，再更新根级架构/接口/系统地图。根目录运行：

```powershell
git diff --check
```
