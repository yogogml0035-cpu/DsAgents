from __future__ import annotations

from typing import Any, Iterator, Sequence

from langgraph.errors import GraphDrained
from langgraph.runtime import RunControl

from runtime import observability
from runtime.agent import BrainFactory
from runtime.middleware import NoProgressLoop, runtime_middlewares
from runtime.resources import AgentResources
from runtime.runs import RunEvent
from runtime.tools import ToolCatalog
from skills.channel_contract import finalize_channel_result
from skills.philips_wgq_inbound_recognition import WAG_WORKFLOW, PhilipsWgqRecognitionResult
from skills.tecan_import.schema import DK_WORKFLOW, TecanOverseasRecognitionResult
from skills.tecan_import.scripts.tools import FINALIZE_TECAN_RESULT_TOOL


ARTIFACT_REFERENCE_HINT = (
    "已上传 artifact：{path}。图片/媒体请用 read_file；"
    "需要结构化抽取的文档请用 parse_documents。"
)

_WORKFLOW_SCHEMAS = {
    WAG_WORKFLOW: PhilipsWgqRecognitionResult,
    DK_WORKFLOW: TecanOverseasRecognitionResult,
}


class HarnessRuntime:
    def __init__(
        self,
        *,
        resources: AgentResources,
        tools: ToolCatalog,
        brain_factory: BrainFactory,
    ) -> None:
        self.resources = resources
        self.tools = tools
        self.brain_factory = brain_factory
        self.run_controls: dict[str, RunControl] = {}

    def execute_run(
        self,
        messages: Sequence[dict[str, Any]],
        session_id: str,
        run_id: str,
        workflow: str | None = None,
    ) -> Iterator[RunEvent]:
        assistant_text = ""
        text_parts: list[str] = []
        result: dict[str, Any] | None = None
        structured_response: Any = None
        tecan_response: TecanOverseasRecognitionResult | None = None
        workflow_schema = _WORKFLOW_SCHEMAS.get(workflow)
        normalized_messages = _normalize_messages(messages)
        yield self.resources.runs.emit_run_status(run_id, "running")
        control = RunControl()
        self.run_controls[run_id] = control
        try:
            brain = self.brain_factory.create(
                resources=self.resources,
                middleware=runtime_middlewares(
                    memory_backend=self.resources.backend,
                    structured_schema=workflow_schema,
                ),
                tools=self.tools.as_list(),
                workflow=workflow,
            )
            for chunk in brain.stream(
                {"messages": normalized_messages},
                config={"configurable": {"thread_id": session_id}},
                stream_mode=["messages", "custom", "updates"],
                subgraphs=True,
                version="v2",
                control=control,
            ):
                # Cooperative drain: langgraph checks RunControl inside its own
                # loop; we also check between chunks so a drain requested mid-step
                # is honored before emitting more events.
                if control.drain_requested:
                    raise GraphDrained()
                if not isinstance(chunk, dict):
                    continue
                kind = chunk["type"]
                data = chunk.get("data")
                if kind == "messages":
                    # Usage is extracted before the subagent text filter so
                    # subagent model cost is captured even though subagent text
                    # stays private.
                    usage = observability.model_usage(data)
                    if usage is not None:
                        yield self.resources.runs.emit_run_event(
                            run_id, "model_usage", usage, raw=chunk
                        )
                    if observability.is_subagent_message(data):
                        continue
                    # ToolMessage chunks carry raw tool results and ToolStrategy
                    # validation errors. They are model context, not assistant text.
                    if not observability.is_assistant_message(data):
                        continue
                    # Channel workflows expose their validated result only at
                    # completion; intermediate model narration is not a client reply.
                    if workflow_schema is not None:
                        continue
                    thinking = observability.thinking_delta(data)
                    if thinking:
                        yield self.resources.runs.emit_run_event(
                            run_id, "thinking", {"content": thinking}, raw=chunk
                        )
                    text = observability.message_delta(data)
                    if text:
                        text_parts.append(text)
                        yield self.resources.runs.emit_run_event(
                            run_id, "text_delta", {"content": text}, raw=chunk
                        )
                elif kind == "custom":
                    payload = observability.stream_payload(data)
                    name = payload.get("name")
                    # tool_telemetry middleware emits tool_execution; tool bodies
                    # (parse_documents, extract_archives) emit progress payloads.
                    if name in {"parse_documents", "extract_archives"}:
                        yield self.resources.runs.emit_run_event(
                            run_id, "tool_progress", payload, raw=chunk
                        )
                    else:
                        yield self.resources.runs.emit_run_event(
                            run_id, "tool_execution", payload, raw=chunk
                        )
                elif kind == "updates":
                    candidate = _structured_response(data)
                    if candidate is not None:
                        structured_response = candidate
                    finalized_tecan = _tecan_finalized_response(data)
                    if finalized_tecan is not None:
                        tecan_response = finalized_tecan
                    for event_type, payload in _update_events(data):
                        if workflow_schema is not None:
                            if event_type == "assistant_message":
                                continue
                            # ToolStrategy arguments are intermediate business
                            # drafts; the validated copy belongs only in run.result.
                            if (
                                event_type == "tool_execution"
                                and payload.get("name") == workflow_schema.__name__
                            ):
                                continue
                        if event_type == "assistant_message" and payload.get("text"):
                            assistant_text = payload["text"]
                        yield self.resources.runs.emit_run_event(
                            run_id, event_type, payload, raw=chunk
                        )
            if workflow_schema is not None:
                if structured_response is None:
                    raise ValueError(f"structured_response missing for {workflow}")
                result = finalize_channel_result(workflow_schema, structured_response)
            elif tecan_response is not None:
                result = finalize_channel_result(TecanOverseasRecognitionResult, tecan_response)
        except GraphDrained:
            yield self.resources.runs.emit_run_status(
                run_id, "cancelled", error="run cancelled", raw={"status": "cancelled"}
            )
            return
        except NoProgressLoop as exc:
            yield self.resources.runs.emit_run_status(
                run_id,
                "failed",
                error=_error_text(exc),
                raw={"status": "failed", "error": repr(exc)},
            )
            return
        except Exception as exc:
            yield self.resources.runs.emit_run_status(
                run_id,
                "failed",
                error=_error_text(exc),
                raw={"status": "failed", "error": repr(exc)},
            )
            return
        finally:
            self.run_controls.pop(run_id, None)

        if workflow_schema is not None:
            assistant_text = "渠道识别完成，结果已写入 run.result。"
        elif not assistant_text and text_parts:
            assistant_text = "".join(text_parts)
        yield self.resources.runs.emit_run_status(
            run_id,
            "succeeded",
            reply=assistant_text,
            result=result,
            raw={"status": "succeeded", "reply": assistant_text, "result": result},
        )

    def request_cancel(self, run_id: str) -> bool:
        """Cooperatively drain an active run. Returns False if no control exists."""
        control = self.run_controls.get(run_id)
        if control is None:
            return False
        control.request_drain(reason="user_cancel")
        return True


