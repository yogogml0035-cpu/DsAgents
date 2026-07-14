# 飞利浦外高桥进境智能识别 PRD

## 1. 目标与边界

DsAgents 接收一个外高桥进境批次的 1–10 个 PDF 和可选 1 个 Tracking `.xlsx`，识别运单、发票及商品行，合并为唯一真实票次，并返回可供 OMS 回填的、经 Pydantic 校验的结构化 JSON。

本功能只负责识别与稳定主数据补齐，不替用户确认或保存订单，不修改 OMS，不生成或回写 Excel，也不自动拆分混合票次。

包含：

- 运单与发票跨文档识别、关联和合并；
- Tracking 严格选行与稳定字段补齐；
- Oracle 对 Tracking 缺失稳定字段的可选补齐；
- 有效 PDF 部分成功、无关附件忽略、混入多个真实票次时拒绝合并；
- 异步 run、轮询与结构化 `result`。

不包含：

- ZIP 解包、DOCX、图片或其他 Excel 的内容解析；
- OMS 页面回填、覆盖规则、最终确认和保存；
- Philips 旧 A/B/C 投票、裁决、canonical artifact 或 Excel 单据生成；
- Tracking 回写、历史交易字段复用、重量分摊；
- 猜测 Oracle 未确认表列中的申报要素或 BU。

## 2. 调用合同

OMS 不拼接业务提示词，只传固定 workflow 与本批 artifact：

```json
{
  "workflow": "philips_wgq_inbound_recognition",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "artifact", "path": "/artifacts/uploads/waybill.pdf"},
        {"type": "artifact", "path": "/artifacts/uploads/invoice.pdf"},
        {"type": "artifact", "path": "/artifacts/uploads/tracking.xlsx"}
      ]
    }
  ]
}
```

调用顺序保持不变：

1. `POST /upload` 上传本票文件；
2. `POST /runs` 提交上述请求并立即取得 `queued` + `run_id`；
3. 轮询 `GET /runs/{run_id}`；
4. 终态从 GET 顶层 `result` 读取业务 JSON，`run.result` 为同一内容。

每个批次必须使用新的 LangGraph thread。调用方应省略 `session_id`（显式 `null` 也会由服务端生成）；非空 `session_id` 返回 `422`。未知 workflow 同样返回 `422`。`POST /runs` 不同步等待模型或 MinerU。

## 3. 结构化结果

```json
{
  "outcome": "success | partial_success | input_problems",
  "data": {
    "shipment": {
      "件数": null,
      "总毛重": null
    },
    "header": {
      "OM": null,
      "DN": null,
      "PO": null,
      "SO": null,
      "原运单号": null,
      "买方": null,
      "卖方": null,
      "发货人": null,
      "收货人": null,
      "付款方式": null,
      "合同号": null,
      "业务员": null,
      "发票号": null,
      "ETD": null,
      "启运港": null,
      "到货港": null
    },
    "items": [
      {
        "SO_ITEM": null,
        "12NC": null,
        "新旧": null,
        "中文品名": null,
        "规格型号": null,
        "库存数量": null,
        "单位": null,
        "币种": null,
        "单价": null,
        "总价": null,
        "原产国": null,
        "海关编码": null,
        "申报要素": null,
        "法一数量": null,
        "法一单位": null,
        "法二数量": null,
        "法二单位": null,
        "毛重": null,
        "净重": null,
        "BU": null,
        "售前/售后": null
      }
    ]
  },
  "problems": [
    {
      "source": "...",
      "location": "...",
      "issue": "...",
      "action": "..."
    }
  ]
}
```

合同规则：

- Pydantic 模型使用固定 JSON alias 与 `extra="forbid"`；`data` 非空时 `items` 至少一行。
- 所有固定字段必须出现；未识别值为 JSON `null`，不返回“未找到”“需确认”、`//` 或 `N/A` 等占位字符串。
- 数量、金额和重量使用无千分位十进制字符串，避免 JSON 浮点误差；`ETD` 明确时为 `YYYY-MM-DD`。
- `success`：形成唯一票次且无问题，`data` 非空、`problems=[]`。
- `partial_success`：形成唯一票次且有可回填数据，但存在无关附件、个别 PDF/字段问题、Tracking/Oracle 失败或未命中；`data` 非空且 `problems` 至少一项。
- `input_problems`：无法安全形成唯一票次；`data=null` 且 `problems` 至少一项。
- `input_problems` 是业务结果，仍对应 `run.status=succeeded`。只有模型、运行时或结构化响应缺失/非法才对应 `run.status=failed`。
- `reply` 仅作人类摘要，不参与业务 JSON 解析。

## 4. 单据识别与票次规则

