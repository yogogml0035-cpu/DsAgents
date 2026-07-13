from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# run 是唯一执行/查询单位。queued → running → succeeded|failed|cancelled；
# 取消路径 queued → cancelled、running → cancelling → cancelled。
RUN_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled", "cancelling"}


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
    input_messages_json: str
    status: str
    created_at: str
    updated_at: str
    reply: str | None = None
    error: str | None = None


class SqliteRunLedger:
    def __init__(self, db_path: Path, run_events_dir: Path, max_inline_bytes: int = 262_144) -> None:
        self.db_path = db_path
        self.run_events_dir = run_events_dir
        self.max_inline_bytes = max_inline_bytes
        self._setup()

    def create_run(self, run_id: str, session_id: str, input_messages_json: str) -> RunSnapshot:
        created_at = _now_text()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                insert into runs(
                    run_id,
                    session_id,
                    input_messages_json,
                    status,
                    created_at,
                    updated_at,
                    reply,
                    error
                )
                values (?, ?, ?, ?, ?, ?, null, null)
                """,
                (run_id, session_id, input_messages_json, "queued", created_at, created_at),
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
                    input_messages_json,
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
            input_messages_json=row[2],
            status=row[3],
            created_at=row[4],
            updated_at=row[5],
            reply=row[6],
            error=row[7],
        )

    def get_run_events(self, run_id: str, after_event_id: int | None = None) -> list[RunEvent]:
        self.get_run(run_id)
        params: tuple[Any, ...] = (run_id,)
        where = "where run_id = ?"
        if after_event_id is not None:
            where = "where run_id = ? and event_id > ?"
            params = (run_id, after_event_id)
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                f"""
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
                {where}
                order by event_id
                """,
                params,
            ).fetchall()
        return [self._read_run_event(row) for row in rows]

    def get_latest_content_event(self, run_id: str) -> RunEvent | None:
        self.get_run(run_id)
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
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
                where run_id = ? and type not in ('status', 'model_usage')
                order by event_id desc
                limit 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return self._read_run_event(row)

    def aggregate_model_usage(self, run_id: str) -> dict[str, Any] | None:
        """Sum all model_usage events for a run into token totals + by-agent.

        Returns None when the run has no model_usage events. Token figures are
        the raw facts; cost/price estimation is layered on by the API caller.
        Per-call records (model + per-call token counts) are kept for tier-aware
        pricing, which cannot be reconstructed from the sums alone.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                """
                select payload_json, payload_artifact_path
                from run_events
                where run_id = ? and type = 'model_usage'
                order by event_id
                """,
                (run_id,),
            ).fetchall()
        if not rows:
            return None
        totals = _new_usage_bucket()
        by_agent: dict[tuple[str, str], dict[str, int]] = {}
        calls: list[dict[str, Any]] = []
        for payload_json, artifact_path in rows:
            payload = self._load_blob(payload_json, artifact_path)
            if not isinstance(payload, dict):
                continue
            _add_usage(totals, payload)
            agent_key = (str(payload.get("scope", "main_agent")), str(payload.get("agent_name", "")))
            _add_usage(by_agent.setdefault(agent_key, _new_usage_bucket()), payload)
            calls.append({
                "model": payload.get("model", ""),
                "input_tokens": _usage_int(payload.get("input_tokens")),
                "output_tokens": _usage_int(payload.get("output_tokens")),
                "cache_read_input_tokens": _usage_int(payload.get("cache_read_input_tokens")),
                "cache_creation_input_tokens": _usage_int(payload.get("cache_creation_input_tokens")),
            })
        totals["by_agent"] = by_agent
        totals["calls"] = calls
        return totals

    def emit_run_event(
        self,
        run_id: str,
        event_type: str,
        payload: Any,
        *,
        raw: Any | None = None,
    ) -> RunEvent:
        created_at = _now_text()
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
        created_at = _now_text()
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
                    error if status in {"failed", "cancelled"} else None,
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
                where status in ('queued', 'running', 'cancelling')
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
        self.run_events_dir.mkdir(parents=True, exist_ok=True)
        artifact = self.run_events_dir / f"{uuid.uuid4().hex}.json"
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
                    input_messages_json text not null,
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


_USAGE_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _new_usage_bucket() -> dict[str, int]:
    return {"model_calls": 0, **{key: 0 for key in _USAGE_TOKEN_KEYS}}


def _add_usage(bucket: dict[str, int], payload: dict[str, Any]) -> None:
    bucket["model_calls"] += 1
    for key in _USAGE_TOKEN_KEYS:
        bucket[key] += _usage_int(payload.get(key))


def _usage_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _now_text() -> str:
    # UTC ISO-8601 毫秒时间（fresh schema，无迁移、无本地时区转换）。
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