def create_harness(resources: AgentResources) -> HarnessRuntime:
    # Local import to avoid a circular module-load dependency: tools.py imports
    # the skill packages, agent.py imports tools.py.
    from runtime.agent import DeepAgentsBrainFactory
    from runtime.tools import default_tool_catalog

    return HarnessRuntime(
        resources=resources,
        tools=default_tool_catalog(),
        brain_factory=DeepAgentsBrainFactory(),
    )


def _normalize_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "role": message["role"],
            "content": _normalize_content_blocks(message["content"]),
        }
        for message in messages
    ]


def _normalize_content_blocks(blocks: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    normalized_blocks: list[dict[str, str]] = []
    for block in blocks:
        if block["type"] == "artifact":
            normalized_blocks.append(
                {
                    "type": "text",
                    "text": ARTIFACT_REFERENCE_HINT.format(path=block["path"]),
                }
            )
            continue
        normalized_blocks.append({"type": "text", "text": block["text"]})
    return normalized_blocks


def _update_events(data: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    """Turn an `updates`-mode chunk into assistant_message / tool_execution events.

    `updates` carries per-node diffs keyed by node name; the agent node diff
    holds a new-messages list. We emit a fresh assistant_message for terminal
    text and a tool_execution entry for each new tool call.
    """
    if not isinstance(data, dict):
        return
    for node_value in data.values():
        new_messages = node_value.get("messages") if isinstance(node_value, dict) else None
        if not isinstance(new_messages, list):
            continue
        for message in new_messages:
            role = _message_role(message)
            if role not in {"assistant", "ai"}:
                continue
            tool_calls = observability.tool_calls_of(message)
            for tool_call in tool_calls:
                payload = observability.tool_call_payload(message, tool_call)
                if payload is None:
                    continue
                yield "tool_execution", payload
            assistant_payload = observability.assistant_message_payload(message, tool_calls=tool_calls)
            if assistant_payload is not None:
                yield "assistant_message", assistant_payload


def _structured_response(data: Any) -> Any:
    if not isinstance(data, dict):
        return None
    for node_value in data.values():
        if isinstance(node_value, dict) and node_value.get("structured_response") is not None:
            return node_value["structured_response"]
    return None


def _tecan_finalized_response(data: Any) -> TecanOverseasRecognitionResult | None:
    """Read only the dedicated Tecan finalizer ToolMessage, never arbitrary tool text."""
    if not isinstance(data, dict):
        return None
    for node_value in data.values():
        messages = node_value.get("messages") if isinstance(node_value, dict) else None
        if not isinstance(messages, list):
            continue
        for message in messages:
            if _message_role(message) != "tool" or _message_name(message) != FINALIZE_TECAN_RESULT_TOOL:
                continue
            text = _message_content(message)
            try:
                return TecanOverseasRecognitionResult.model_validate_json(text)
            except (TypeError, ValueError):
                continue
    return None


def _message_role(message: Any) -> str | None:
    role = getattr(message, "role", None)
    if isinstance(role, str):
        return role
    message_type = getattr(message, "type", None)
    if isinstance(message_type, str):
        return "assistant" if message_type == "ai" else message_type
    if isinstance(message, dict):
        value = message.get("role") or message.get("type")
        if isinstance(value, str):
            return "assistant" if value == "ai" else value
    return None


def _message_name(message: Any) -> str | None:
    value = message.get("name") if isinstance(message, dict) else getattr(message, "name", None)
    return value if isinstance(value, str) else None


def _message_content(message: Any) -> str:
    value = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__
