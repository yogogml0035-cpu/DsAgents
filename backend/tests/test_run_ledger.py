from __future__ import annotations

import tempfile
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
        run_events = resources.runs.get_run_events("run-1")
        assert [event.event_type for event in run_events] == ["status", "status", "values", "status"]
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


if __name__ == "__main__":
    run()
