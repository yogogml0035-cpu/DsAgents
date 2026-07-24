# DsAgents（Windows）部署与使用

本页只说明在 **Windows + PowerShell** 环境中如何配置运行环境、启动单实例服务并通过 HTTP 调用它。业务字段与完整接口合同请查阅 [INTERFACES.md](INTERFACES.md)，不要以本文示例替代接口合同。

## 1. 部署前准备

- 64 位 Windows 10/11 或 Windows Server，以及 PowerShell；本文所有命令均在 PowerShell 中执行。
- Python `>=3.11`；依赖和锁文件由 [`uv`](https://docs.astral.sh/uv/) 管理。
- 一个可用的 MiniMax Anthropic 兼容模型服务；这是创建 run 所必需的外部依赖。
- 如需解析上传的 PDF/Office 文档，还需要可访问的 MinerU 服务。
- 服务运行账号必须能读取 `backend/.env` 并写入 `backend/data/`；如需保留 OMS 创建索引，还应写入 `backend/log/`。
- 生产环境应使用受信内网或网关保护该服务。应用本身没有认证、上传配额或下载端点，不应直接暴露到公网。

当前仓库提供的是直接使用 `uv` 启动的后端服务，没有随仓库提供的容器编排配置。请先按下文完成单实例部署，再考虑接入现有的反向代理、监控和备份体系。

## 2. Windows 安装项目

在 PowerShell 中打开仓库根目录，先确认 Python 和 `uv` 可用：

```powershell
python --version
uv --version
```

Python 版本必须为 `3.11` 或更高；若未安装，请从 [Python for Windows](https://www.python.org/downloads/windows/) 安装 64 位版本并启用 `Add python.exe to PATH`，然后重新打开 PowerShell。缺少 `uv` 时，请按 [uv Windows 安装说明](https://docs.astral.sh/uv/getting-started/installation/) 安装并重新打开 PowerShell；不要用 `pip` 替代 `uv`。

然后安装锁定依赖：

```powershell
cd backend
uv sync
```

不要使用 `pip install -e .`；这会绕过仓库锁定的依赖版本。

```powershell
Copy-Item .env.example .env
```

`backend/.env` 已被 Git 忽略。只在该文件或部署平台的受管密钥存储中保存凭据，绝不提交它；不要直接复用示例中已有的服务地址，应替换为当前环境的资源。

## 3. 配置环境变量

服务会在导入运行时模块时自动读取 `backend/.env`。复制 `.env.example` 后，逐项替换为当前环境的真实配置；修改后必须重启服务才会生效。若部署平台已注入同名进程环境变量，它会优先于 `.env`，因此生产环境应只保留一个权威配置来源，避免两处值冲突。

### 模型服务（创建 run 必需）

| 变量 | 是否必填 | 填写说明 |
| --- | --- | --- |
| `MINIMAX_API_KEY` | 是 | 模型服务的访问密钥。 |
| `MINIMAX_BASE_URL` | 是 | 服务商提供的 Anthropic 兼容基础地址；应保留该服务所需的路径层级，不要填写单个模型或聊天接口地址。 |
| `MINIMAX_MODEL` | 是 | 已开通的模型标识。 |

这三项任一项错误时，服务进程可能仍能启动，但创建或执行 run 会失败。因此应在上线前用一次真实的最小请求验证模型连通性。

### MinerU 文档解析（解析文档时必需）

| 变量 | 是否必填 | 填写说明 |
| --- | --- | --- |
| `MINERU_BASE_URL` | 解析文档时是 | MinerU 服务的基础地址。程序会在其后请求任务接口，因此不要把单个任务接口路径填入此变量。 |
| `MINERU_BACKEND` | 解析文档时是 | 与所部署 MinerU 服务一致的后端名称。 |
| `MINERU_EFFORT` | 否 | MinerU 支持该参数时填写；不使用时保持为空。 |
| `MINERU_TIMEOUT_SECONDS` | 解析文档时是 | 正整数秒数，覆盖提交、轮询和下载的单次超时上限；应按文件大小和 MinerU 吞吐量设置。 |

普通纯文本请求可以不触发 MinerU；但上传文档的常规使用，以及 `WGQ`、`DK` 工作流，都应将前三个必需项配置完整并验证网络可达。

### Oracle 主数据查询（仅 Philips WGQ，可选）

| 变量 | 是否必填 | 填写说明 |
| --- | --- | --- |
| `ORACLE_DSN` | 一组同时配置 | Oracle 连接描述。 |
| `ORACLE_USERNAME` | 一组同时配置 | Oracle 用户名。 |
| `ORACLE_PASSWORD` | 一组同时配置 | Oracle 密码。 |
| `ORACLE_CLIENT_LIB_DIR` | 否 | 仅使用 Oracle thick mode 时填写，必须是包含 Instant Client 库文件的绝对目录；留空时使用 thin mode。 |
| `ORACLE_TIMEOUT_SECONDS` | 否 | 正数秒；未设置时程序使用默认超时。 |

`ORACLE_DSN`、`ORACLE_USERNAME`、`ORACLE_PASSWORD` 必须同时存在。未配置或查询失败时，WGQ 会以业务问题提示或空字段降级，不会因为主数据查询直接阻塞已创建的 run；仍应由业务方决定是否接受该结果。

### 不属于服务配置的变量

以 `DSAGENTS_RUN_REAL_` 开头的变量只用于选择性运行真实集成测试，例如真实图片、PDF 或渠道样例验收。它们不是生产服务配置，不应作为部署所需项写进 `.env`。完整测试开关见 [docs/commands.md](docs/commands.md)。

## 4. 数据目录、权限与备份

服务的数据目录固定在 `backend/data/`，没有可用的 `DATA_DIR` 环境变量。部署时应将整个目录保留在持久磁盘上，升级代码时不要覆盖或删除它。

| 路径 | 需要保留的内容 |
| --- | --- |
| `backend/data/dsagents_runs.db` | run 快照与事件账本。 |
| `backend/data/dsagents_checkpoints.db` | LangGraph checkpoint。 |
| `backend/data/dsagents_store.db` | 运行时 store。 |
| `backend/data/artifacts/` | 上传文件和文档解析输出。 |
| `backend/data/internal/run-events/` | 较大事件的外置内容。 |
| `backend/log/oms_log.log` | best-effort 的 OMS 创建索引日志；写入失败不会阻塞 run。 |

建议：

- 给 `backend/.env` 设置仅服务账号可读的权限。
- 将 `backend/data/` 和 `backend/log/` 纳入备份、容量监控和保留策略；应用不内置清理任务。
- 在停止服务后再做文件级备份，或使用基础设施提供的 SQLite 一致性快照。
- 备份中可能包含上传材料和业务结果，应按敏感数据处理。

## 5. 启动服务

从 `backend/` 目录启动：

```powershell
uv run uvicorn api:app --host 0.0.0.0 --port 8500
```

本地仅供快速使用时，也可以从仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1
```

该脚本会打开单独的 PowerShell 窗口，适合本地调试，不适合作为无人值守的生产托管方式。

> **生产部署必须保持单进程、单 worker。** 不要给 Uvicorn 配置 `--workers` 大于 `1`，也不要让多个进程共享同一个 `backend/data/`。当前 session 单飞、取消控制和 SQLite 存储均以单进程为边界；多 worker 或多副本会导致会话互斥和取消行为不可靠。

进程重启会将未完成的 run 标记为失败，客户端应以新 run 重试。因此发布前应先停止流量并等待活跃 run 结束，避免直接强制终止进程。

## 6. Windows 生产托管与网络边界

推荐让应用仅监听本机地址，由企业现有网关或反向代理提供 HTTPS、认证、访问控制和请求体大小限制：

```powershell
uv run uvicorn api:app --host 127.0.0.1 --port 8500
```

网关至少应限制可访问来源、要求认证，并限制上传大小。不要把 `8500` 直接映射到公网。

生产环境请使用组织认可的 Windows 服务管理器托管进程，而不是依赖交互式 PowerShell 窗口。服务配置应满足：

- 工作目录设为 `backend/` 的绝对路径。
- 启动参数为 `uv run uvicorn api:app --host 127.0.0.1 --port 8500`；可通过 `(Get-Command uv).Source` 查询 `uv.exe` 的绝对路径，供服务管理器填写可执行文件。
- 服务账号可读取 `backend/.env`、写入 `backend/data/`；如需保留 OMS 索引，再授予 `backend/log/` 写入权限。
- 启用失败自动重启，但发布或维护时应先等待活跃 run 完成。

不要将 `scripts/start-backend.ps1` 作为 Windows 服务入口：它会启动可见的独立 PowerShell 窗口，仅适合本地调试。

## 7. 部署验收

先运行不依赖真实模型、MinerU 或 Oracle 的本地门禁：

```powershell
cd backend
python -m tests.test_tools
python -m tests.test_run_ledger
python -m tests.test_harness
python -m tests.test_api
python -m tests.test_workflow_setup
python -m tests.test_philips_wgq_inbound_recognition
python -m tests.test_tecan_import
```

这些脚本是可执行的 `assert` 脚本，不是 pytest；必须用 `python -m tests.<name>` 运行。门禁通过后，用下一节的上传、创建和轮询流程做一次真实连通性验证。真实集成测试的开关和样例要求见 [docs/commands.md](docs/commands.md)。

## 8. 首次调用：上传、创建 run、轮询结果

HTTP 服务只有四个业务端点：

| 方法与路径 | 用途 |
| --- | --- |
| `POST /upload` | 上传材料，返回 `/artifacts/...` 路径。 |
| `POST /runs` | 创建异步 run，立即返回 `run_id` 和 `queued` 状态。 |
| `GET /runs/{run_id}` | 查询 run、事件、用量和最终 `result`。 |
| `POST /runs/{run_id}/cancel` | 请求协作式取消活跃 run。 |

下面的 PowerShell 示例上传一个文件、创建 run 并每两秒轮询一次。请将 `sample.pdf` 改为实际文件；Windows 10/11 自带的 `curl.exe` 可用于 multipart 上传。

```powershell
$base = "http://127.0.0.1:8500"
$file = (Resolve-Path ".\sample.pdf").Path

$upload = (
  & curl.exe --silent --show-error --fail -X POST "$base/upload" `
    -F "files=@$file"
) | ConvertFrom-Json

$request = @{
  messages = @(
    @{
      role = "user"
      content = @(
        @{ type = "text"; text = "请处理这个文件" }
        @{ type = "artifact"; path = $upload.files[0].file_path }
      )
    }
  )
}

$run = Invoke-RestMethod -Method Post -Uri "$base/runs" `
  -ContentType "application/json" `
  -Body ($request | ConvertTo-Json -Depth 8)

do {
  Start-Sleep -Seconds 2
  $state = Invoke-RestMethod -Method Get -Uri "$base/runs/$($run.run_id)"
} while ($state.run.status -notin @("succeeded", "failed", "cancelled"))

$state.run.status
$state.result
```

业务结果应读取顶层 `result`，不要从 `reply` 推断业务 JSON。长时间轮询事件时，使用 `GET /runs/{run_id}?after_event_id=<event_id>` 获取增量事件，避免反复读取全量事件。

如需取消仍在执行的 run：

```powershell
Invoke-RestMethod -Method Post -Uri "$base/runs/$($run.run_id)/cancel"
```

取消是协作式的：外部模型、MinerU 或 Oracle 请求已发出时，状态可能会短暂保持在 `cancelling`，直到当前步骤返回。

## 9. 使用 WGQ 或 DK 工作流

创建渠道识别请求时，在上一节构造的 `$request` 顶层增加 `workflow`，值只能是 `WGQ` 或 `DK`：

```powershell
$request["workflow"] = "WGQ"  # 或 "DK"
```

工作流请求不要传 `session_id`；服务会生成新的 session。仍按第 8 节轮询，终态业务 JSON 从顶层 `result` 获取。请求字段、各渠道结果结构和错误响应以 [INTERFACES.md](INTERFACES.md) 为准。

## 10. 常见问题

| 现象 | 优先检查 |
| --- | --- |
| 服务能启动，但 run 很快失败 | `MINIMAX_API_KEY`、`MINIMAX_BASE_URL`、`MINIMAX_MODEL` 是否完整；部署主机能否访问模型服务。 |
| 文档解析报缺少环境变量或超时 | `MINERU_BASE_URL`、`MINERU_BACKEND`、`MINERU_TIMEOUT_SECONDS`，以及 MinerU 服务连通性和文件大小。 |
| WGQ 中缺少 Oracle 主数据 | 三个 `ORACLE_*` 凭据是否同时配置；thick mode 时 `ORACLE_CLIENT_LIB_DIR` 是否为绝对库目录。未配 Oracle 时可降级为人工补齐。 |
| 同一会话收到 `409` | 前一个同 `session_id` 的 run 尚未结束；继续轮询或取消该 run 后再试。 |
| 取消长期停在 `cancelling` | 当前外部调用尚未返回；这是协作式取消的预期行为。 |
| 重启后 run 失败 | 运行中的后台线程不能跨进程恢复；以新的 run 重试，并在发布时先 drain 流量。 |
| SQLite 锁冲突、取消失效或会话并发异常 | 核对是否错误使用了多个 worker/副本，或多个进程共享了同一 `backend/data/`。 |

## 11. 延伸文档

- [docs/commands.md](docs/commands.md)：完整启动、测试和真实集成命令。
- [INTERFACES.md](INTERFACES.md)：请求、响应、状态码和业务结果合同。
- [ARCHITECTURE.md](ARCHITECTURE.md)：系统边界与运行时约束。
- [docs/channel-supply-chain-json-prd.md](docs/channel-supply-chain-json-prd.md)：渠道供应链 JSON 合同。
