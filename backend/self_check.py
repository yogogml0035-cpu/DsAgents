from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dotenv import load_dotenv

warnings.filterwarnings(
    "ignore",
    message=r"Using `httpx` with `starlette\.testclient` is deprecated; install `httpx2` instead\.",
)
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

from api import INTERRUPTED_RUN_ERROR, create_app
from hands import ToolStatusHands, ToolStatusMiddleware
from harness import DeepAgentsBrainFactory, HarnessRuntime, _thinking_delta
from resources import AgentResources, ResourceConfig
from run_ledger import SqliteRunLedger
from tools import ToolCatalog, _extract_markdown, _find_value, default_tool_catalog, parse_document


class _StreamControl:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()


class _FakeBrain:
    def __init__(self, threads: dict[str, list[str]], control: _StreamControl | None = None) -> None:
        self.threads = threads
        self.control = control

    def stream(self, payload: dict, config: dict | None = None, **kwargs: object):
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "user"
        assert getattr(payload["messages"][0], "id", None) is None
        assert kwargs["stream_mode"] == ["messages", "custom", "values"]
        assert kwargs["version"] == "v2"
        assert config is not None
        thread_id = config["configurable"]["thread_id"]
        text = payload["messages"][0]["content"]
        history = self.threads.setdefault(thread_id, [])
        history.append(text)
        yield {
            "type": "values",
            "ns": (),
            "data": {"messages": [{"role": "user", "content": text}]},
            "interrupts": (),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                AIMessageChunk(
                    content=[{"type": "thinking", "thinking": "plan: ", "index": 0}],
                    response_metadata={"model_provider": "anthropic"},
                ),
                {"langgraph_node": "model"},
            ),
        }
        if text == "fail":
            raise RuntimeError("planned failure")
        if text == "hold":
            assert self.control is not None
            self.control.started.set()
            assert self.control.release.wait(timeout=5), "hold run was never released"
        reply = f"echo[{len(history)}]: {text}"
        yield {"type": "messages", "ns": (), "data": (AIMessageChunk(content="echo["), {"langgraph_node": "model"})}
        yield {"type": "custom", "ns": (), "data": {"name": "parse_document", "status": "started"}}
        yield {
            "type": "messages",
            "ns": (),
            "data": (AIMessageChunk(content=f"{len(history)}]: {text}"), {"langgraph_node": "model"}),
        }
        yield {
            "type": "values",
            "ns": (),
            "data": {
                "messages": [
                    AIMessage(
                        content=[
                            {"type": "thinking", "thinking": "plan: "},
                            {"type": "text", "text": reply},
                        ],
                        response_metadata={"model_provider": "anthropic"},
                    )
                ]
            },
            "interrupts": (),
        }


class _FakeBrainFactory:
    def __init__(self, control: _StreamControl | None = None) -> None:
        self.control = control
        self.threads: dict[str, list[str]] = {}

    def create(self, **_: object) -> _FakeBrain:
        return _FakeBrain(self.threads, self.control)


def main() -> None:
    assert _thinking_delta((AIMessageChunk(content=[{"type": "thinking", "thinking": "plan"}]), {})) == "plan"
    assert _find_value({"data": {"task_id": "abc"}}, {"task_id"}) == "abc"
    assert _extract_markdown({"result": {"md_content": "# ok"}}) == "# ok"
    assert default_tool_catalog().handlers[0].__name__ == "parse_document"

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        _check_model_env_loading(tmp)
        _check_parse_document_env_guard(tmp)
        _check_resources_and_ledger(tmp)
        _check_tool_status_middleware()
        _check_harness(tmp)
        _check_api(tmp)
        _check_startup_recovery(tmp)
        _check_virtual_artifacts(tmp)

    print("self-check passed")


def _check_model_env_loading(tmp: str) -> None:
    with patch.dict(os.environ, {}, clear=True):
        env_path = Path(tmp) / ".env"
        env_path.write_text(
            "MINIMAX_API_KEY=test-key\n"
            "MINIMAX_BASE_URL=https://minimax.example/anthropic\n"
            "MINIMAX_MODEL=test-minimax\n",
            encoding="utf-8",
        )
        load_dotenv(env_path, override=True)
        factory = DeepAgentsBrainFactory()
        assert factory.model.__class__.__name__ == "ChatAnthropic"
        assert getattr(factory.model, "model", None) == "test-minimax"
        assert factory.model.thinking == {"type": "adaptive"}
        assert factory.model.anthropic_api_key.get_secret_value() == "test-key"
        assert factory.model.anthropic_api_url == "https://minimax.example/anthropic"


