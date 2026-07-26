from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from deepagents.middleware.memory import MemoryMiddleware
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from runtime.agent import (
    BACKEND_ENV_PATH,
    DeepAgentsBrainFactory,
)
from runtime.execution import ARTIFACT_REFERENCE_HINT, HarnessRuntime
from runtime.middleware import (
    NoProgressLoop,
    NoProgressMiddleware,
    StructuredOutputCompatibility,
    StructuredOutputRecovery,
    ToolTelemetry,
    is_empty_channel_data_shell,
    runtime_middlewares,
)
from runtime.observability import (
    assistant_message_payload,
    is_assistant_message,
    is_subagent_message,
    model_usage,
    thinking_delta,
)
from runtime.resources import RUNTIME_AGENTS_PATH, AgentResources, ResourceConfig
from runtime.tools import ToolCatalog
from skills.philips_wgq_inbound_recognition.schema import WAG_WORKFLOW, PhilipsWgqRecognitionResult
from skills.tecan_import.schema import DK_WORKFLOW, TecanOverseasRecognitionResult
from skills.tecan_import.scripts.tools import FINALIZE_TECAN_RESULT_TOOL
from tests.test_support import (
    FakeBrainFactory,
    _recognition_result,
    _tecan_recognition_result,
    artifact_block,
    messages_json,
    text_block,
    user_message,
)


def run() -> None:
    assert BACKEND_ENV_PATH == Path(__file__).resolve().parents[1] / ".env"
    assert is_subagent_message((AIMessageChunk(content="hidden"), {"lc_agent_name": "tecan-extractor-a"}))
    assert not is_subagent_message((AIMessageChunk(content="shown"), {"lc_agent_name": "dsagents-main"}))
    assert is_assistant_message((AIMessageChunk(content="shown"), {}))
    assert not is_assistant_message((ToolMessage(content="hidden", tool_call_id="tool-1"), {}))
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
    _check_structured_output_integration()
    _check_structured_output_recovery()
    _check_empty_data_shell_recovery()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        _check_model_env_loading(tmp)
        _check_tool_telemetry_middleware()
        _check_main_agent_memory_middleware()
        _check_no_progress_middleware()
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
        with patch("runtime.agent.create_deep_agent", return_value=object()) as create:
            factory.create(
                resources=SimpleNamespace(backend=object(), checkpointer=object(), store=object()),
                middleware=runtime_middlewares(structured_schema=PhilipsWgqRecognitionResult),
                tools=[],
                workflow=WAG_WORKFLOW,
            )
        kwargs = create.call_args.kwargs
        assert kwargs["model"] is factory.model
        compatibility = next(
            middleware
            for middleware in kwargs["middleware"]
            if isinstance(middleware, StructuredOutputCompatibility)
        )
        request = ModelRequest(
            model=factory.model,
            messages=[],
            response_format=kwargs["response_format"],
        )
        seen_requests = []

        def handler(adjusted_request: ModelRequest) -> ModelResponse:
            seen_requests.append(adjusted_request)
            return ModelResponse(result=[AIMessage(content="done")])

        compatibility.wrap_model_call(request, handler)
        assert len(seen_requests) == 1
        assert seen_requests[0].model.thinking is None
        assert factory.model.thinking == {"type": "adaptive"}


class _StructuredOutputResult(BaseModel):
    value: str


class _StructuredOutputModel(BaseChatModel):
    thinking: dict[str, str] | None = {"type": "adaptive"}
    bound_thinking: dict[str, str] | None = None

    @property
    def _llm_type(self) -> str:
        return "test-structured-output"

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        del messages, stop, run_manager, kwargs
        raise AssertionError("the unbound model must not be invoked")

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> RunnableLambda:
        del kwargs
        self.bound_thinking = self.thinking
        output_tool = tools[-1]
        tool_name = getattr(output_tool, "name", None)
        assert isinstance(tool_name, str)
        return RunnableLambda(
            lambda _input: AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "structured-call-1",
                        "name": tool_name,
                        "args": {"value": "ok"},
                        "type": "tool_call",
                    }
                ],
            )
        )


