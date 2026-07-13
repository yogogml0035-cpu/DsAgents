from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AIMessageChunk

from dsagents.runtime.agent import (
    DeepAgentsBrainFactory,
    ToolTelemetry,
)
from dsagents.runtime.execution import ARTIFACT_REFERENCE_HINT, HarnessRuntime
from dsagents.runtime.observability import (
    assistant_message_payload,
    is_subagent_message,
    model_usage,
    thinking_delta,
)
from dsagents.runtime.resources import AgentResources, ResourceConfig
from dsagents.runtime.tools import ToolCatalog
from tests.test_support import (
    FakeBrainFactory,
    artifact_block,
    messages_json,
    text_block,
    user_message,
)


def run() -> None:
    assert is_subagent_message((AIMessageChunk(content="hidden"), {"lc_agent_name": "tecan-extractor-a"}))
    assert not is_subagent_message((AIMessageChunk(content="shown"), {"lc_agent_name": "dsagents-main"}))
    assert thinking_delta((AIMessageChunk(content=[{"type": "thinking", "thinking": "plan"}]), {})) == "plan"
    _check_model_usage_helper()
    assert assistant_message_payload(
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
        _check_tool_telemetry_middleware()
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


def _check_tool_telemetry_middleware() -> None:
    middleware = ToolTelemetry()
    emitted: list[dict[str, object]] = []
    request = SimpleNamespace(
        tool_call={"name": "demo", "args": {"value": 1}},
        runtime=SimpleNamespace(config={"metadata": {"langgraph_node": "agent"}}),
    )
    with patch("dsagents.runtime.agent.get_stream_writer", return_value=emitted.append):
        result = middleware.wrap_tool_call(request, lambda _request: {"ok": True})
    assert result == {"ok": True}
    statuses = [event["status"] for event in emitted]
    assert statuses == ["started", "completed"]
    assert emitted[0]["name"] == "demo"
    assert emitted[0]["agent_name"] == "agent"
    assert emitted[0]["args"] == {"value": 1}
    assert "duration_ms" in emitted[1]
    assert "result" in emitted[1]

    emitted = []
    with patch("dsagents.runtime.agent.get_stream_writer", return_value=emitted.append):
        try:
            middleware.wrap_tool_call(
                SimpleNamespace(
                    tool_call={"name": "demo", "args": {}},
                    runtime=SimpleNamespace(config={"metadata": {"langgraph_node": "agent"}}),
                ),
                lambda _request: (_ for _ in ()).throw(RuntimeError("boom")),
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("tool errors must be passed through")
    assert [event["status"] for event in emitted] == ["started", "error"]


def _check_model_usage_helper() -> None:
    # No usage_metadata => nothing to record.
    assert model_usage((AIMessageChunk(content="x"), {"langgraph_node": "model"})) is None
    # Main agent call: input_token_details optional, cache fields default to 0.
    main_usage = model_usage(
        (
            AIMessageChunk(
                content="x",
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            ),
            {"langgraph_node": "model"},
        )
    )
    assert main_usage == {
        "model": "MiniMax-M3",
        "scope": "main_agent",
        "agent_name": "dsagents-main",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    # Subagent call: scope/agent_name come from the same chunk metadata, and
    # cache_creation sums the generic + 5m + 1h detail fields.
    sub_usage = model_usage(
        (
            AIMessageChunk(
                content="x",
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 8,
                    "total_tokens": 108,
                    "input_token_details": {
                        "cache_read": 30,
                        "cache_creation": 0,
                        "ephemeral_5m_input_tokens": 7,
                        "ephemeral_1h_input_tokens": 3,
                    },
                },
            ),
            {"langgraph_node": "model", "lc_agent_name": "tecan-extractor-a"},
        )
    )
    assert sub_usage == {
        "model": "MiniMax-M3",
        "scope": "subagent",
        "agent_name": "tecan-extractor-a",
        "input_tokens": 100,
        "output_tokens": 8,
        "cache_read_input_tokens": 30,
        "cache_creation_input_tokens": 10,
    }


def _check_harness(tmp: str) -> None:
    data_dir = Path(tmp) / "harness-data"
    factory = FakeBrainFactory()
    with AgentResources(ResourceConfig(data_dir=data_dir)) as resources:
        harness = HarnessRuntime(
            resources=resources,
            tools=ToolCatalog(()),
            brain_factory=factory,
        )
        hello_messages = [user_message(text_block("hello"))]
        resources.runs.create_run("run-h1", "thread-a", messages_json(hello_messages))
        events = list(harness.execute_run(hello_messages, "thread-a", "run-h1"))
        assert [event.event_type for event in events] == [
            "status",
            "thinking",
            "model_usage",
            "text_delta",
            "tool_execution",
            "tool_progress",
            "model_usage",
            "text_delta",
            "assistant_message",
            "status",
        ]
        assert resources.runs.get_run("run-h1").reply == "echo[1]: hello"
        raw_events = resources.runs.get_run_events("run-h1")
        thinking_event = [event for event in raw_events if event.event_type == "thinking"][0]
        assert thinking_event.raw["type"] == "messages"
        tool_execution_event = [event for event in raw_events if event.event_type == "tool_execution"][0]
        assert tool_execution_event.payload == {
            "message_id": "assistant-tool-thread-a-1",
            "tool_call_id": "call-thread-a-1",
            "name": "read_file",
            "args": {"file_path": "/artifacts/uploads/demo.jpg"},
        }
        tool_progress_event = [event for event in raw_events if event.event_type == "tool_progress"][0]
        assert tool_progress_event.payload == {"name": "parse_documents", "status": "started"}
        assert tool_progress_event.raw["type"] == "custom"
        assistant_event = [event for event in raw_events if event.event_type == "assistant_message"][0]
        assert assistant_event.payload == {
            "message_id": "assistant-final-thread-a-1",
            "thinking": "plan: ",
            "text": "echo[1]: hello",
        }
        assert factory.received_payloads[0] == hello_messages

        # Usage: one main_agent call + one subagent call, summed correctly.
        # The subagent chunk carried usage but its text never leaks as text_delta.
        usage_events = [event for event in raw_events if event.event_type == "model_usage"]
        assert [event.payload["scope"] for event in usage_events] == ["subagent", "main_agent"]
        assert all(event.payload["model"] == "MiniMax-M3" for event in usage_events)
        assert "subagent secret" not in "".join(
            event.payload["content"] for event in raw_events if event.event_type == "text_delta"
        )
        agg = resources.runs.aggregate_model_usage("run-h1")
        assert agg["model_calls"] == 2
        assert agg["input_tokens"] == 1000 + 200
        assert agg["output_tokens"] == 300 + 40
        assert agg["cache_read_input_tokens"] == 600 + 50
        assert agg["cache_creation_input_tokens"] == (200 + 50 + 30) + 10
        assert agg["by_agent"][("main_agent", "dsagents-main")]["model_calls"] == 1
        assert agg["by_agent"][("subagent", "philips-wgq-extractor-a")]["model_calls"] == 1

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

        # Failed run (own thread so it never perturbs thread-a/b history counts)
        # still preserves model_usage written before the exception is raised.
        fail_messages = [user_message(text_block("fail"))]
        resources.runs.create_run("run-fail", "thread-c", messages_json(fail_messages))
        list(harness.execute_run(fail_messages, "thread-c", "run-fail"))
        assert resources.runs.get_run("run-fail").status == "failed"
        fail_agg = resources.runs.aggregate_model_usage("run-fail")
        assert fail_agg is not None
        # The subagent chunk is yielded before the "fail" raise, so its usage is kept.
        assert fail_agg["model_calls"] == 1
        assert fail_agg["by_agent"][("subagent", "philips-wgq-extractor-a")]["model_calls"] == 1


if __name__ == "__main__":
    run()
