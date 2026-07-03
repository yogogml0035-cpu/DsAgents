from __future__ import annotations

import json
import os
import sqlite3
import tempfile
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

from api import create_app
from hands import TraceHands
from harness import DeepAgentsBrainFactory, HarnessRuntime, _thinking_delta
from resources import AgentResources, ResourceConfig
from session import SqliteSessionStore
from tools import ToolCatalog, _extract_markdown, _find_value, default_tool_catalog, parse_document


class _FakeBrain:
    def invoke(self, payload: dict, config: dict | None = None) -> dict:
        assert getattr(payload["messages"][0], "id", None) == "__remove_all__"
        text = payload["messages"][-1]["content"]
        return {"messages": [SimpleNamespace(content=f"echo: {text}")]}

    def stream(self, payload: dict, config: dict | None = None, **kwargs: object):
        assert getattr(payload["messages"][0], "id", None) == "__remove_all__"
        assert kwargs["stream_mode"] == ["messages", "custom", "values"]
        assert kwargs["version"] == "v2"
        text = payload["messages"][-1]["content"]
        yield {"type": "values", "ns": (), "data": {"messages": [{"role": "user", "content": text}]}, "interrupts": ()}
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
        yield {"type": "messages", "ns": (), "data": (AIMessageChunk(content="echo: "), {"langgraph_node": "model"})}
        yield {"type": "custom", "ns": (), "data": {"name": "parse_document", "status": "started"}}
        yield {"type": "messages", "ns": (), "data": (AIMessageChunk(content=text), {"langgraph_node": "model"})}
        yield {
            "type": "values",
            "ns": (),
            "data": {
                "messages": [
                    AIMessage(
                        content=[
                            {"type": "thinking", "thinking": "plan: "},
                            {"type": "text", "text": f"echo: {text}"},
                        ],
                        response_metadata={"model_provider": "anthropic"},
                    )
                ]
            },
            "interrupts": (),
        }


class _FakeBrainFactory:
    def create(self, **_: object) -> _FakeBrain:
        return _FakeBrain()


