from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api import INTERRUPTED_RUN_ERROR, create_app
from harness import ARTIFACT_REFERENCE_HINT, HarnessRuntime
from hands import ToolStatusHands
from resources import AgentResources, ResourceConfig
from run_ledger import SqliteRunLedger
from tests.test_support import (
    FakeBrainFactory,
    StreamControl,
    artifact_block,
    text_block,
    user_message,
    wait_for_run,
)
from tools import ToolCatalog


def run() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        _check_api(tmp)
        _check_startup_recovery(tmp)
        _check_usage_pricing(tmp)


def _check_api(tmp: str) -> None:
    api_data_dir = Path(tmp) / "api-data"
    hold_control = StreamControl()
    harness_creations = {"count": 0}
    factory = FakeBrainFactory(control=hold_control)

    def fake_harness(resources: AgentResources) -> HarnessRuntime:
        harness_creations["count"] += 1
        return HarnessRuntime(
            resources=resources,
            hands=ToolStatusHands(),
            tools=ToolCatalog(()),
            brain_factory=factory,
        )

    app = create_app(
        resource_config=ResourceConfig(data_dir=api_data_dir),
        harness_factory=fake_harness,
    )
    with TestClient(app) as client:
        old_request = client.post("/runs", json={"message": "hello", "session_id": None})
        assert old_request.status_code == 422

        with patch("api.time.strftime", return_value="20260708010203"):
            single_upload = client.post(
                "/upload",
                files=[("files", ("../photo.png", b"img", "image/png"))],
            )
        assert single_upload.status_code == 200
        single_upload_payload = single_upload.json()["files"]
        assert len(single_upload_payload) == 1
        first_file = single_upload_payload[0]
        assert first_file["name"] == "photo.png"
        assert first_file["mime_type"] == "image/png"
        assert first_file["size"] == 3
        assert first_file["file_path"].startswith("/artifacts/uploads/")
        first_upload_name = Path(first_file["file_path"]).name
        assert first_upload_name == "photo_20260708010203.png"
        assert (api_data_dir / "artifacts" / "uploads" / first_upload_name).read_bytes() == b"img"

        with patch("api.time.strftime", return_value="20260708010204"):
            normalized_upload = client.post(
                "/upload",
                files=[("files", ("Shipping\u00a0documents\u00a0T_CHINA\u00a015.06.2026.pdf", b"pdf", "application/pdf"))],
            )
        assert normalized_upload.status_code == 200
        normalized_file = normalized_upload.json()["files"][0]
        assert normalized_file["name"] == "Shipping documents T_CHINA 15.06.2026.pdf"
        normalized_upload_name = Path(normalized_file["file_path"]).name
        assert normalized_upload_name == "Shipping documents T_CHINA 15.06.2026_20260708010204.pdf"
        assert (api_data_dir / "artifacts" / "uploads" / normalized_upload_name).read_bytes() == b"pdf"

        with patch("api.time.strftime", return_value="20260708010205"):
            multi_upload = client.post(
                "/upload",
                files=[
                    ("files", ("first.png", b"one", "image/png")),
                    ("files", ("notes.txt", b"hello", "text/plain")),
                ],
            )
        assert multi_upload.status_code == 200
        multi_upload_payload = multi_upload.json()["files"]
        assert len(multi_upload_payload) == 2
        assert [item["name"] for item in multi_upload_payload] == ["first.png", "notes.txt"]
        assert [item["mime_type"] for item in multi_upload_payload] == ["image/png", "text/plain"]
        assert [item["size"] for item in multi_upload_payload] == [3, 5]
        assert [Path(item["file_path"]).name for item in multi_upload_payload] == [
            "first_20260708010205.png",
            "notes_20260708010205.txt",
        ]

        with patch("api.time.strftime", return_value="20260708010206"):
            duplicate_upload = client.post(
                "/upload",
                files=[
                    ("files", ("report.pdf", b"a", "application/pdf")),
                    ("files", ("report.pdf", b"b", "application/pdf")),
                ],
            )
        assert duplicate_upload.status_code == 200
        duplicate_upload_names = [
            Path(item["file_path"]).name for item in duplicate_upload.json()["files"]
        ]
        assert duplicate_upload_names == [
            "report_20260708010206.pdf",
            "report_20260708010206_2.pdf",
        ]

        with patch("api.time.strftime", return_value="20260708010206"):
            conflict_upload = client.post(
                "/upload",
                files=[("files", ("report.pdf", b"c", "application/pdf"))],
            )
        assert conflict_upload.status_code == 200
        assert Path(conflict_upload.json()["files"][0]["file_path"]).name == "report_20260708010206_3.pdf"

        with patch("api.time.strftime", return_value="20260708010207"):
            mixed_upload = client.post(
                "/upload",
                files=[
                    ("files", ("report.pdf", b"pdf", "application/pdf")),
                    ("files", ("diagram.jpg", b"jpeg", "image/jpeg")),
                ],
            )
        assert mixed_upload.status_code == 200
        mixed_upload_payload = mixed_upload.json()["files"]
        assert len(mixed_upload_payload) == 2
        mixed_artifact_path = mixed_upload_payload[1]["file_path"]

        missing_upload = client.post("/files")
        assert missing_upload.status_code == 404

        first_messages = [user_message(text_block("hello"))]
        first = client.post("/runs", json={"messages": first_messages, "session_id": None})
        assert first.status_code == 200
        first_payload = first.json()
        session_id = first_payload["session_id"]
        run_id = first_payload["run_id"]
        assert first_payload == {
            "run_id": run_id,
            "session_id": session_id,
            "status": "queued",
        }
        first_run = wait_for_run(client, run_id, "succeeded")
        assert first_run["reply"] == "echo[1]: hello"
        assert json.loads(first_run["input_messages_json"]) == first_messages
        assert harness_creations["count"] == 1

        follow_up_messages = [user_message(text_block("again"))]
        follow_up = client.post("/runs", json={"messages": follow_up_messages, "session_id": session_id})
        assert follow_up.status_code == 200
        follow_up_payload = follow_up.json()
        follow_up_run = wait_for_run(client, follow_up_payload["run_id"], "succeeded")
        assert follow_up_run["reply"] == "echo[2]: again"

        run_detail = client.get(f"/runs/{follow_up_payload['run_id']}")
        assert run_detail.status_code == 200
        run_payload = run_detail.json()
        event_types = [event["type"] for event in run_payload["events"]]
        assert event_types == [
            "status",
            "status",
            "thinking",
            "model_usage",
            "text_delta",
            "tool_call",
            "tool_status",
            "tool_result",
            "model_usage",
            "text_delta",
            "assistant_message",
            "status",
        ]
        tool_call_event = [event for event in run_payload["events"] if event["type"] == "tool_call"][0]
        assert tool_call_event["payload"] == {
            "message_id": "assistant-tool-" + session_id + "-2",
            "tool_call_id": "call-" + session_id + "-2",
            "name": "read_file",
            "args": {"file_path": "/artifacts/uploads/demo.jpg"},
        }
        tool_result_event = [event for event in run_payload["events"] if event["type"] == "tool_result"][0]
        assert tool_result_event["payload"] == {
            "message_id": "tool-result-" + session_id + "-2",
            "tool_call_id": "call-" + session_id + "-2",
            "name": "read_file",
            "status": "success",
            "content_type": "image",
            "mime_type": "image/jpeg",
            "text": None,
            "preview": None,
        }
        assert "base64" not in json.dumps(tool_result_event["payload"], ensure_ascii=False)
        latest_content_event = run_payload["latest_content_event"]
        assert latest_content_event is not None
        assert latest_content_event["type"] == "assistant_message"
        assert latest_content_event["payload"] == {
            "message_id": "assistant-final-" + session_id + "-2",
            "thinking": "plan: ",
            "text": "echo[2]: again",
        }
        cursor = latest_content_event["event_id"]
        cursor_payload = client.get(f"/runs/{follow_up_payload['run_id']}", params={"after_event_id": cursor}).json()
        assert len(cursor_payload["events"]) < len(run_payload["events"])
        assert [event["type"] for event in cursor_payload["events"]] == ["status"]
        assert cursor_payload["events"][0]["event_id"] > cursor
        assert cursor_payload["latest_content_event"] == latest_content_event
        # usage is always aggregated from the whole run, regardless of after_event_id.
        assert cursor_payload["usage"] == run_payload["usage"]

        # Top-level usage block: raw token facts + cache hit rate + tier-priced
        # CNY estimates. main_agent input(1000) is standard tier (<=512k).
        usage = run_payload["usage"]
        assert usage["model_calls"] == 2
        assert usage["input_tokens"] == 1200
        assert usage["output_tokens"] == 340
        assert usage["cache_read_input_tokens"] == 650
        assert usage["cache_creation_input_tokens"] == 290
        assert usage["cache_hit_rate"] == 650 / 1200
        assert usage["pricing_as_of"] == "2026-07-12"
        # Standard tier: input 2.10, output 8.40, cache_read 0.42 per million.
        # cost = non-cache-read input (550) *2.10 + cache_read 650 *0.42 + out 340 *8.40, all /1e6
        expected_cost = round((550 * 2.10 + 650 * 0.42 + 340 * 8.40) / 1_000_000, 6)
        assert usage["estimated_cost_cny"] == expected_cost
        # savings = cache_read 650 * (input 2.10 - cache_read 0.42) / 1e6
        expected_savings = round(650 * (2.10 - 0.42) / 1_000_000, 6)
        assert usage["estimated_savings_cny"] == expected_savings
        agent_scopes = {agent["scope"] for agent in usage["by_agent"]}
        assert agent_scopes == {"main_agent", "subagent"}
        main_agent = [a for a in usage["by_agent"] if a["scope"] == "main_agent"][0]
        assert main_agent["model_calls"] == 1
        assert main_agent["cache_hit_rate"] == 600 / 1000

        multimodal_messages = [
            user_message(
                text_block("这张图里有什么？"),
                artifact_block(mixed_artifact_path),
            )
        ]
        multimodal_run = client.post("/runs", json={"messages": multimodal_messages, "session_id": "image-session"})
        assert multimodal_run.status_code == 200
        multimodal_run_payload = multimodal_run.json()
        multimodal_snapshot = wait_for_run(client, multimodal_run_payload["run_id"], "succeeded")
        assert multimodal_snapshot["reply"].startswith("echo[1]: 这张图里有什么？")
        multimodal_detail = client.get(f"/runs/{multimodal_run_payload['run_id']}").json()
        assert multimodal_detail["latest_content_event"]["type"] == "assistant_message"
        assert multimodal_detail["latest_content_event"]["payload"]["text"] == multimodal_snapshot["reply"]
        normalized_messages = factory.received_payloads[2]
        assert normalized_messages[0]["content"][1] == {
            "type": "text",
            "text": ARTIFACT_REFERENCE_HINT.format(path=mixed_artifact_path),
        }

        background_messages = [user_message(text_block("hold"))]
        background = client.post("/runs", json={"messages": background_messages, "session_id": session_id})
        assert background.status_code == 200
        background_payload = background.json()
        assert hold_control.started.wait(timeout=5)
        running_payload = client.get(f"/runs/{background_payload['run_id']}").json()["run"]
        assert running_payload["status"] == "running"
        conflict = client.post("/runs", json={"messages": [user_message(text_block("blocked"))], "session_id": session_id})
        assert conflict.status_code == 409
        assert conflict.json() == {
            "error": "该会话正在运行",
            "active_run_id": background_payload["run_id"],
        }

        hold_control.release.set()
        background_run = wait_for_run(client, background_payload["run_id"], "succeeded")
        assert background_run["reply"] == "echo[3]: hold"

        failed = client.post("/runs", json={"messages": [user_message(text_block("fail"))], "session_id": "resume-session"})
        assert failed.status_code == 200
        failed_payload = failed.json()
        failed_run = wait_for_run(client, failed_payload["run_id"], "failed")
        assert failed_run["error"] == "planned failure"
        # Failed run still returns usage for model calls recorded before the exception.
        failed_detail = client.get(f"/runs/{failed_payload['run_id']}").json()
        assert failed_detail["usage"] is not None
        assert failed_detail["usage"]["model_calls"] == 1
        resumed = client.post("/runs", json={"messages": [user_message(text_block("recover"))], "session_id": "resume-session"})
        assert resumed.status_code == 200
        resumed_run = wait_for_run(client, resumed.json()["run_id"], "succeeded")
        assert resumed_run["reply"] == "echo[2]: recover"

        unknown = client.get("/runs/missing-run")
        assert unknown.status_code == 404
        assert unknown.json() == {"error": "Unknown run: missing-run"}


