from __future__ import annotations

import json
import tempfile
from pathlib import Path

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
        assert (api_data_dir / "artifacts" / "uploads" / first_upload_name).read_bytes() == b"img"

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
        assert event_types == ["status", "status", "values", "thinking", "text_delta", "tool_status", "text_delta", "values", "status"]
        latest_content_event = run_payload["latest_content_event"]
        assert latest_content_event is not None
        assert latest_content_event["type"] == "values"
        assert latest_content_event["payload"] == {"text": "echo[2]: again"}
        cursor = latest_content_event["event_id"]
        cursor_payload = client.get(f"/runs/{follow_up_payload['run_id']}", params={"after_event_id": cursor}).json()
        assert len(cursor_payload["events"]) < len(run_payload["events"])
        assert [event["type"] for event in cursor_payload["events"]] == ["status"]
        assert cursor_payload["events"][0]["event_id"] > cursor
        assert cursor_payload["latest_content_event"] == latest_content_event

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
        resumed = client.post("/runs", json={"messages": [user_message(text_block("recover"))], "session_id": "resume-session"})
        assert resumed.status_code == 200
        resumed_run = wait_for_run(client, resumed.json()["run_id"], "succeeded")
        assert resumed_run["reply"] == "echo[2]: recover"

        unknown = client.get("/runs/missing-run")
        assert unknown.status_code == 404
        assert unknown.json() == {"error": "Unknown run: missing-run"}


def _check_startup_recovery(tmp: str) -> None:
    cleanup_data_dir = Path(tmp) / "cleanup-api"
    cleanup_store = SqliteRunLedger(cleanup_data_dir / "dsagents_runs.db", cleanup_data_dir / "artifacts")
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


if __name__ == "__main__":
    run()
