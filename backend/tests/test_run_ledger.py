from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from api import INTERRUPTED_RUN_ERROR
from resources import AgentResources, ResourceConfig
from run_ledger import SqliteRunLedger
from tests.test_support import messages_json, text_block, user_message


def run() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        _check_resources_and_ledger(tmp)


def _check_resources_and_ledger(tmp: str) -> None:
    data_dir = Path(tmp) / "data"
    hello_messages = [user_message(text_block("hello"))]
    multi_messages = [user_message(text_block("multi"))]
    hold_messages = [user_message(text_block("hold"))]
    later_messages = [user_message(text_block("later"))]

    with AgentResources(ResourceConfig(data_dir=data_dir)) as resources:
        assert resources.config.run_db.exists()
        assert resources.config.store_db.exists()
        assert resources.config.checkpoint_db.exists()
        assert resources.backend is not None
        assert set(resources.backend.routes) == {"/memories/", "/artifacts/", "/large_tool_results/"}

        queued = resources.runs.create_run("run-1", "s1", messages_json(hello_messages))
        assert queued.status == "queued"
        assert queued.input_messages_json == messages_json(hello_messages)
        assert resources.runs.get_latest_content_event("run-1") is None
        resources.runs.emit_run_status("run-1", "running")
        resources.runs.emit_run_event("run-1", "values", {"text": "draft"}, raw={"type": "values", "data": {"text": "draft"}})
        resources.runs.emit_run_status("run-1", "succeeded", reply="ok")
        snapshot = resources.runs.get_run("run-1")
        assert snapshot.status == "succeeded"
        assert snapshot.reply == "ok"
        _assert_second_precision_timestamp(snapshot.created_at)
        _assert_second_precision_timestamp(snapshot.updated_at)
        run_events = resources.runs.get_run_events("run-1")
        assert [event.event_type for event in run_events] == ["status", "status", "values", "status"]
        assert all(_is_second_precision_timestamp(event.created_at) for event in run_events)
        assert run_events[2].raw["type"] == "values"
        latest_content = resources.runs.get_latest_content_event("run-1")
        assert latest_content is not None
        assert latest_content.event_id == run_events[2].event_id

        resources.runs.create_run("run-2", "s2", messages_json(multi_messages))
        resources.runs.emit_run_status("run-2", "running")
        resources.runs.emit_run_event("run-2", "thinking", {"content": "plan"}, raw={"type": "messages"})
        resources.runs.emit_run_event(
            "run-2",
            "tool_status",
            {"name": "demo", "status": "started"},
            raw={"type": "custom", "data": {"name": "demo", "status": "started"}},
        )
        latest_event = resources.runs.emit_run_event(
            "run-2",
            "text_delta",
            {"content": "done"},
            raw={"type": "messages", "data": {"content": "done"}},
        )
        resources.runs.emit_run_status("run-2", "succeeded", reply="done")
        latest_content = resources.runs.get_latest_content_event("run-2")
        assert latest_content is not None
        assert latest_content.event_id == latest_event.event_id

        resources.runs.create_run("recover-run", "recover", messages_json(hold_messages))
        resources.runs.emit_run_status("recover-run", "running")
        resources.runs.create_run("queued-run", "recover", messages_json(later_messages))
        failed_runs = resources.runs.fail_incomplete_runs(INTERRUPTED_RUN_ERROR)
        assert failed_runs == ["recover-run", "queued-run"]
        assert resources.runs.get_run("recover-run").status == "failed"
        assert resources.runs.get_run("queued-run").status == "failed"

    oversized = SqliteRunLedger(data_dir / "dsagents_runs.db", data_dir / "artifacts", max_inline_bytes=10)
    oversized.create_run("big-run", "s-big", messages_json([user_message(text_block("blob"))]))
    oversized.emit_run_event(
        "big-run",
        "values",
        {"content": "x" * 100},
        raw={"type": "values", "data": {"content": "x" * 100}},
    )
    large_event = oversized.get_run_events("big-run")[-1]
    assert large_event.payload["content"] == "x" * 100
    assert large_event.raw["data"]["content"] == "x" * 100
    latest_large_event = oversized.get_latest_content_event("big-run")
    assert latest_large_event is not None
    assert latest_large_event.payload["content"] == "x" * 100
    assert latest_large_event.raw["data"]["content"] == "x" * 100
    assert any((data_dir / "artifacts" / "run-events").glob("*.json"))

    legacy_db = data_dir / "legacy.db"
    legacy = SqliteRunLedger(legacy_db, data_dir / "artifacts")
    legacy.create_run("legacy-run", "legacy-session", messages_json([user_message(text_block("legacy"))]))
    with sqlite3.connect(legacy_db) as conn:
        conn.execute(
            """
            update runs
            set created_at = ?, updated_at = ?
            where run_id = ?
            """,
            ("2026-07-07T08:18:59.740303+00:00", "2026-07-07 09:42:01", "legacy-run"),
        )
        conn.execute(
            """
            update run_events
            set created_at = ?
            where run_id = ?
            """,
            ("2026-07-07 09:40:16", "legacy-run"),
        )
        conn.execute("pragma user_version = 0")
        conn.commit()
    normalized = SqliteRunLedger(legacy_db, data_dir / "artifacts")
    normalized_run = normalized.get_run("legacy-run")
    assert normalized_run.created_at == _local_expected_from_utc_iso("2026-07-07T08:18:59.740303+00:00")
    assert normalized_run.updated_at == _local_expected_from_utc_naive("2026-07-07 09:42:01")
    assert normalized.get_run_events("legacy-run")[0].created_at == _local_expected_from_utc_naive("2026-07-07 09:40:16")
    normalized_again = SqliteRunLedger(legacy_db, data_dir / "artifacts")
    assert normalized_again.get_run("legacy-run").updated_at == normalized_run.updated_at
    assert normalized_again.get_run_events("legacy-run")[0].created_at == normalized.get_run_events("legacy-run")[0].created_at


def _assert_second_precision_timestamp(value: str) -> None:
    assert _is_second_precision_timestamp(value)


def _is_second_precision_timestamp(value: str) -> bool:
    if "T" in value or "." in value or len(value) != 19:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return True


def _local_expected_from_utc_iso(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _local_expected_from_utc_naive(value: str) -> str:
    return (
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
    )


if __name__ == "__main__":
    run()
