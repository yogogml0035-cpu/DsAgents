from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_STATUSES = {"queued", "running", "succeeded", "failed"}


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
    input_message: str
    status: str
    created_at: str
    updated_at: str
    reply: str | None = None
    error: str | None = None

class SqliteRunLedger:
    def __init__(self, db_path: Path, artifacts_dir: Path, max_inline_bytes: int = 262_144) -> None:
        self.db_path = db_path
        self.run_artifacts_dir = artifacts_dir / "run-events"
        self.max_inline_bytes = max_inline_bytes
        self.run_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._setup()

    def create_run(self, run_id: str, session_id: str, input_message: str) -> RunSnapshot:
        created_at = _utcnow()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                insert into runs(
                    run_id,
                    session_id,
                    input_message,
                    status,
                    created_at,
                    updated_at,
                    reply,
                    error
                )
                values (?, ?, ?, ?, ?, ?, null, null)
                """,
                (run_id, session_id, input_message, "queued", created_at, created_at),
            )
            self._insert_event(
                conn,
                run_id,
                "status",
                created_at,
                {"status": "queued"},
                {"status": "queued"},
            )
            conn.commit()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunSnapshot:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                """
                select
                    run_id,
                    session_id,
                    input_message,
                    status,
                    created_at,
                    updated_at,
                    reply,
                    error
                from runs
                where run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")
        return RunSnapshot(
            run_id=row[0],
            session_id=row[1],
            input_message=row[2],
            status=row[3],
            created_at=row[4],
            updated_at=row[5],
            reply=row[6],
            error=row[7],
        )

    def get_run_events(self, run_id: str, after_event_id: int | None = None) -> list[RunEvent]:
        self.get_run(run_id)
        with closing(sqlite3.connect(self.db_path)) as conn:
            if after_event_id is None:
                rows = conn.execute(
                    """
                    select
                        event_id,
                        run_id,
                        type,
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
                        type,
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
        with closing(sqlite3.connect(self.db_path)) as conn:
            self._require_run(conn, run_id)
            event_id = self._insert_event(conn, run_id, event_type, created_at, safe_payload, safe_raw)
            conn.commit()
        return RunEvent(
            event_id=event_id,
            run_id=run_id,
            event_type=event_type,
            created_at=created_at,
            payload=safe_payload,
            raw=safe_raw,
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
        created_at = _utcnow()
        payload: dict[str, Any] = {"status": status}
        if reply is not None:
            payload["reply"] = reply
        if error is not None:
            payload["error"] = error
        safe_payload = _safe(payload)
        safe_raw = safe_payload if raw is None else _safe(raw)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self._require_run(conn, run_id)
            cursor = conn.execute(
                """
                update runs
                set status = ?, updated_at = ?, reply = ?, error = ?
                where run_id = ?
                """,
                (
                    status,
                    created_at,
                    reply if status == "succeeded" else None,
                    error if status == "failed" else None,
                    run_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown run: {run_id}")
            event_id = self._insert_event(conn, run_id, "status", created_at, safe_payload, safe_raw)
            conn.commit()
        return RunEvent(
            event_id=event_id,
            run_id=run_id,
            event_type="status",
            created_at=created_at,
            payload=safe_payload,
            raw=safe_raw,
        )

    def fail_incomplete_runs(self, error: str) -> list[str]:
        failed_run_ids: list[str] = []
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                select run_id
                from runs
                where status in ('queued', 'running')
                order by created_at
                """
            ).fetchall()
        for row in rows:
            run_id = row[0]
            self.emit_run_status(
                run_id,
                "failed",
                error=error,
                raw={"status": "failed", "error": error, "reason": "startup_interrupted"},
            )
            failed_run_ids.append(run_id)
        return failed_run_ids

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        event_type: str,
        created_at: str,
        payload: Any,
        raw: Any,
    ) -> int:
        payload_json, payload_artifact_path = self._store_blob(payload)
        raw_json, raw_artifact_path = self._store_blob(raw)
        cursor = conn.execute(
            """
            insert into run_events(
                run_id,
                type,
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
        return int(cursor.lastrowid)

    def _read_run_event(self, row: tuple[Any, ...]) -> RunEvent:
        return RunEvent(
            event_id=int(row[0]),
            run_id=row[1],
            event_type=row[2],
            created_at=row[3],
            payload=self._load_blob(row[4], row[5]),
            raw=self._load_blob(row[6], row[7]),
        )

    def _store_blob(self, payload: Any) -> tuple[str, str | None]:
        raw_payload = json.dumps(_safe(payload), ensure_ascii=False)
        if len(raw_payload.encode("utf-8")) <= self.max_inline_bytes:
            return raw_payload, None
        artifact = self.run_artifacts_dir / f"{uuid.uuid4().hex}.json"
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

    def _require_run(self, conn: sqlite3.Connection, run_id: str) -> None:
        row = conn.execute("select 1 from runs where run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")

    def _setup(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                create table if not exists runs (
                    run_id text primary key,
                    session_id text not null,
                    input_message text not null,
                    status text not null,
                    created_at text not null,
                    updated_at text not null,
                    reply text,
                    error text
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
                    type text not null,
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