def _check_startup_recovery(tmp: str) -> None:
    cleanup_data_dir = Path(tmp) / "cleanup-api"
    cleanup_store = SqliteRunLedger(
        cleanup_data_dir / "dsagents_runs.db",
        cleanup_data_dir / "internal" / "run-events",
    )
    cleanup_store.create_run("cleanup-queued", "cleanup-session", json.dumps([user_message(text_block("queued"))], ensure_ascii=False))
    cleanup_store.create_run("cleanup-running", "cleanup-session", json.dumps([user_message(text_block("running"))], ensure_ascii=False))
    cleanup_store.emit_run_status("cleanup-running", "running")
    cleanup_factory_count = {"count": 0}

    def cleanup_harness(resources: AgentResources) -> HarnessRuntime:
        cleanup_factory_count["count"] += 1
        return HarnessRuntime(
            resources=resources,
            hands=ToolStatusHands(),
            tools=ToolCatalog(()),
            brain_factory=FakeBrainFactory(),
        )

    cleanup_app = create_app(
        resource_config=ResourceConfig(data_dir=cleanup_data_dir),
        harness_factory=cleanup_harness,
    )
    with TestClient(cleanup_app) as cleanup_client:
        queued_run = cleanup_client.get("/runs/cleanup-queued").json()
        running_run = cleanup_client.get("/runs/cleanup-running").json()
        assert queued_run["run"]["status"] == "failed"
        assert queued_run["run"]["error"] == INTERRUPTED_RUN_ERROR
        assert queued_run["latest_content_event"] is None
        assert running_run["run"]["status"] == "failed"
        assert running_run["run"]["error"] == INTERRUPTED_RUN_ERROR
        assert running_run["latest_content_event"] is None
        assert cleanup_factory_count["count"] == 1