def _check_structured_output_integration() -> None:
    model = _StructuredOutputModel()
    agent = create_agent(
        model=model,
        middleware=[StructuredOutputCompatibility()],
        response_format=ToolStrategy(_StructuredOutputResult),
    )
    result = agent.invoke({"messages": [HumanMessage(content="return a result")]})
    assert result["structured_response"].value == "ok"
    assert model.bound_thinking is None
    assert model.thinking == {"type": "adaptive"}


def _check_structured_output_recovery() -> None:
    """Text JSON without schema tool_call is recovered; validation failures retry."""
    recovery = StructuredOutputRecovery(PhilipsWgqRecognitionResult, max_retries=2)
    payload = _recognition_result("success")
    payload["problems"] = [
        {
            "source": "pdf",
            "location": "header",
            "issue": "OM missing",
            "action": "leave null",
        }
    ]
    fenced = (
        "识别完成。\n\n```json\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n```\n"
    )
    update = recovery.after_model(
        {"messages": [AIMessage(content=fenced)], "structured_response": None},
        None,
    )
    assert update is not None
    recovered = update["structured_response"]
    assert isinstance(recovered, PhilipsWgqRecognitionResult)
    assert recovered.outcome == "success"
    assert len(recovered.problems) == 1
    assert recovered.data is not None
    assert recovered.data.header.original_waybill_number == "9198153694"
    assert update.get("structured_recovery_attempts") == 0

    # Already present: no-op.
    assert (
        recovery.after_model(
            {
                "messages": [AIMessage(content=fenced)],
                "structured_response": recovered,
            },
            None,
        )
        is None
    )
    # Tool call pending: do not steal from ToolStrategy path.
    assert (
        recovery.after_model(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "c1",
                                "name": "parse_documents",
                                "args": {"paths": []},
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            },
            None,
        )
        is None
    )

    # No JSON: jump back to model with a correction message (attempt 1).
    no_json = recovery.after_model(
        {"messages": [AIMessage(content="no json here")]},
        None,
    )
    assert no_json is not None
    assert no_json["jump_to"] == "model"
    assert no_json["structured_recovery_attempts"] == 1
    assert isinstance(no_json["messages"][0], HumanMessage)
    assert "结构化输出被拒绝" in no_json["messages"][0].content
    assert "JSON" in no_json["messages"][0].content

    # Invalid schema JSON: also jump_to model with validation error.
    bad_payload = _recognition_result("success")
    bad_payload["outcome"] = "not-an-outcome"
    bad_fenced = "```json\n" + json.dumps(bad_payload) + "\n```"
    invalid = recovery.after_model(
        {"messages": [AIMessage(content=bad_fenced)], "structured_recovery_attempts": 0},
        None,
    )
    assert invalid is not None
    assert invalid["jump_to"] == "model"
    assert invalid["structured_recovery_attempts"] == 1
    assert "结构化输出被拒绝" in invalid["messages"][0].content
    assert "问题：" in invalid["messages"][0].content

    # Exhausted retries: exit graph (jump_to end) so ToolStrategy does not loop.
    exhausted = recovery.after_model(
        {
            "messages": [AIMessage(content=bad_fenced)],
            "structured_recovery_attempts": 2,
        },
        None,
    )
    assert exhausted is not None
    assert exhausted.get("jump_to") == "end"
    assert "structured_response" not in exhausted

    # Empty assistant text: first failure still retries (same budget as parse fail).
    empty_retry = recovery.after_model(
        {"messages": [AIMessage(content="")], "structured_recovery_attempts": 0},
        None,
    )
    assert empty_retry is not None
    assert empty_retry.get("jump_to") == "model"
    assert empty_retry.get("structured_recovery_attempts") == 1
    empty_exit = recovery.after_model(
        {"messages": [AIMessage(content="")], "structured_recovery_attempts": 2},
        None,
    )
    assert empty_exit is not None
    assert empty_exit.get("jump_to") == "end"

    # End-to-end: first reply invalid JSON text, second reply valid → structured.
    class _RetryThenOkModel(BaseChatModel):
        calls: int = 0

        @property
        def _llm_type(self) -> str:
            return "test-retry-text-json"

        def _generate(
            self,
            messages: list[Any],
            stop: list[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> Any:
            del stop, run_manager, kwargs
            from langchain_core.outputs import ChatGeneration, ChatResult

            self.calls += 1
            if self.calls == 1:
                content = "almost done but not json yet"
            else:
                # Retry prompt must have been appended as a human message.
                assert any(
                    isinstance(message, HumanMessage)
                    and "结构化输出被拒绝" in str(message.content)
                    for message in messages
                )
                content = (
                    "done\n```json\n"
                    + json.dumps(payload, ensure_ascii=False)
                    + "\n```"
                )
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=content))]
            )

        def bind_tools(self, tools: list[Any], **kwargs: Any) -> Any:
            del tools, kwargs
            return self

    retry_model = _RetryThenOkModel()
    agent = create_agent(
        model=retry_model,
        middleware=[StructuredOutputRecovery(PhilipsWgqRecognitionResult, max_retries=2)],
        response_format=ToolStrategy(PhilipsWgqRecognitionResult),
    )
    e2e = agent.invoke({"messages": [HumanMessage(content="recognize")]})
    assert retry_model.calls == 2
    assert isinstance(e2e["structured_response"], PhilipsWgqRecognitionResult)
    assert e2e["structured_response"].outcome == "success"
    assert e2e["structured_response"].problems

    # Exhaust retries end-to-end: no structured_response key.
    class _AlwaysBadModel(BaseChatModel):
        calls: int = 0

        @property
        def _llm_type(self) -> str:
            return "test-always-bad"

        def _generate(
            self,
            messages: list[Any],
            stop: list[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> Any:
            del messages, stop, run_manager, kwargs
            from langchain_core.outputs import ChatGeneration, ChatResult

            self.calls += 1
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="still no json"))]
            )

        def bind_tools(self, tools: list[Any], **kwargs: Any) -> Any:
            del tools, kwargs
            return self

    always_bad = _AlwaysBadModel()
    fail_agent = create_agent(
        model=always_bad,
        middleware=[StructuredOutputRecovery(PhilipsWgqRecognitionResult, max_retries=2)],
        response_format=ToolStrategy(PhilipsWgqRecognitionResult),
    )
    failed = fail_agent.invoke({"messages": [HumanMessage(content="recognize")]})
    assert "structured_response" not in failed or failed.get("structured_response") is None
    # initial model + max_retries correction loops (2) = 3 calls, then jump_to end
    assert always_bad.calls == 3


