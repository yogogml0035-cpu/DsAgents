from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

from session import SqliteSessionStore

# 数据目录固定在 backend/ 下，与 CWD 无关。
_BACKEND_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ResourceConfig:
    data_dir: Path = _BACKEND_DIR / "data"

    @property
    def session_db(self) -> Path:
        return self.data_dir / "dsagents_sessions.db"

    @property
    def store_db(self) -> Path:
        return self.data_dir / "dsagents_store.db"

    @property
    def checkpoint_db(self) -> Path:
        return self.data_dir / "dsagents_checkpoints.db"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"


class AgentResources:
    def __init__(self, config: ResourceConfig | None = None) -> None:
        self.config = config or ResourceConfig()
        self._stack = ExitStack()

    def __enter__(self) -> "AgentResources":
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.config.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.sessions = SqliteSessionStore(self.config.session_db, self.config.artifacts_dir)

        self.store = self._stack.enter_context(SqliteStore.from_conn_string(str(self.config.store_db)))
        self.store.setup()
        self.checkpointer = self._stack.enter_context(
            SqliteSaver.from_conn_string(str(self.config.checkpoint_db))
        )
        self.checkpointer.setup()

        persistent = StoreBackend(store=self.store, namespace=lambda _rt: ("dsagents",))
        disk = FilesystemBackend(root_dir=self.config.artifacts_dir.resolve(), virtual_mode=True)
        self.backend = CompositeBackend(
            default=StateBackend(),
            routes={
                "/memories/": persistent,
                "/conversation_history/": persistent,
                "/logs/": persistent,
                "/artifacts/": disk,
                "/large_tool_results/": disk,
            },
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stack.close()