def main() -> None:
    assert (
        _thinking_delta((AIMessageChunk(content=[{"type": "thinking", "thinking": "plan"}]), {}))
        == "plan"
    )
    assert _find_value({"data": {"task_id": "abc"}}, {"task_id"}) == "abc"
    assert _extract_markdown({"result": {"md_content": "# ok"}}) == "# ok"
    assert default_tool_catalog().handlers[0].__name__ == "parse_document"

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        with patch.dict(os.environ, {}, clear=True):
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "MINIMAX_API_KEY=test-key\n"
                "MINIMAX_BASE_URL=https://minimax.example/anthropic\n"
                "MINIMAX_MODEL=test-minimax\n",
                encoding="utf-8",
            )
            load_dotenv(env_path)
            factory = DeepAgentsBrainFactory()
            assert factory.model.__class__.__name__ == "ChatAnthropic"
            assert getattr(factory.model, "model", None) == "test-minimax"
            assert factory.model.thinking == {"type": "adaptive"}
            assert factory.model.anthropic_api_key.get_secret_value() == "test-key"
            assert factory.model.anthropic_api_url == "https://minimax.example/anthropic"

        with patch.dict(os.environ, {}, clear=True):
            source = Path(tmp) / "sample.pdf"
            source.write_text("demo", encoding="utf-8")
            try:
                parse_document(str(source))
            except RuntimeError as exc:
                assert str(exc) == "Missing required environment variable: MINERU_BASE_URL"
            else:
                raise AssertionError("parse_document must fail fast when MINERU_BASE_URL is missing")

        data_dir = Path(tmp) / "data"
        with AgentResources(ResourceConfig(data_dir=data_dir)) as resources:
            assert resources.config.session_db.exists()
            assert resources.config.store_db.exists()
            assert resources.config.checkpoint_db.exists()
            assert resources.backend is not None

            resources.sessions.ensure_session("s1")
            resources.sessions.emit_event("s1", "note", {"ok": True})
            note_events = resources.sessions.get_events("s1")
            assert note_events[-1].payload["ok"] is True
            resources.sessions.emit_event("s1", "user_message", {"role": "user", "content": "u"})
            resources.sessions.emit_event("s1", "assistant_message", {"role": "assistant", "content": "a"})
            assert resources.sessions.context_window("s1").messages == [
                {"role": "user", "content": "u"},
                {"role": "assistant", "content": "a"},
            ]

            trace = TraceHands(resources.sessions).middleware("s1")[0]
            trace.wrap_model_call(
                SimpleNamespace(messages=[{"role": "user", "content": "ping"}]),
                lambda _request: SimpleNamespace(result=[SimpleNamespace(content="pong")]),
            )
            trace.wrap_tool_call(
                SimpleNamespace(tool_call={"name": "demo", "args": {"value": 1}}),
                lambda _request: {"ok": True},
            )
            trace_types = [event.event_type for event in resources.sessions.get_events("s1")]
            assert "model_request" in trace_types
            assert "tool_response" in trace_types
            try:
                trace.wrap_model_call(
                    SimpleNamespace(messages=[{"role": "user", "content": "bad"}]),
                    lambda _request: (_ for _ in ()).throw(ValueError("bad model")),
                )
            except ValueError:
                pass
            else:
                raise AssertionError("model errors must be passed through")
            assert resources.sessions.get_events("s1")[-1].event_type == "model_error"
            try:
                trace.wrap_tool_call(
                    SimpleNamespace(tool_call={"name": "bad", "args": {}}),
                    lambda _request: (_ for _ in ()).throw(RuntimeError("boom")),
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError("tool errors must be passed through")
            assert resources.sessions.get_events("s1")[-1].event_type == "tool_error"

            harness = HarnessRuntime(
                resources=resources,
                hands=TraceHands(resources.sessions),
                tools=ToolCatalog(()),
                brain_factory=_FakeBrainFactory(),
            )
            turn = harness.run_turn("hello", "s2")
            assert turn.result["messages"][-1].content == "echo: hello"
            assert turn.context.messages == [{"role": "user", "content": "hello"}]
            turn = harness.run_turn("again", "s2")
            assert turn.context.messages == [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "echo: hello"},
                {"role": "user", "content": "again"},
            ]
            turn_types = [event.event_type for event in resources.sessions.get_events("s2")]
            assert turn_types == ["user_message", "assistant_message", "user_message", "assistant_message"]

        oversized = SqliteSessionStore(data_dir / "dsagents_sessions.db", data_dir / "artifacts", max_inline_bytes=10)
        oversized.emit_event("s3", "tool_response", {"content": "x" * 100})
        large_event = oversized.get_events("s3")[-1]
        assert large_event.payload["content"] == "x" * 100
        assert any((data_dir / "artifacts" / "session-events").glob("*.json"))

        api_data_dir = Path(tmp) / "api-data"
        app = create_app(
            resource_config=ResourceConfig(data_dir=api_data_dir),
            harness_factory=_fake_harness,
        )
        with TestClient(app) as client:
            message_response = client.post("/sessions/messages", json={"message": "hello", "session_id": None})
            assert message_response.status_code == 200
            payload = message_response.json()
            session_id = payload["session_id"]
            assert payload == {"session_id": session_id, "reply": "echo: hello"}
            follow_up = client.post("/sessions/messages", json={"message": "again", "session_id": session_id})
            assert follow_up.status_code == 200
            assert follow_up.json() == {"session_id": session_id, "reply": "echo: again"}
            with sqlite3.connect(api_data_dir / "dsagents_sessions.db") as conn:
                session_count = conn.execute("select count(*) from sessions").fetchone()[0]
            assert session_count == 1

            with client.stream("POST", "/sessions/messages/stream", json={"message": "stream me", "session_id": None}) as response:
                assert response.status_code == 200
                events = _parse_sse("".join(response.iter_text()))
            event_names = [event["event"] for event in events]
            assert event_names[0] == "session"
            assert event_names[-1] == "done"
            assert "thinking_delta" in event_names
            assert "text_delta" in event_names
            assert "tool_status" in event_names
            assert (
                event_names.index("session")
                < event_names.index("thinking_delta")
                < event_names.index("text_delta")
                < event_names.index("tool_status")
                < event_names.index("done")
            )

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
                    parsed = json.loads(
                        parse_document(
                            "/artifacts/uploads/source.pdf",
                            "/artifacts/generated/output.md",
                        )
                    )
            assert Path(parsed["source"]) == virtual_source.resolve()
            assert Path(parsed["output_path"]) == (api_data_dir / "artifacts" / "generated" / "output.md").resolve()
            assert Path(parsed["output_path"]).read_text(encoding="utf-8") == "# parsed"
            try:
                parse_document("/artifacts/../x")
            except ValueError as exc:
                assert str(exc) == "Invalid /artifacts path: /artifacts/../x"
            else:
                raise AssertionError("parse_document must reject /artifacts path escapes")

    print("self-check passed")


def _fake_harness(resources: AgentResources) -> HarnessRuntime:
    return HarnessRuntime(
        resources=resources,
        hands=TraceHands(resources.sessions),
        tools=ToolCatalog(()),
        brain_factory=_FakeBrainFactory(),
    )


def _parse_sse(raw: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in raw.strip().split("\n\n"):
        lines = [line for line in block.splitlines() if line]
        if len(lines) != 2:
            continue
        events.append(
            {
                "event": lines[0].removeprefix("event: ").strip(),
                "data": json.loads(lines[1].removeprefix("data: ").strip()),
            }
        )
    return events


if __name__ == "__main__":
    main()
