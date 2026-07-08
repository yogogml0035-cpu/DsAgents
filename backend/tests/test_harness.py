from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AIMessageChunk

from hands import ToolStatusHands, ToolStatusMiddleware
from harness import (
    ARTIFACT_REFERENCE_HINT,
    DeepAgentsBrainFactory,
    HarnessRuntime,
    _assistant_message_payload,
    _thinking_delta,
)
from resources import AgentResources, ResourceConfig
from tests.test_support import FakeBrainFactory, artifact_block, messages_json, text_block, user_message
from tools import ToolCatalog


def run() -> None:
    assert _thinking_delta((AIMessageChunk(content=[{"type": "thinking", "thinking": "plan"}]), {})) == "plan"
    assert _assistant_message_payload(
        AIMessage(
            content=[
                {"type": "thinking", "thinking": "old", "index": 0},
                {"type": "text", "text": "answer"},
                {"type": "thinking", "thinking": "new", "index": 1, "signature": "sig"},
            ],
            id="assistant-final",
        ),
        tool_calls=[],
    ) == {
        "message_id": "assistant-final",
        "thinking": "new",
        "text": "answer",
    }

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        _check_model_env_loading(tmp)
        _check_tool_status_middleware()
        _check_harness(tmp)


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
    factory = FakeBrainFactory()
    with AgentResources(ResourceConfig(data_dir=data_dir)) as resources:
        harness = HarnessRuntime(
            resources=resources,
            hands=ToolStatusHands(),
            tools=ToolCatalog(()),
            brain_factory=factory,
        )
        hello_messages = [user_message(text_block("hello"))]
        resources.runs.create_run("run-h1", "thread-a", messages_json(hello_messages))
        events = list(harness.execute_run(hello_messages, "thread-a", "run-h1"))
        assert [event.event_type for event in events] == [
            "status",
            "thinking",
            "text_delta",
            "tool_call",
            "tool_status",
            "tool_result",
            "text_delta",
            "assistant_message",
            "status",
        ]
        assert resources.runs.get_run("run-h1").reply == "echo[1]: hello"
        raw_events = resources.runs.get_run_events("run-h1")
        thinking_event = [event for event in raw_events if event.event_type == "thinking"][0]
        assert thinking_event.raw["type"] == "messages"
        tool_call_event = [event for event in raw_events if event.event_type == "tool_call"][0]
        assert tool_call_event.payload == {
            "message_id": "assistant-tool-thread-a-1",
            "tool_call_id": "call-thread-a-1",
            "name": "read_file",
            "args": {"file_path": "/artifacts/uploads/demo.jpg"},
        }
        tool_result_event = [event for event in raw_events if event.event_type == "tool_result"][0]
        assert tool_result_event.payload == {
            "message_id": "tool-result-thread-a-1",
            "tool_call_id": "call-thread-a-1",
            "name": "read_file",
            "status": "success",
            "content_type": "image",
            "mime_type": "image/jpeg",
            "text": None,
            "preview": None,
        }
        assistant_event = [event for event in raw_events if event.event_type == "assistant_message"][0]
        assert assistant_event.payload == {
            "message_id": "assistant-final-thread-a-1",
            "thinking": "plan: ",
            "text": "echo[1]: hello",
        }
        assert factory.received_payloads[0] == hello_messages

        again_messages = [user_message(text_block("again"))]
        resources.runs.create_run("run-h2", "thread-a", messages_json(again_messages))
        list(harness.execute_run(again_messages, "thread-a", "run-h2"))
        assert resources.runs.get_run("run-h2").reply == "echo[2]: again"
        assert len(factory.threads["thread-a"]) == 2

        multimodal_messages = [
            user_message(text_block("Context first.")),
            user_message(text_block("What is in this file?"), artifact_block("/artifacts/uploads/demo.png")),
        ]
        resources.runs.create_run("run-h3", "thread-b", messages_json(multimodal_messages))
        list(harness.execute_run(multimodal_messages, "thread-b", "run-h3"))
        normalized_messages = factory.received_payloads[2]
        assert len(normalized_messages) == 2
        assert normalized_messages[1]["content"] == [
            {"type": "text", "text": "What is in this file?"},
            {
                "type": "text",
                "text": ARTIFACT_REFERENCE_HINT.format(path="/artifacts/uploads/demo.png"),
            },
        ]


if __name__ == "__main__":
    run()