def _check_usage_pricing(tmp: str) -> None:
    """Tier switching (long-context threshold), unpriceable model => null amounts,
    and zero-input => null cache hit rate, exercised directly via the ledger."""
    pricing_data_dir = Path(tmp) / "pricing-api"
    pricing_store = SqliteRunLedger(
        pricing_data_dir / "dsagents_runs.db",
        pricing_data_dir / "internal" / "run-events",
    )

    pricing_store.create_run("tier-run", "s-tier", json.dumps([user_message(text_block("t"))], ensure_ascii=False))
    pricing_store.emit_run_status("tier-run", "running")
    # One standard-tier call (input 1000) + one long-context call (input 600000).
    pricing_store.emit_run_event(
        "tier-run",
        "model_usage",
        {
            "model": "MiniMax-M3",
            "scope": "main_agent",
            "agent_name": "dsagents-main",
            "input_tokens": 1000,
            "output_tokens": 100,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        raw={"type": "messages"},
    )
    pricing_store.emit_run_event(
        "tier-run",
        "model_usage",
        {
            "model": "MiniMax-M3",
            "scope": "main_agent",
            "agent_name": "dsagents-main",
            "input_tokens": 600_000,
            "output_tokens": 1000,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        raw={"type": "messages"},
    )
    pricing_store.emit_run_status("tier-run", "succeeded", reply="t")

    pricing_store.create_run("unpriceable-run", "s-unp", json.dumps([user_message(text_block("u"))], ensure_ascii=False))
    pricing_store.emit_run_status("unpriceable-run", "running")
    pricing_store.emit_run_event(
        "unpriceable-run",
        "model_usage",
        {
            "model": "SomeOtherModel",
            "scope": "main_agent",
            "agent_name": "dsagents-main",
            "input_tokens": 500,
            "output_tokens": 50,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 0,
        },
        raw={"type": "messages"},
    )
    pricing_store.emit_run_status("unpriceable-run", "succeeded", reply="u")

    pricing_store.create_run("no-input-run", "s-noin", json.dumps([user_message(text_block("n"))], ensure_ascii=False))
    pricing_store.emit_run_status("no-input-run", "running")
    pricing_store.emit_run_event(
        "no-input-run",
        "model_usage",
        {
            "model": "MiniMax-M3",
            "scope": "main_agent",
            "agent_name": "dsagents-main",
            "input_tokens": 0,
            "output_tokens": 50,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        raw={"type": "messages"},
    )
    pricing_store.emit_run_status("no-input-run", "succeeded", reply="n")

    factory_count = {"count": 0}

    def noop_harness(resources: AgentResources) -> HarnessRuntime:
        factory_count["count"] += 1
        return HarnessRuntime(
            resources=resources,
            hands=ToolStatusHands(),
            tools=ToolCatalog(()),
            brain_factory=FakeBrainFactory(),
        )

    pricing_app = create_app(
        resource_config=ResourceConfig(data_dir=pricing_data_dir),
        harness_factory=noop_harness,
    )
    with TestClient(pricing_app) as pricing_client:
        tier_usage = pricing_client.get("/runs/tier-run").json()["usage"]
        # Mixed tiers: 1000@standard + 600000@long_context.
        # standard: (1000*2.10 + 0*0.42 + 100*8.40)/1e6
        # long_context (4.20, 16.80, 0.84): (600000*4.20 + 0*0.84 + 1000*16.80)/1e6
        expected_cost = round(
            (1000 * 2.10 + 100 * 8.40) / 1_000_000
            + (600_000 * 4.20 + 1000 * 16.80) / 1_000_000,
            6,
        )
        assert tier_usage["estimated_cost_cny"] == expected_cost

        unpriceable_usage = pricing_client.get("/runs/unpriceable-run").json()["usage"]
        # Unknown model => amounts null, token facts still complete.
        assert unpriceable_usage["estimated_cost_cny"] is None
        assert unpriceable_usage["estimated_savings_cny"] is None
        assert unpriceable_usage["input_tokens"] == 500
        assert unpriceable_usage["cache_read_input_tokens"] == 100

        no_input_usage = pricing_client.get("/runs/no-input-run").json()["usage"]
        # Zero input => null cache hit rate (no division by zero).
        assert no_input_usage["cache_hit_rate"] is None
        assert no_input_usage["input_tokens"] == 0


if __name__ == "__main__":
    run()
