from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dotenv import load_dotenv

from hands import TraceHands
from harness import DeepAgentsBrainFactory, HarnessRuntime
from resources import AgentResources, ResourceConfig
from session import SqliteSessionStore
from tools import ToolCatalog, _extract_markdown, _find_value


class _FakeBrain:
    def invoke(self, payload: dict, config: dict | None = None) -> dict:
        assert getattr(payload["messages"][0], "id", None) == "__remove_all__"
        text = payload["messages"][-1]["content"]
        return {"messages": [SimpleNamespace(content=f"echo: {text}")]}


class _FakeBrainFactory:
    def create(self, **_: object) -> _FakeBrain:
        return _FakeBrain()


def main() -> None:
    assert _find_value({"data": {"task_id": "abc"}}, {"task_id"}) == "abc"
    assert _extract_markdown({"result": {"md_content": "# ok"}}) == "# ok"

    with tempfile.TemporaryDirectory() as tmp:
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
            assert factory.model.anthropic_api_key.get_secret_value() == "test-key"
            assert factory.model.anthropic_api_url == "https://minimax.example/anthropic"

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

    print("self-check passed")


if __name__ == "__main__":
    main()
