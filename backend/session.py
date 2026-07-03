from __future__ import annotations

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

RUN_STATUSES = {"queued", "running", "succeeded", "failed"}
RUN_PREVIEW_LIMIT = 200


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
class RunRecord:
    run_id: str
    session_id: str
    created_at: str


@dataclass(frozen=True)
class RunEvent:
    event_id: int
    run_id: str
    event_type: str
    created_at: str
    payload: Any
    raw: Any


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    session_id: str
    status: str
    created_at: str
    updated_at: str
    reply: str | None = None
    error: str | None = None
    reply_preview: str | None = None
    error_preview: str | None = None


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

    def create_run(self, session_id: str, run_id: str) -> RunSnapshot: ...

    def get_run(self, run_id: str) -> RunSnapshot: ...

    def list_runs(self, session_id: str) -> list[RunSnapshot]: ...

    def get_run_events(self, run_id: str, after_event_id: int | None = None) -> list[RunEvent]: ...

    def emit_run_event(
        self,
        run_id: str,
        event_type: str,
        payload: Any,
        *,
        raw: Any | None = None,
    ) -> RunEvent: ...

    def emit_run_status(
        self,
        run_id: str,
        status: str,
        *,
        reply: str | None = None,
        error: str | None = None,
        raw: Any | None = None,
    ) -> RunEvent: ...

    def fail_incomplete_runs(self, error: str) -> list[str]: ...