1. 一次解析本批全部 PDF，再统一识别运单、发票和商品行。
2. 使用共同运单号、发票号、PO、SO、DN、OM 以及一致的买卖方/收发货关系判断同一真实票次，不引入评分系统。
3. 商品行按发票上传顺序、再按发票原始行顺序输出；重复 12NC 保留为不同商品行，不合并。
4. 两个以上独立真实票次混入同一批次时返回 `input_problems`，不输出可能混票的数据，也不自动返回 `orders[]`。
5. 没有有效 PDF、超过 10 个 PDF、运单与发票无法关联时返回 `input_problems`。
6. ZIP、DOCX、图片和其他 Excel 不读取、不解包；记录 problem。只要有效 PDF 能形成唯一票次，继续返回 `partial_success`。
7. 单个 PDF 解析失败不应丢弃其他有效 PDF；是否能形成唯一票次决定最终 outcome。

## 5. 字段来源优先级

| 字段组 | 第一来源 | 第二来源 | 禁止来源 |
|---|---|---|---|
| 运单号、件数、总重量、买卖方、收发货方 | 本批运单/其他 PDF | — | Tracking、Oracle |
| 发票号、PO/SO/DN/OM、数量、单位、币种、单价、总价 | 本批发票/其他 PDF | — | Tracking、Oracle |
| 中文品名、规格型号、原产国、海关编码、法定单位、新旧 | 合格 Tracking | Oracle 已确认字段 | 不合格历史行 |
| 申报要素、BU | 合格 Tracking | `null` | 未确认 Oracle 表列、猜测 |
| 法一/法二数量 | 本批文件明确值或文件内确认的换算依据 | `null` | 仅凭单位推断 |
| 商品级毛重、净重 | 本批文件明确值 | `null` | 历史重量、shipment 总重量分摊 |

外高桥页面没有订单头件数和总毛重字段，因此响应保留 `shipment` 总量。多商品且没有明确行级重量时，不把总重量复制到每个商品行。

## 6. Tracking 确定性规则

专用工具为：

```text
lookup_philips_wgq_master_data(product_ids, tracking_artifact?)
```

固定规则：

1. 12NC 去除空格和连字符；超过 12 位取末 12 位；重复请求料号只查询一次。
2. Tracking 必须是存在的 `.xlsx`，只读取 `进口` sheet。
3. 从最后一行向前查找；必须同时满足标准化 12NC 相同，且 A 列 trim 后严格等于 `进口`、`出1`、`出2` 或 `已出`。
4. 返回遇到的第一条合格行，即行号最大的合格记录；不得回退到空白状态、其他状态或备注包含“进口”的行。
5. 只有命中合格进口行，才允许读取同料号的 `申报要素` sheet 记录。
6. Tracking 内部优先使用 `申报要素` sheet 的稳定字段，缺失再使用已选进口行；`Modality` 映射为 `BU`。
7. 主数据工具不暴露 Tracking 的旧数量、价格、单号、金额或重量，从返回边界上阻止污染本票交易字段。

## 7. Oracle 补齐与降级

- Oracle 仅查询 Tracking 仍缺失的中文品名、规格型号、原产国、海关编码、申报计量单位及法定第一/第二单位。
- Tracking 已有非空值不得被 Oracle 覆盖；申报要素和 BU 不查未确认表列。
- 查询使用参数化 `:product_id`；可选配置键为 `ORACLE_DSN`、`ORACLE_USERNAME`、`ORACLE_PASSWORD`、`ORACLE_CLIENT_LIB_DIR`、`ORACLE_TIMEOUT_SECONDS`。
- 配置缺失、client/查询失败或单个料号未命中均写入 `problems`；保留 PDF/Tracking 数据并由 Agent 返回 `partial_success`。
- 不增加缓存、主数据服务 Protocol、批处理框架、重试框架或新的 Oracle 配置键。

## 8. Run、事件与持久化

- `workflow` 写入 `runs.workflow`；验证后的结果 JSON 写入 `runs.result_json`。
- 终态 `status` 事件携带同一 `result`；不增加第八类事件。
- 继续使用 `queued → running → succeeded|failed|cancelled` 与协作 cancel；`result.outcome` 不扩展 run 状态机。
- `session_id` 仍只用于 LangGraph `thread_id` 和进程内单飞锁；Philips 每批使用新值以避免历史 thread 污染。
- 不新增自定义 middleware、workflow registry、HITL、恢复游标、票次 checkpoint 或新的 LangGraph state 字段。

## 9. 验收口径

普通 assert 回归必须覆盖：

- workflow 路由、新 session、未知 workflow/非法 session `422`；
- `structured_response` 捕获、Pydantic 复验、终态事件、SQLite 持久化与 GET 投影；
- 缺少结构化结果时 run 失败，`input_problems` 时 run 成功；
- Tracking 严格状态、倒序最新行、无空状态/备注 fallback、申报要素页优先；
- Tracking → Oracle → `null`，Oracle 只补缺失字段；
- 工具结果不含历史交易字段；重复 12NC 不合并商品行；
- Oracle 配置缺失、失败和未命中的降级。

真实 HTTP 验收脚本覆盖外部样例目录中的 DHL、DSV、FedEx、UPS、康捷空，逐例执行 upload → run → poll，并核对关键运单号、商品行、12NC、数量/价格、主数据工具调用和 ZIP/DOCX 忽略行为。真实模型、MinerU、Oracle 与普通本地回归保持分离。

