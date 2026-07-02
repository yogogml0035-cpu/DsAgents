# DsAgents Backend

当前仓库用 `requirements.txt` 管理 Python 依赖，还没有 `pyproject.toml` 或 `uv.lock`。如果你只是想把后端环境装起来，最短路径就是：创建 `.venv`，安装依赖，跑离线自检。

## 快速开始

```powershell
cd D:\AgentProject\DsAgents
uv python install 3.12
uv venv .venv --python 3.12
.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
python -m backend.self_check
```

看到 `self-check passed` 就说明后端环境可用。

## 1. 安装 uv

如果本机还没装 `uv`，先装它。

PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

或者用 `winget`：

```powershell
winget install --id=astral-sh.uv -e
```

装完后确认：

```powershell
uv --version
```

## 2. 创建 `.venv`

仓库当前已经在 `Python 3.12` 上验证过，推荐直接用它：

```powershell
cd D:\AgentProject\DsAgents
uv python install 3.12
uv venv .venv --python 3.12
```

激活虚拟环境：

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

`cmd.exe`：

```bat
.venv\Scripts\activate.bat
```

macOS / Linux:

```bash
source .venv/bin/activate
```

## 3. 安装后端依赖

```powershell
uv pip install -r requirements.txt
```

当前仓库没有锁文件，所以这里是按 `requirements.txt` 解析并安装依赖，不追求完全可复现版本。

## 4. 运行前配置

默认模型来自 `backend/harness.py`，默认值是 `openai:gpt-5.5`。真实跑会话前，至少要准备模型提供方的凭证。

PowerShell:

```powershell
$env:OPENAI_API_KEY = "your-openai-api-key"
```

如果要换模型，可以额外设置：

```powershell
$env:DSAGENTS_MODEL = "openai:gpt-5.5"
```

补充说明：

- 文档解析工具会固定访问 `http://10.11.0.110:6006` 上的 MinerU 服务。
- `backend.self_check` 是离线自检，不依赖真实模型调用，也不依赖 MinerU。
- 运行过程中生成的数据默认写到 `data/`。

## 5. 验证安装

离线自检：

```powershell
python -m backend.self_check
```

预期输出末尾包含：

```text
self-check passed
```

项目当前没有 `pytest` 套件；按仓库约定，离线自检入口就是这条命令。

## 6. 运行真实会话

最小示例：

```powershell
python -m backend "hello"
```

带固定 `session_id`：

```powershell
python -m backend "hello" --session-id demo-1
```

如果要让 Agent 调用 MinerU 解析文档，可以这样传消息：

```powershell
python -m backend "请解析 D:/docs/demo.pdf 并总结重点" --session-id demo-parse
```

这一步要求：

- `OPENAI_API_KEY` 已配置；
- MinerU 服务 `http://10.11.0.110:6006` 可访问。

## 7. 常用维护命令

重新安装依赖：

```powershell
uv pip install -r requirements.txt --upgrade
```

退出虚拟环境：

```powershell
deactivate
```

删除并重建环境：

```powershell
deactivate
Remove-Item -LiteralPath .venv -Recurse -Force
uv venv .venv --python 3.12
.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
```