class SqliteSessionStore:
    CONTEXT_MESSAGE_LIMIT = 20

    def __init__(self, db_path: Path, artifacts_dir: Path, max_inline_bytes: int = 262_144) -> None:
        self.db_path = db_path
        self.session_artifacts_dir = artifacts_dir / "session-events"
        self.run_artifacts_dir = artifacts_dir / "run-events"
        self.max_inline_bytes = max_inline_bytes
        self.session_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.run_artifacts_dir.mkdir(parents=True, exist_ok=True)
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
        payload_json, artifact_path = self._store_blob(payload, self.session_artifacts_dir)
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
            payload=self._load_blob(payload_json, artifact_path),
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

    def create_run(self, session_id: str, run_id: str) -> RunSnapshot:
        self.ensure_session(session_id)
        created_at = _utcnow()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                insert into runs(run_id, session_id, created_at)
                values (?, ?, ?)
                """,
                (run_id, session_id, created_at),
            )
            conn.commit()
        self.emit_run_status(run_id, "queued")
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunSnapshot:
        record = self._get_run_record(run_id)
        return self._project_run(record, self.get_run_events(run_id))

    def list_runs(self, session_id: str) -> list[RunSnapshot]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                select run_id, session_id, created_at
                from runs
                where session_id = ?
                order by created_at desc
                """,
                (session_id,),
            ).fetchall()
        return [self._project_run(self._read_run_record(row), self.get_run_events(row[0])) for row in rows]

    def get_run_events(self, run_id: str, after_event_id: int | None = None) -> list[RunEvent]:
        self._get_run_record(run_id)
        with closing(sqlite3.connect(self.db_path)) as conn:
            if after_event_id is None:
                rows = conn.execute(
                    """
                    select
                        event_id,
                        run_id,
                        event_type,
                        created_at,
                        payload_json,
                        payload_artifact_path,
                        raw_json,
                        raw_artifact_path
                    from run_events
                    where run_id = ?
                    order by event_id
                    """,
                    (run_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    select
                        event_id,
                        run_id,
                        event_type,
                        created_at,
                        payload_json,
                        payload_artifact_path,
                        raw_json,
                        raw_artifact_path
                    from run_events
                    where run_id = ? and event_id > ?
                    order by event_id
                    """,
                    (run_id, after_event_id),
                ).fetchall()
        return [self._read_run_event(row) for row in rows]

    def emit_run_event(
        self,
        run_id: str,
        event_type: str,
        payload: Any,
        *,
        raw: Any | None = None,
    ) -> RunEvent:
        created_at = _utcnow()
        safe_payload = _safe(payload)
        safe_raw = safe_payload if raw is None else _safe(raw)
        payload_json, payload_artifact_path = self._store_blob(safe_payload, self.run_artifacts_dir)
        raw_json, raw_artifact_path = self._store_blob(safe_raw, self.run_artifacts_dir)
        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.execute(
                """
                insert into run_events(
                    run_id,
                    event_type,
                    created_at,
                    payload_json,
                    payload_artifact_path,
                    raw_json,
                    raw_artifact_path
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    event_type,
                    created_at,
                    payload_json,
                    payload_artifact_path,
                    raw_json,
                    raw_artifact_path,
                ),
            )
            conn.commit()
            event_id = int(cursor.lastrowid)
        return RunEvent(
            event_id=event_id,
            run_id=run_id,
            event_type=event_type,
            created_at=created_at,
            payload=self._load_blob(payload_json, payload_artifact_path),
            raw=self._load_blob(raw_json, raw_artifact_path),
        )

    def emit_run_status(
        self,
        run_id: str,
        status: str,
        *,
        reply: str | None = None,
        error: str | None = None,
        raw: Any | None = None,
    ) -> RunEvent:
        if status not in RUN_STATUSES:
            raise ValueError(f"Unsupported run status: {status}")
        payload: dict[str, Any] = {"status": status}
        if reply is not None:
            payload["reply"] = reply
            payload["reply_preview"] = _preview(reply)
        if error is not None:
            payload["error"] = error
            payload["error_preview"] = _preview(error)
        return self.emit_run_event(run_id, "status", payload, raw=payload if raw is None else raw)

    def fail_incomplete_runs(self, error: str) -> list[str]:
        failed_run_ids: list[str] = []
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                select run_id, session_id, created_at
                from runs
                order by created_at
                """
            ).fetchall()
        for row in rows:
            record = self._read_run_record(row)
            snapshot = self._project_run(record, self.get_run_events(record.run_id))
            if snapshot.status not in {"queued", "running"}:
                continue
            self.emit_run_status(
                record.run_id,
                "failed",
                error=error,
                raw={"status": "failed", "error": error, "reason": "startup_interrupted"},
            )
            failed_run_ids.append(record.run_id)
        return failed_run_ids

    def _get_run_record(self, run_id: str) -> RunRecord:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                """
                select run_id, session_id, created_at
                from runs
                where run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")
        return self._read_run_record(row)

    def _project_run(self, record: RunRecord, events: list[RunEvent]) -> RunSnapshot:
        status = "queued"
        updated_at = record.created_at
        reply = None
        error = None
        for event in events:
            updated_at = event.created_at
            if event.event_type != "status" or not isinstance(event.payload, dict):
                continue
            event_status = event.payload.get("status")
            if event_status in RUN_STATUSES:
                status = event_status
            if isinstance(event.payload.get("reply"), str):
                reply = event.payload["reply"]
            if isinstance(event.payload.get("error"), str):
                error = event.payload["error"]
        return RunSnapshot(
            run_id=record.run_id,
            session_id=record.session_id,
            status=status,
            created_at=record.created_at,
            updated_at=updated_at,
            reply=reply,
            error=error,
            reply_preview=_preview(reply),
            error_preview=_preview(error),
        )

    def _read_event(self, row: tuple[Any, ...]) -> SessionEvent:
        payload_json = row[4]
        artifact_path = row[5]
        return SessionEvent(
            event_id=int(row[0]),
            session_id=row[1],
            event_type=row[2],
            created_at=row[3],
            payload=self._load_blob(payload_json, artifact_path),
            artifact_path=artifact_path,
        )

    def _read_run_record(self, row: tuple[Any, ...]) -> RunRecord:
        return RunRecord(run_id=row[0], session_id=row[1], created_at=row[2])

    def _read_run_event(self, row: tuple[Any, ...]) -> RunEvent:
        return RunEvent(
            event_id=int(row[0]),
            run_id=row[1],
            event_type=row[2],
            created_at=row[3],
            payload=self._load_blob(row[4], row[5]),
            raw=self._load_blob(row[6], row[7]),
        )

    def _store_blob(self, payload: Any, artifacts_dir: Path) -> tuple[str, str | None]:
        raw_payload = json.dumps(_safe(payload), ensure_ascii=False)
        if len(raw_payload.encode("utf-8")) <= self.max_inline_bytes:
            return raw_payload, None
        artifact = artifacts_dir / f"{uuid.uuid4().hex}.json"
        artifact.write_text(raw_payload, encoding="utf-8")
        return (
            json.dumps(
                {"artifact_path": str(artifact), "bytes": len(raw_payload.encode("utf-8"))},
                ensure_ascii=False,
            ),
            str(artifact),
        )

    def _load_blob(self, inline_json: str, artifact_path: str | None) -> Any:
        if artifact_path:
            return json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        return json.loads(inline_json)

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
            conn.execute(
                """
                create table if not exists runs (
                    run_id text primary key,
                    session_id text not null,
                    created_at text not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_runs_session_created
                on runs(session_id, created_at desc)
                """
            )
            conn.execute(
                """
                create table if not exists run_events (
                    event_id integer primary key autoincrement,
                    run_id text not null,
                    event_type text not null,
                    created_at text not null,
                    payload_json text not null,
                    payload_artifact_path text,
                    raw_json text not null,
                    raw_artifact_path text
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_run_events_run_order
                on run_events(run_id, event_id)
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


def _preview(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:RUN_PREVIEW_LIMIT]


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