def _check_empty_data_shell_recovery() -> None:
    empty_shell = {"outcome": "success", "data": {}, "problems": []}
    assert is_empty_channel_data_shell(empty_shell)
    assert is_empty_channel_data_shell({**empty_shell, "outcome": "input_problems"})
    assert not is_empty_channel_data_shell(_recognition_result("input problems"))

    for schema, payload in (
        (PhilipsWgqRecognitionResult, _recognition_result("success")),
        (TecanOverseasRecognitionResult, _tecan_recognition_result()),
    ):
        recovery = StructuredOutputRecovery(schema, max_retries=2)
        text = "```json\n" + json.dumps(empty_shell, ensure_ascii=False) + "\n```"
        retry = recovery.after_model(
            {"messages": [AIMessage(content=text)], "structured_recovery_attempts": 0},
            None,
        )
        assert retry is not None
        assert retry["jump_to"] == "model"
        assert retry["structured_recovery_attempts"] == 1
        assert "data" in retry["messages"][0].content

        exhausted = recovery.after_model(
            {"messages": [AIMessage(content=text)], "structured_recovery_attempts": 2},
            None,
        )
        assert exhausted is not None
        assert exhausted["jump_to"] == "end"
        fallback = exhausted["structured_response"]
        assert isinstance(fallback, schema)
        assert fallback.outcome == "input_problems"
        assert fallback.data.items == []
        assert all(value is None for value in fallback.data.header.model_dump().values())
        assert fallback.problems[0].source == "runtime"

        valid = recovery.after_model(
            {"messages": [AIMessage(content="```json\n" + json.dumps(payload) + "\n```")]},
            None,
        )
        assert valid is not None
        assert isinstance(valid["structured_response"], schema)

    recovery = StructuredOutputRecovery(PhilipsWgqRecognitionResult, max_retries=2)
    paired_ai = AIMessage(
        content="```json\n" + json.dumps(_recognition_result("success")) + "\n```",
        tool_calls=[
            {
                "id": "struct-empty-text-1",
                "name": "PhilipsWgqRecognitionResult",
                "args": empty_shell,
                "type": "tool_call",
            }
        ],
    )
    paired = recovery.after_model(
        {
            "messages": [
                paired_ai,
                ToolMessage(
                    content="schema validation failed",
                    tool_call_id="struct-empty-text-1",
                    name="PhilipsWgqRecognitionResult",
                ),
            ]
        },
        None,
    )
    assert paired is not None
    assert paired["jump_to"] == "end"
    assert paired["structured_response"].data.header.original_waybill_number == "9198153694"
    assert (
        recovery.after_model(
            {
                "messages": [
                    paired_ai,
                    ToolMessage(
                        content="schema validation failed",
                        tool_call_id="other-call",
                        name="PhilipsWgqRecognitionResult",
                    ),
                ]
            },
            None,
        )
        is None
    )

    class _AlwaysEmptyShellModel(BaseChatModel):
        calls: int = 0

        @property
        def _llm_type(self) -> str:
            return "test-empty-structured-shell"

        def _generate(
            self,
            messages: list[Any],
            stop: list[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> Any:
            raise AssertionError("the ToolStrategy-bound model must be invoked")

        def bind_tools(self, tools: list[Any], **kwargs: Any) -> RunnableLambda:
            del kwargs
            tool_name = getattr(tools[-1], "name", None)
            assert isinstance(tool_name, str)

            def reply(_input: Any) -> AIMessage:
                self.calls += 1
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": f"empty-shell-{self.calls}",
                            "name": tool_name,
                            "args": empty_shell,
                            "type": "tool_call",
                        }
                    ],
                )

            return RunnableLambda(reply)

    empty_shell_model = _AlwaysEmptyShellModel()
    empty_shell_agent = create_agent(
        model=empty_shell_model,
        middleware=[StructuredOutputRecovery(PhilipsWgqRecognitionResult, max_retries=2)],
        response_format=ToolStrategy(PhilipsWgqRecognitionResult),
    )
    empty_shell_result = empty_shell_agent.invoke(
        {"messages": [HumanMessage(content="recognize")]}
    )["structured_response"]
    assert empty_shell_model.calls == 3
    assert empty_shell_result.outcome == "input_problems"
    assert empty_shell_result.data.items == []
    assert empty_shell_result.problems[0].source == "runtime"


