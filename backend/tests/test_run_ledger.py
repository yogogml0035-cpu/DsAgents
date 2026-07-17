from __future__ import annotations

import re
import tempfile
from pathlib import Path

from api import INTERRUPTED_RUN_ERROR
from runtime.resources import (
    RUNTIME_AGENTS_BASELINE,
    RUNTIME_AGENTS_PATH,
    AgentResources,
    ResourceConfig,
)
from runtime.runs import SqliteRunLedger
from tests.test_support import messages_json, text_block, user_message


def run() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        _check_resources_and_ledger(tmp)
        _check_runtime_agents_handbook(tmp)
        _check_model_usage_aggregation(tmp)


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
        assert not resources.config.run_events_dir.exists()
        assert not (data_dir / "artifacts" / "run-events").exists()
        assert resources.backend is not None
        assert set(resources.backend.routes) == {
            "/memories/",
            "/artifacts/",
            "/large_tool_results/",
            "/skills/",
        }

        queued = resources.runs.create_run("run-1", "s1", messages_json(hello_messages))
        assert queued.status == "queued"
        assert queued.input_messages_json == messages_json(hello_messages)
        assert resources.runs.get_latest_content_event("run-1") is None
        resources.runs.emit_run_status("run-1", "running")
        resources.runs.emit_run_event(
            "run-1",
            "assistant_message",
            {"message_id": "msg-1", "text": "draft"},
            raw={"type": "updates", "data": {"messages": [{"id": "msg-1", "type": "ai", "content": "draft"}]}},
        )
        resources.runs.emit_run_status("run-1", "succeeded", reply="ok")
        snapshot = resources.runs.get_run("run-1")
        assert snapshot.status == "succeeded"
        assert snapshot.reply == "ok"
        # Fresh schema: China local time YYYY-MM-DD HH:MM:SS.
        assert _is_china_local_time(snapshot.created_at)
        assert _is_china_local_time(snapshot.updated_at)
        run_events = resources.runs.get_run_events("run-1")
        assert [event.event_type for event in run_events] == ["status", "status", "assistant_message", "status"]
        assert all(_is_china_local_time(event.created_at) for event in run_events)
        assert run_events[2].raw["type"] == "updates"
        latest_content = resources.runs.get_latest_content_event("run-1")
        assert latest_content is not None
        assert latest_content.event_id == run_events[2].event_id

        resources.runs.create_run(
            "workflow-run",
            "workflow-session",
            messages_json(hello_messages),
            workflow="philips_wgq_inbound_recognition",
        )
        resources.runs.emit_run_status("workflow-run", "running")
        result = {"outcome": "input_problems", "data": None, "problems": [{"issue": "mixed"}]}
        terminal = resources.runs.emit_run_status(
            "workflow-run",
            "succeeded",
            reply="请拆分批次",
            result=result,
        )
        workflow_snapshot = resources.runs.get_run("workflow-run")
        assert workflow_snapshot.workflow == "philips_wgq_inbound_recognition"
        assert workflow_snapshot.result == result
        assert terminal.payload["result"] == result

        resources.runs.create_run("run-2", "s2", messages_json(multi_messages))
        resources.runs.emit_run_status("run-2", "running")
        resources.runs.emit_run_event("run-2", "thinking", {"content": "plan"}, raw={"type": "messages"})
        resources.runs.emit_run_event(
            "run-2",
            "tool_execution",
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

    oversized_run_events_dir = data_dir / "internal" / "run-events"
    oversized = SqliteRunLedger(data_dir / "dsagents_runs.db", oversized_run_events_dir, max_inline_bytes=10)
    assert not oversized_run_events_dir.exists()
    oversized.create_run("big-run", "s-big", messages_json([user_message(text_block("blob"))]))
    oversized.emit_run_event(
        "big-run",
        "assistant_message",
        {"message_id": "big-msg", "text": "x" * 100},
        raw={"type": "updates", "data": {"messages": [{"id": "big-msg", "type": "ai", "content": "x" * 100}]}},
    )
    large_event = oversized.get_run_events("big-run")[-1]
    assert large_event.payload["text"] == "x" * 100
    assert large_event.raw["data"]["messages"][0]["content"] == "x" * 100
    latest_large_event = oversized.get_latest_content_event("big-run")
    assert latest_large_event is not None
    assert latest_large_event.payload["text"] == "x" * 100
    assert latest_large_event.raw["data"]["messages"][0]["content"] == "x" * 100
    assert any(oversized_run_events_dir.glob("*.json"))
    assert not (data_dir / "artifacts" / "run-events").exists()

    # Reopening the same DB is idempotent: no migration, fresh schema stable.
    reopened = SqliteRunLedger(data_dir / "dsagents_runs.db", data_dir / "internal" / "run-events")
    assert reopened.get_run("workflow-run").result["outcome"] == "input_problems"


def _check_runtime_agents_handbook(tmp: str) -> None:
    """Shared /memories/AGENTS.md is seeded once and never overwritten on reopen."""
    data_dir = Path(tmp) / "data" / "handbook"
    with AgentResources(ResourceConfig(data_dir=data_dir)) as resources:
        first = resources.backend.read(RUNTIME_AGENTS_PATH)
        assert first.error is None and first.file_data is not None
        content = first.file_data["content"]
        assert "result_path" in content
        assert "extract_archives" in content
        assert "不要" in content and "read_file" in content and "zip" in content
        assert content.strip() == RUNTIME_AGENTS_BASELINE.strip()
        resources.backend.edit(
            RUNTIME_AGENTS_PATH,
            "## 工具误用笔记（仅追加）",
            "## 工具误用笔记（仅追加）\n\n### read_file\n"
            "- 错误: 把二进制 zip 当文本读\n"
            "- 下一步: extract_archives 后再读文本\n",
        )

    with AgentResources(ResourceConfig(data_dir=data_dir)) as resources:
        second = resources.backend.read(RUNTIME_AGENTS_PATH)
        assert second.error is None and second.file_data is not None
        assert "### read_file" in second.file_data["content"]
        assert "把二进制 zip 当文本读" in second.file_data["content"]


def _check_model_usage_aggregation(tmp: str) -> None:
    data_dir = Path(tmp) / "data" / "usage"
    with AgentResources(ResourceConfig(data_dir=data_dir)) as resources:
        # A run with no model_usage events aggregates to None and never counts as
        # the latest content event.
        resources.runs.create_run("empty-run", "s-empty", messages_json([user_message(text_block("hi"))]))
        assert resources.runs.aggregate_model_usage("empty-run") is None
        resources.runs.emit_run_status("empty-run", "running")
        resources.runs.emit_run_event("empty-run", "text_delta", {"content": "hi"}, raw={"type": "messages"})
        assert resources.runs.aggregate_model_usage("empty-run") is None
        latest = resources.runs.get_latest_content_event("empty-run")
        assert latest is not None and latest.event_type == "text_delta"

        # A run with several model_usage events across two agents sums correctly.
        resources.runs.create_run("usage-run", "s-usage", messages_json([user_message(text_block("q"))]))
        resources.runs.emit_run_status("usage-run", "running")
        for payload in (
            {
                "model": "MiniMax-M3",
                "scope": "main_agent",
                "agent_name": "dsagents-main",
                "input_tokens": 1000,
                "output_tokens": 100,
                "cache_read_input_tokens": 400,
                "cache_creation_input_tokens": 100,
            },
            {
                "model": "MiniMax-M3",
                "scope": "subagent",
                "agent_name": "tecan-extractor-a",
                "input_tokens": 500,
                "output_tokens": 50,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 20,
            },
            {
                "model": "MiniMax-M3",
                "scope": "main_agent",
                "agent_name": "dsagents-main",
                "input_tokens": 2000,
                "output_tokens": 200,
                "cache_read_input_tokens": 800,
                "cache_creation_input_tokens": 0,
            },
        ):
            resources.runs.emit_run_event("usage-run", "model_usage", payload, raw={"type": "messages"})
        resources.runs.emit_run_status("usage-run", "succeeded", reply="done")

        agg = resources.runs.aggregate_model_usage("usage-run")
        assert agg is not None
        assert agg["model_calls"] == 3
        assert agg["input_tokens"] == 3500
        assert agg["output_tokens"] == 350
        assert agg["cache_read_input_tokens"] == 1200
        assert agg["cache_creation_input_tokens"] == 120
        assert agg["by_agent"][("main_agent", "dsagents-main")]["model_calls"] == 2
        assert agg["by_agent"][("main_agent", "dsagents-main")]["input_tokens"] == 3000
        assert agg["by_agent"][("subagent", "tecan-extractor-a")]["model_calls"] == 1
        # Per-call records kept for tier-aware pricing.
        assert [call["input_tokens"] for call in agg["calls"]] == [1000, 500, 2000]

        # model_usage never becomes the latest content event.
        latest_after = resources.runs.get_latest_content_event("usage-run")
        assert latest_after is None


_CHINA_LOCAL_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def _is_china_local_time(value: str) -> bool:
    return _CHINA_LOCAL_TIME.match(value) is not None


if __name__ == "__main__":
    run()
