from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    created_at: str


@dataclass(frozen=True)
class SessionEvent:
    event_id: int
    session_id: str
    event_type: str
    created_at: str
    payload: Any
    artifact_path: str | None = None


@dataclass(frozen=True)
class ContextWindow:
    session_id: str
    messages: list[dict[str, Any]]
    source_event_ids: tuple[int, ...]


class SessionStore(Protocol):
    def ensure_session(self, session_id: str) -> SessionRecord: ...

    def get_session(self, session_id: str) -> SessionRecord: ...

    def get_events(self, session_id: str, after_event_id: int | None = None) -> list[SessionEvent]: ...

    def emit_event(self, session_id: str, event_type: str, payload: Any) -> SessionEvent: ...

    def context_window(self, session_id: str) -> ContextWindow: ...


class SqliteSessionStore:
    CONTEXT_MESSAGE_LIMIT = 20

    def __init__(self, db_path: Path, artifacts_dir: Path, max_inline_bytes: int = 262_144) -> None:
        self.db_path = db_path
        self.artifacts_dir = artifacts_dir / "session-events"
        self.max_inline_bytes = max_inline_bytes
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._setup()

    def ensure_session(self, session_id: str) -> SessionRecord:
        created_at = _utcnow()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                insert or ignore into sessions(session_id, created_at)
                values (?, ?)
                """,
                (session_id, created_at),
            )
            conn.commit()
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                """
                select session_id, created_at
                from sessions
                where session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        return SessionRecord(session_id=row[0], created_at=row[1])

    def get_events(self, session_id: str, after_event_id: int | None = None) -> list[SessionEvent]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            if after_event_id is None:
                rows = conn.execute(
                    """
                    select event_id, session_id, event_type, created_at, payload_json, artifact_path
                    from session_events
                    where session_id = ?
                    order by event_id
                    """,
                    (session_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    select event_id, session_id, event_type, created_at, payload_json, artifact_path
                    from session_events
                    where session_id = ? and event_id > ?
                    order by event_id
                    """,
                    (session_id, after_event_id),
                ).fetchall()
        return [self._read_event(row) for row in rows]

    def emit_event(self, session_id: str, event_type: str, payload: Any) -> SessionEvent:
        self.ensure_session(session_id)
        created_at = _utcnow()
        raw_payload = json.dumps(_safe(payload), ensure_ascii=False)
        payload_json = raw_payload
        artifact_path = None

        if len(raw_payload.encode("utf-8")) > self.max_inline_bytes:
            artifact = self.artifacts_dir / f"{uuid.uuid4().hex}.json"
            artifact.write_text(raw_payload, encoding="utf-8")
            artifact_path = str(artifact)
            payload_json = json.dumps(
                {"artifact_path": artifact_path, "bytes": len(raw_payload.encode("utf-8"))},
                ensure_ascii=False,
            )

        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.execute(
                """
                insert into session_events(session_id, event_type, created_at, payload_json, artifact_path)
                values (?, ?, ?, ?, ?)
                """,
                (session_id, event_type, created_at, payload_json, artifact_path),
            )
            conn.commit()
            event_id = int(cursor.lastrowid)

        return SessionEvent(
            event_id=event_id,
            session_id=session_id,
            event_type=event_type,
            created_at=created_at,
            payload=json.loads(raw_payload),
            artifact_path=artifact_path,
        )

    def context_window(self, session_id: str) -> ContextWindow:
        pairs = [
            (event.event_id, message)
            for event in self.get_events(session_id)
            if (message := _event_to_message(event)) is not None
        ]
        selected = pairs[-self.CONTEXT_MESSAGE_LIMIT :]
        while selected and selected[0][1].get("role") != "user":
            selected = selected[1:]
        return ContextWindow(
            session_id=session_id,
            messages=[message for _event_id, message in selected],
            source_event_ids=tuple(event_id for event_id, _message in selected),
        )

    def _read_event(self, row: tuple[Any, ...]) -> SessionEvent:
        payload_json = row[4]
        artifact_path = row[5]
        if artifact_path:
            payload = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        else:
            payload = json.loads(payload_json)
        return SessionEvent(
            event_id=int(row[0]),
            session_id=row[1],
            event_type=row[2],
            created_at=row[3],
            payload=payload,
            artifact_path=artifact_path,
        )

    def _setup(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                create table if not exists sessions (
                    session_id text primary key,
                    created_at text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists session_events (
                    event_id integer primary key autoincrement,
                    session_id text not null,
                    event_type text not null,
                    created_at text not null,
                    payload_json text not null,
                    artifact_path text
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_session_events_session_order
                on session_events(session_id, event_id)
                """
            )
            conn.commit()


def run_session(message: str, session_id: str | None = None) -> dict:
    from harness import create_harness
    from resources import AgentResources, ResourceConfig

    session_id = session_id or uuid.uuid4().hex
    with AgentResources(ResourceConfig()) as resources:
        return create_harness(resources).run_turn(message, session_id).result


def main() -> None:
    message = "你是谁"
    session_id = uuid.uuid4().hex
    result = run_session(message, session_id)
    print(result["messages"][-1].content)


def _event_to_message(event: SessionEvent) -> dict[str, Any] | None:
    if event.event_type not in {"user_message", "assistant_message"}:
        return None
    if not isinstance(event.payload, dict):
        return None
    role = event.payload.get("role")
    if role not in {"user", "assistant"}:
        return None
    return {"role": role, "content": event.payload.get("content", "")}


def _safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