def _check_parse_document_env_guard(tmp: str) -> None:
    with patch.dict(os.environ, {}, clear=True):
        source = Path(tmp) / "sample.pdf"
        source.write_text("demo", encoding="utf-8")
        try:
            parse_document(str(source))
        except RuntimeError as exc:
            assert str(exc) == "Missing required environment variable: MINERU_BASE_URL"
        else:
            raise AssertionError("parse_document must fail fast when MINERU_BASE_URL is missing")


def _check_resources_and_ledger(tmp: str) -> None:
    data_dir = Path(tmp) / "data"
    with AgentResources(ResourceConfig(data_dir=data_dir)) as resources:
        assert resources.config.run_db.exists()
        assert resources.config.store_db.exists()
        assert resources.config.checkpoint_db.exists()
        assert resources.backend is not None

        queued = resources.runs.create_run("run-1", "s1", "hello")
        assert queued.status == "queued"
        assert queued.input_message == "hello"
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

        resources.runs.create_run("run-2", "s2", "multi")
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

        resources.runs.create_run("recover-run", "recover", "hold")
        resources.runs.emit_run_status("recover-run", "running")
        resources.runs.create_run("queued-run", "recover", "later")
        failed_runs = resources.runs.fail_incomplete_runs(INTERRUPTED_RUN_ERROR)
        assert failed_runs == ["recover-run", "queued-run"]
        assert resources.runs.get_run("recover-run").status == "failed"
        assert resources.runs.get_run("queued-run").status == "failed"

    oversized = SqliteRunLedger(data_dir / "dsagents_runs.db", data_dir / "artifacts", max_inline_bytes=10)
    oversized.create_run("big-run", "s-big", "blob")
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


