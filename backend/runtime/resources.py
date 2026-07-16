from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

from runtime.runs import SqliteRunLedger

# 数据目录固定在 backend/ 下，与 CWD 无关。
_BACKEND_DIR = Path(__file__).resolve().parents[1]

# 共享运行时操作手册（StoreBackend，路径 /memories/）。
RUNTIME_AGENTS_PATH = "/memories/AGENTS.md"

# 仅在缺失时写入一次；不覆盖人工或智能体追加内容。
RUNTIME_AGENTS_BASELINE = """# 运行时操作手册

跨 run 共享。请遵循下列工具使用约定。

## parse_documents 的 ZIP / 结果消费

- 调用 `parse_documents` 后，若有 JSON `result_path`，优先用 `read_file` 读取该文件。
- 若工具返回 `archive_path`（`.zip`），**不要**把 zip 当 UTF-8 文本 `read_file`。
- ZIP 输出：先对 `archive_path` 调用 `extract_archives`，再对解压出的文本/Markdown 路径 `read_file`。

## 工具误用笔记（仅追加）

工具失败且属于可复用的误用模式时，追加一条短记录：

```
### <tool_name>
- 错误: <失败现象>
- 下一步: <正确下一步>
```

只写已验证的工具误用模式。不要写业务数据、用户偏好、密钥、私有路径、完整文件内容或未验证猜测。
"""


@dataclass(frozen=True)
class ResourceConfig:
    data_dir: Path = _BACKEND_DIR / "data"

    @property
    def run_db(self) -> Path:
        return self.data_dir / "dsagents_runs.db"

    @property
    def store_db(self) -> Path:
        return self.data_dir / "dsagents_store.db"

    @property
    def checkpoint_db(self) -> Path:
        return self.data_dir / "dsagents_checkpoints.db"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def run_events_dir(self) -> Path:
        return self.data_dir / "internal" / "run-events"

    @property
    def skills_dir(self) -> Path:
        return _BACKEND_DIR / "skills"


class AgentResources:
    def __init__(self, config: ResourceConfig | None = None) -> None:
        self.config = config or ResourceConfig()
        self._stack = ExitStack()

    def __enter__(self) -> "AgentResources":
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.config.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.runs = SqliteRunLedger(self.config.run_db, self.config.run_events_dir)

        self.store = self._stack.enter_context(SqliteStore.from_conn_string(str(self.config.store_db)))
        self.store.setup()
        self.checkpointer = self._stack.enter_context(
            SqliteSaver.from_conn_string(str(self.config.checkpoint_db))
        )
        self.checkpointer.setup()

        persistent = StoreBackend(store=self.store, namespace=lambda _rt: ("dsagents",))
        disk = FilesystemBackend(root_dir=self.config.artifacts_dir.resolve(), virtual_mode=True)
        skills = FilesystemBackend(root_dir=self.config.skills_dir.resolve(), virtual_mode=True)
        self.backend = CompositeBackend(
            default=StateBackend(),
            routes={
                "/memories/": persistent,
                "/artifacts/": disk,
                "/large_tool_results/": disk,
                "/skills/": skills,
            },
        )
        _ensure_runtime_agents(self.backend)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stack.close()


def _ensure_runtime_agents(backend: CompositeBackend) -> None:
    """Write the baseline handbook only when the shared file is missing."""
    existing = backend.read(RUNTIME_AGENTS_PATH)
    if existing.error is None and existing.file_data is not None:
        content = existing.file_data.get("content")
        if isinstance(content, str) and content.strip():
            return
    backend.write(RUNTIME_AGENTS_PATH, RUNTIME_AGENTS_BASELINE)
