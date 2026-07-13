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
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stack.close()