def _check_tool_status_middleware() -> None:
    middleware = ToolStatusMiddleware()
    emitted: list[dict[str, str]] = []
    with patch("hands.get_stream_writer", return_value=emitted.append):
        result = middleware.wrap_tool_call(
            SimpleNamespace(tool_call={"name": "demo", "args": {"value": 1}}),
            lambda _request: {"ok": True},
        )
    assert result == {"ok": True}
    assert emitted == [
        {"name": "demo", "status": "started"},
        {"name": "demo", "status": "completed"},
    ]

    emitted = []
    with patch("hands.get_stream_writer", return_value=emitted.append):
        try:
            middleware.wrap_tool_call(
                SimpleNamespace(tool_call={"name": "demo", "args": {}}),
                lambda _request: (_ for _ in ()).throw(RuntimeError("boom")),
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("tool errors must be passed through")
    assert emitted == [
        {"name": "demo", "status": "started"},
        {"name": "demo", "status": "error"},
    ]


def _check_harness(tmp: str) -> None:
    data_dir = Path(tmp) / "harness-data"
    factory = _FakeBrainFactory()
    with AgentResources(ResourceConfig(data_dir=data_dir)) as resources:
        harness = HarnessRuntime(
            resources=resources,
            hands=ToolStatusHands(),
            tools=ToolCatalog(()),
            brain_factory=factory,
        )
        resources.runs.create_run("run-h1", "thread-a", "hello")
        events = list(harness.execute_run("hello", "thread-a", "run-h1"))
        assert [event.event_type for event in events] == ["status", "values", "thinking", "text_delta", "tool_status", "text_delta", "values", "status"]
        assert resources.runs.get_run("run-h1").reply == "echo[1]: hello"
        raw_events = resources.runs.get_run_events("run-h1")
        thinking_event = [event for event in raw_events if event.event_type == "thinking"][0]
        assert thinking_event.raw["type"] == "messages"

        resources.runs.create_run("run-h2", "thread-a", "again")
        list(harness.execute_run("again", "thread-a", "run-h2"))
        assert resources.runs.get_run("run-h2").reply == "echo[2]: again"


def _check_api(tmp: str) -> None:
    api_data_dir = Path(tmp) / "api-data"
    hold_control = _StreamControl()
    harness_creations = {"count": 0}
    factory = _FakeBrainFactory(control=hold_control)

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
        first = client.post("/runs", json={"message": "hello", "session_id": None})
        assert first.status_code == 200
        first_payload = first.json()
        session_id = first_payload["session_id"]
        run_id = first_payload["run_id"]
        assert first_payload == {
            "run_id": run_id,
            "session_id": session_id,
            "status": "queued",
        }
        first_run = _wait_for_run(client, run_id, "succeeded")
        assert first_run["reply"] == "echo[1]: hello"
        assert harness_creations["count"] == 1

        follow_up = client.post("/runs", json={"message": "again", "session_id": session_id})
        assert follow_up.status_code == 200
        follow_up_payload = follow_up.json()
        follow_up_run = _wait_for_run(client, follow_up_payload["run_id"], "succeeded")
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

        background = client.post("/runs", json={"message": "hold", "session_id": session_id})
        assert background.status_code == 200
        background_payload = background.json()
        assert hold_control.started.wait(timeout=5)
        running_payload = client.get(f"/runs/{background_payload['run_id']}").json()["run"]
        assert running_payload["status"] == "running"
        conflict = client.post("/runs", json={"message": "blocked", "session_id": session_id})
        assert conflict.status_code == 409
        assert conflict.json() == {
            "error": "该会话正在运行",
            "active_run_id": background_payload["run_id"],
        }

        upload_response = client.post(
            "/files",
            files={"file": ("../report.pdf", b"demo upload", "application/pdf")},
        )
        assert upload_response.status_code == 200
        upload_payload = upload_response.json()
        assert upload_payload["file_path"].startswith("/artifacts/uploads/")
        upload_name = Path(upload_payload["file_path"]).name
        assert upload_name.endswith("_report.pdf")
        upload_file = api_data_dir / "artifacts" / "uploads" / upload_name
        assert upload_file.read_bytes() == b"demo upload"

        hold_control.release.set()
        background_run = _wait_for_run(client, background_payload["run_id"], "succeeded")
        assert background_run["reply"] == "echo[3]: hold"

        failed = client.post("/runs", json={"message": "fail", "session_id": "resume-session"})
        assert failed.status_code == 200
        failed_payload = failed.json()
        failed_run = _wait_for_run(client, failed_payload["run_id"], "failed")
        assert failed_run["error"] == "planned failure"
        resumed = client.post("/runs", json={"message": "recover", "session_id": "resume-session"})
        assert resumed.status_code == 200
        resumed_run = _wait_for_run(client, resumed.json()["run_id"], "succeeded")
        assert resumed_run["reply"] == "echo[2]: recover"

        unknown = client.get("/runs/missing-run")
        assert unknown.status_code == 404
        assert unknown.json() == {"error": "Unknown run: missing-run"}


def _check_startup_recovery(tmp: str) -> None:
    cleanup_data_dir = Path(tmp) / "cleanup-api"
    cleanup_store = SqliteRunLedger(cleanup_data_dir / "dsagents_runs.db", cleanup_data_dir / "artifacts")
    cleanup_store.create_run("cleanup-queued", "cleanup-session", "queued")
    cleanup_store.create_run("cleanup-running", "cleanup-session", "running")
    cleanup_store.emit_run_status("cleanup-running", "running")
    cleanup_factory_count = {"count": 0}

    def cleanup_harness(resources: AgentResources) -> HarnessRuntime:
        cleanup_factory_count["count"] += 1
        return HarnessRuntime(
            resources=resources,
            hands=ToolStatusHands(),
            tools=ToolCatalog(()),
            brain_factory=_FakeBrainFactory(),
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


def _check_virtual_artifacts(tmp: str) -> None:
    api_data_dir = Path(tmp) / "api-data"
    virtual_source = api_data_dir / "artifacts" / "uploads" / "source.pdf"
    virtual_source.parent.mkdir(parents=True, exist_ok=True)
    virtual_source.write_text("demo", encoding="utf-8")
    with patch("tools._artifacts_root", return_value=(api_data_dir / "artifacts").resolve()):
        with patch.dict(
            os.environ,
            {
                "MINERU_BASE_URL": "https://mineru.example",
                "MINERU_BACKEND": "pipeline",
                "MINERU_EFFORT": "standard",
                "MINERU_TIMEOUT_SECONDS": "10",
            },
            clear=True,
        ):
            with patch("tools._submit_mineru_task", return_value="task-1"), patch(
                "tools._wait_for_mineru_result",
                return_value={"md_content": "# parsed"},
            ):
                parsed = parse_document(
                    "/artifacts/uploads/source.pdf",
                    "/artifacts/generated/output.md",
                )
        parsed_payload = json.loads(parsed)
        assert Path(parsed_payload["source"]) == virtual_source.resolve()
        assert Path(parsed_payload["output_path"]) == (api_data_dir / "artifacts" / "generated" / "output.md").resolve()
        assert Path(parsed_payload["output_path"]).read_text(encoding="utf-8") == "# parsed"
        try:
            parse_document("/artifacts/../x")
        except ValueError as exc:
            assert str(exc) == "Invalid /artifacts path: /artifacts/../x"
        else:
            raise AssertionError("parse_document must reject /artifacts path escapes")


def _wait_for_run(client: TestClient, run_id: str, expected_status: str) -> dict[str, object]:
    deadline = time.time() + 5
    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()["run"]
        if payload["status"] == expected_status:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached {expected_status}")


if __name__ == "__main__":
    main()