def _check_tool_telemetry_middleware() -> None:
    middleware = ToolTelemetry()
    emitted: list[dict[str, object]] = []
    request = SimpleNamespace(
        tool_call={"name": "demo", "args": {"value": 1}},
        runtime=SimpleNamespace(config={"metadata": {"langgraph_node": "agent"}}),
    )
    with patch("runtime.middleware.get_stream_writer", return_value=emitted.append):
        result = middleware.wrap_tool_call(request, lambda _request: {"ok": True})
    assert result == {"ok": True}
    statuses = [event["status"] for event in emitted]
    assert statuses == ["started", "completed"]
    assert emitted[0]["name"] == "demo"
    assert emitted[0]["agent_name"] == "agent"
    assert emitted[0]["args"] == {"value": 1}
    assert "duration_ms" in emitted[1]
    assert "result" not in emitted[1]

    emitted = []
    with patch("runtime.middleware.get_stream_writer", return_value=emitted.append):
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


def _check_main_agent_memory_middleware() -> None:
    """Main-agent middleware stack has exactly one restricted MemoryMiddleware."""
    from langchain_core.messages import SystemMessage

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        with AgentResources(ResourceConfig(data_dir=Path(tmp) / "data")) as resources:
            middleware = runtime_middlewares(memory_backend=resources.backend)
            memory_items = [item for item in middleware if isinstance(item, MemoryMiddleware)]
            assert len(memory_items) == 1
            mm = memory_items[0]
            assert mm.sources == [RUNTIME_AGENTS_PATH]
            assert mm._add_cache_control is True
            update = mm.before_agent(
                {},
                SimpleNamespace(context=None, stream_writer=None, store=resources.store),
                {},
            )
            assert update is not None
            contents = update["memory_contents"]
            assert RUNTIME_AGENTS_PATH in contents
            assert "extract_archives" in contents[RUNTIME_AGENTS_PATH]
            request = ModelRequest(
                model=object(),
                messages=[],
                system_message=SystemMessage(content="base"),
                state={"memory_contents": contents},
            )
            modified = mm.modify_request(request)
            text = modified.system_message.content
            if not isinstance(text, str):
                text = str(text)
            assert "extract_archives" in text
            assert "Learning from feedback" not in text


def _check_no_progress_middleware() -> None:
    middleware = NoProgressMiddleware()

    def loop_messages(count: int, *, second_arg: int = 1) -> list[object]:
        messages: list[object] = [HumanMessage(content="repeat this")]
        for index in range(count):
            call_id = f"call-{index}"
            messages.extend(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": call_id,
                                "name": "demo",
                                "args": {"value": second_arg},
                            }
                        ],
                    ),
                    ToolMessage(
                        content="ok",
                        tool_call_id=call_id,
                        name="demo",
                    ),
                ]
            )
        return messages

    middleware.before_model({"messages": loop_messages(2)}, None)
    try:
        middleware.before_model({"messages": loop_messages(3)}, None)
    except NoProgressLoop as exc:
        assert "repeated demo" in str(exc)
    else:
        raise AssertionError("repeated tool calls must be detected")

    # A new human turn resets the derived window without mutable middleware
    # state, and different arguments are not considered the same call.
    middleware.before_model(
        {"messages": loop_messages(3) + [HumanMessage(content="new turn")]},
        None,
    )
    different = loop_messages(3)
    different[-2].tool_calls[0]["args"] = {"value": 2}
    middleware.before_model({"messages": different}, None)


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

        tecan_messages = [user_message(text_block("tecan final"))]
        resources.runs.create_run("run-tecan", "thread-tecan", messages_json(tecan_messages))
        list(harness.execute_run(tecan_messages, "thread-tecan", "run-tecan"))
        tecan_snapshot = resources.runs.get_run("run-tecan")
        assert tecan_snapshot.status == "succeeded"
        assert tecan_snapshot.result["data"]["header"]["po"] == "PO123"
        assert len(tecan_snapshot.result["data"]["items"][0]) == 24
        tecan_tools = [
            event.payload["name"]
            for event in resources.runs.get_run_events("run-tecan")
            if event.event_type == "tool_execution"
        ]
        assert "finalize_tecan_overseas_recognition" in tecan_tools

        # Usage: one main_agent call + one subagent call, summed correctly.
        # The subagent chunk carried usage but its text never leaks as text_delta.
        usage_events = [event for event in raw_events if event.event_type == "model_usage"]
        assert [event.payload["scope"] for event in usage_events] == ["subagent", "main_agent"]
        assert all(event.payload["model"] == "MiniMax-M3" for event in usage_events)
        assert "subagent secret" not in "".join(
            event.payload["content"] for event in raw_events if event.event_type == "text_delta"
        )
        assert "tool result must stay private" not in "".join(
            event.payload["content"] for event in raw_events if event.event_type == "text_delta"
        )
        agg = resources.runs.aggregate_model_usage("run-h1")
        assert agg["model_calls"] == 2
        assert agg["input_tokens"] == 1000 + 200
        assert agg["output_tokens"] == 300 + 40
        assert agg["cache_read_input_tokens"] == 600 + 50
        assert agg["cache_creation_input_tokens"] == (200 + 50 + 30) + 10
        assert agg["by_agent"][("main_agent", "dsagents-main")]["model_calls"] == 1
        assert agg["by_agent"][("subagent", "tecan-extractor-a")]["model_calls"] == 1

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
        normalized_messages = factory.received_payloads[3]
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
        assert fail_agg["by_agent"][("subagent", "tecan-extractor-a")]["model_calls"] == 1

        workflow_messages = [user_message(text_block("workflow success"))]
        resources.runs.create_run(
            "run-workflow",
            "thread-workflow",
            messages_json(workflow_messages),
            workflow=WAG_WORKFLOW,
        )
        workflow_events = list(
            harness.execute_run(
                workflow_messages,
                "thread-workflow",
                "run-workflow",
                workflow=WAG_WORKFLOW,
            )
        )
        workflow_snapshot = resources.runs.get_run("run-workflow")
        assert workflow_snapshot.status == "succeeded"
        assert workflow_snapshot.workflow == WAG_WORKFLOW
        assert workflow_snapshot.result["data"]["header"]["original_waybill_number"] == "9198153694"
        assert workflow_snapshot.reply == "渠道识别完成，结果已写入 run.result。"
        assert workflow_events[-1].payload["result"] == workflow_snapshot.result
        assert "tool result must stay private" not in workflow_snapshot.reply
        assert not any(
            event.event_type in {"thinking", "text_delta", "assistant_message"}
            for event in workflow_events
        )
        assert PhilipsWgqRecognitionResult.__name__ not in [
            event.payload["name"]
            for event in workflow_events
            if event.event_type == "tool_execution"
        ]

        input_problem_messages = [user_message(text_block("input problems"))]
        resources.runs.create_run(
            "run-input-problems",
            "thread-input-problems",
            messages_json(input_problem_messages),
            workflow=WAG_WORKFLOW,
        )
        list(
            harness.execute_run(
                input_problem_messages,
                "thread-input-problems",
                "run-input-problems",
                workflow=WAG_WORKFLOW,
            )
        )
        assert resources.runs.get_run("run-input-problems").status == "succeeded"
        assert resources.runs.get_run("run-input-problems").result["outcome"] == "input_problems"

        missing_messages = [user_message(text_block("missing structured"))]
        resources.runs.create_run(
            "run-missing-structured",
            "thread-missing-structured",
            messages_json(missing_messages),
            workflow=WAG_WORKFLOW,
        )
        list(
            harness.execute_run(
                missing_messages,
                "thread-missing-structured",
                "run-missing-structured",
                workflow=WAG_WORKFLOW,
            )
        )
        missing_snapshot = resources.runs.get_run("run-missing-structured")
        assert missing_snapshot.status == "failed"
        assert "structured_response missing" in missing_snapshot.error

        dk_messages = [user_message(text_block("workflow success"))]
        resources.runs.create_run(
            "run-dk-workflow",
            "thread-dk-workflow",
            messages_json(dk_messages),
            workflow=DK_WORKFLOW,
        )
        list(
            harness.execute_run(
                dk_messages,
                "thread-dk-workflow",
                "run-dk-workflow",
                workflow=DK_WORKFLOW,
            )
        )
        dk_snapshot = resources.runs.get_run("run-dk-workflow")
        assert dk_snapshot.status == "succeeded"
        assert dk_snapshot.result["data"]["header"]["po"] == "PO123"
        assert dk_snapshot.reply == "渠道识别完成，结果已写入 run.result。"
        assert "tool result must stay private" not in dk_snapshot.reply
        assert not any(
            event.event_type in {"thinking", "text_delta", "assistant_message"}
            for event in resources.runs.get_run_events("run-dk-workflow")
        )
        assert TecanOverseasRecognitionResult.__name__ not in [
            event.payload["name"]
            for event in resources.runs.get_run_events("run-dk-workflow")
            if event.event_type == "tool_execution"
        ]
        assert FINALIZE_TECAN_RESULT_TOOL not in [
            event.payload["name"]
            for event in resources.runs.get_run_events("run-dk-workflow")
            if event.event_type == "tool_execution"
        ]

        dk_problem_messages = [user_message(text_block("input problems"))]
        resources.runs.create_run(
            "run-dk-input-problems",
            "thread-dk-input-problems",
            messages_json(dk_problem_messages),
            workflow=DK_WORKFLOW,
        )
        list(
            harness.execute_run(
                dk_problem_messages,
                "thread-dk-input-problems",
                "run-dk-input-problems",
                workflow=DK_WORKFLOW,
            )
        )
        assert resources.runs.get_run("run-dk-input-problems").result["outcome"] == "input_problems"

        missing_dk_messages = [user_message(text_block("missing structured"))]
        resources.runs.create_run(
            "run-dk-missing-structured",
            "thread-dk-missing-structured",
            messages_json(missing_dk_messages),
            workflow=DK_WORKFLOW,
        )
        list(
            harness.execute_run(
                missing_dk_messages,
                "thread-dk-missing-structured",
                "run-dk-missing-structured",
                workflow=DK_WORKFLOW,
            )
        )
        assert "structured_response missing for DK" in resources.runs.get_run(
            "run-dk-missing-structured"
        ).error


if __name__ == "__main__":
    run()
