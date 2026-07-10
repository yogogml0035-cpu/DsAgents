from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from dotenv import load_dotenv
from langchain.agents.middleware import AgentMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from hands import Hands, ToolStatusHands
from resources import AgentResources
from run_ledger import RunEvent
from subagents import workflow_subagents
from tools import ToolCatalog, ToolHandler, default_tool_catalog


load_dotenv(Path(__file__).with_name(".env"))


DEFAULT_SYSTEM_PROMPT = (
    "You are a document-processing agent. When a user provides a local "
    "/artifacts/ path, use `read_file` for images or media inspection and "
    "`parse_documents` for documents when structured extraction is needed. "
    "Use a business Skill only when the user clearly requests that business "
    "outcome; a filename or an ordinary PDF extraction request is not enough. "
    "Business tools accept only explicit artifact paths and never search for "
    "a recent file or prior task. "
    "Persist important notes under /memories/ and write large outputs under "
    "/artifacts/."
)

MAIN_AGENT_NAME = "dsagents-main"
SKILLS_SOURCE = "/skills/"

# deepagents 0.6.12 exposes profile registration, not a create_deep_agent
# harness_profile argument. Disable its auto-added fifth subagent at the
# provider profile and keep the four explicit workflow extractors below.
register_harness_profile(
    "anthropic",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
)

ARTIFACT_REFERENCE_HINT = (
    "Uploaded artifact: {path}. Use read_file for images/media or "
    "parse_documents for documents when needed."
)


class Brain(Protocol):
    def stream(
        self,
        payload: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any] | Any]: ...


class BrainFactory(Protocol):
    def create(
        self,
        *,
        resources: AgentResources,
        middleware: Sequence[AgentMiddleware],
        tools: Sequence[ToolHandler],
    ) -> Brain: ...


class DeepAgentsBrainFactory:
    def __init__(self, model: str | BaseChatModel | None = None, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        if model is None:
            # ponytail: keep existing MINIMAX_* keys and adapt them onto LangChain's Anthropic client.
            model = init_chat_model(
                f"anthropic:{os.getenv('MINIMAX_MODEL')}",
                api_key=os.getenv("MINIMAX_API_KEY"),
                base_url=os.getenv("MINIMAX_BASE_URL"),
                thinking={"type": "adaptive"},
            )
        self.model = model
        self.system_prompt = system_prompt

    def create(
        self,
        *,
        resources: AgentResources,
        middleware: Sequence[AgentMiddleware],
        tools: Sequence[ToolHandler],
    ) -> Brain:
        return create_deep_agent(
            model=self.model,
            tools=tools,
            system_prompt=self.system_prompt,
            middleware=list(middleware),
            backend=resources.backend,
            checkpointer=resources.checkpointer,
            store=resources.store,
            skills=[SKILLS_SOURCE],
            subagents=workflow_subagents(),
            permissions=[
                FilesystemPermission(
                    operations=["write"],
                    paths=["/skills/**"],
                    mode="deny",
                )
            ],
            name=MAIN_AGENT_NAME,
        )


class HarnessRuntime:
    def __init__(
        self,
        *,
        resources: AgentResources,
        hands: Hands,
        tools: ToolCatalog,
        brain_factory: BrainFactory,
    ) -> None:
        self.resources = resources
        self.hands = hands
        self.tools = tools
        self.brain_factory = brain_factory

    def execute_run(self, messages: Sequence[dict[str, Any]], session_id: str, run_id: str) -> Iterator[RunEvent]:
        assistant_text = ""
        text_parts: list[str] = []
        seen_tool_call_ids: set[str] = set()
        seen_tool_result_ids: set[str] = set()
        seen_assistant_message_ids: set[str] = set()
        tool_call_names: dict[str, str] = {}
        normalized_messages = _normalize_messages(messages)
        yield self.resources.runs.emit_run_status(run_id, "running")
        try:
            brain = self.brain_factory.create(
                resources=self.resources,
                middleware=self.hands.middleware(),
                tools=self.tools.as_list(),
            )
            for chunk in brain.stream(
                {"messages": normalized_messages},
                config={"configurable": {"thread_id": session_id}},
                stream_mode=["messages", "custom", "values"],
                version="v2",
            ):
                if not isinstance(chunk, dict):
                    continue
                if chunk["type"] == "messages":
                    if _is_subagent_message(chunk["data"]):
                        continue
                    thinking = _thinking_delta(chunk["data"])
                    if thinking:
                        yield self.resources.runs.emit_run_event(
                            run_id,
                            "thinking",
                            {"content": thinking},
                            raw=chunk,
                        )
                    text = _message_delta(chunk["data"])
                    if text:
                        text_parts.append(text)
                        yield self.resources.runs.emit_run_event(
                            run_id,
                            "text_delta",
                            {"content": text},
                            raw=chunk,
                        )
                elif chunk["type"] == "custom":
                    yield self.resources.runs.emit_run_event(
                        run_id,
                        "tool_status",
                        _stream_payload(chunk["data"]),
                        raw=chunk,
                    )
                elif chunk["type"] == "values":
                    snapshot_text = _assistant_text(chunk["data"])
                    if snapshot_text:
                        assistant_text = snapshot_text
                    for event_type, payload in _snapshot_events(
                        chunk["data"],
                        seen_tool_call_ids=seen_tool_call_ids,
                        seen_tool_result_ids=seen_tool_result_ids,
                        seen_assistant_message_ids=seen_assistant_message_ids,
                        tool_call_names=tool_call_names,
                    ):
                        if event_type == "assistant_message" and payload.get("text"):
                            assistant_text = payload["text"]
                        yield self.resources.runs.emit_run_event(
                            run_id,
                            event_type,
                            payload,
                            raw=chunk,
                        )
        except Exception as exc:
            yield self.resources.runs.emit_run_status(
                run_id,
                "failed",
                error=_error_text(exc),
                raw={"status": "failed", "error": repr(exc)},
            )
            return

        if not assistant_text and text_parts:
            assistant_text = "".join(text_parts)
        yield self.resources.runs.emit_run_status(
            run_id,
            "succeeded",
            reply=assistant_text,
            raw={"status": "succeeded", "reply": assistant_text},
        )


def create_harness(resources: AgentResources) -> HarnessRuntime:
    return HarnessRuntime(
        resources=resources,
        hands=ToolStatusHands(),
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


def _assistant_content(result: dict[str, Any]) -> Any:
    messages = result.get("messages") or []
    for message in reversed(messages):
        if _message_role(message) in {"assistant", "ai"}:
            return _message_content(message)
    if messages:
        return _message_content(messages[-1])
    return None


def _assistant_text(result: dict[str, Any]) -> str:
    return _content_text(_assistant_content(result))


def _snapshot_events(
    result: dict[str, Any],
    *,
    seen_tool_call_ids: set[str],
    seen_tool_result_ids: set[str],
    seen_assistant_message_ids: set[str],
    tool_call_names: dict[str, str],
) -> Iterator[tuple[str, dict[str, Any]]]:
    messages = result.get("messages") or []
    for message in messages:
        role = _message_role(message)
        if role in {"assistant", "ai"}:
            tool_calls = _message_tool_calls(message)
            for tool_call in tool_calls:
                payload = _tool_call_payload(message, tool_call)
                if payload is None:
                    continue
                tool_call_id = payload["tool_call_id"]
                if tool_call_id in seen_tool_call_ids:
                    continue
                seen_tool_call_ids.add(tool_call_id)
                tool_call_names[tool_call_id] = payload["name"]
                yield "tool_call", payload
            payload = _assistant_message_payload(message, tool_calls=tool_calls)
            if payload is None:
                continue
            message_id = payload["message_id"]
            if message_id in seen_assistant_message_ids:
                continue
            seen_assistant_message_ids.add(message_id)
            yield "assistant_message", payload
            continue
        if role != "tool":
            continue
        payload = _tool_result_payload(message, tool_call_names=tool_call_names)
        if payload is None:
            continue
        result_id = payload["tool_call_id"]
        if result_id in seen_tool_result_ids:
            continue
        seen_tool_result_ids.add(result_id)
        yield "tool_result", payload


def _message_delta(data: Any) -> str:
    message = data[0] if isinstance(data, tuple) and data else data
    if isinstance(message, dict):
        event = message.get("event")
        if event and not str(event).endswith("delta"):
            return ""
        return _content_text(message.get("delta")) or _content_text(message.get("content")) or _content_text(message)
    return _content_text(_message_content(message))


def _is_subagent_message(data: Any) -> bool:
    if not isinstance(data, tuple) or len(data) < 2 or not isinstance(data[1], dict):
        return False
    agent_name = data[1].get("lc_agent_name")
    return isinstance(agent_name, str) and agent_name not in {"", MAIN_AGENT_NAME}


def _thinking_delta(data: Any) -> str:
    message = data[0] if isinstance(data, tuple) and data else data
    if isinstance(message, dict):
        event = message.get("event")
        if event and not str(event).endswith("delta"):
            return ""
        return (
            _thinking_text(message.get("delta"))
            or _thinking_text(message.get("content"))
            or _thinking_text(message)
        )
    return _thinking_text(getattr(message, "content_blocks", None)) or _thinking_text(
        _message_content(message)
    )


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


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", message)


def _message_id(message: Any) -> str | None:
    if isinstance(message, dict):
        value = message.get("id")
    else:
        value = getattr(message, "id", None)
    return value if isinstance(value, str) and value else None


def _message_tool_calls(message: Any) -> list[dict[str, Any]]:
    if isinstance(message, dict):
        tool_calls = message.get("tool_calls")
    else:
        tool_calls = getattr(message, "tool_calls", None)
    if not isinstance(tool_calls, list):
        return []
    return [tool_call for tool_call in tool_calls if isinstance(tool_call, dict)]


def _tool_call_payload(message: Any, tool_call: dict[str, Any]) -> dict[str, Any] | None:
    message_id = _message_id(message)
    tool_call_id = tool_call.get("id")
    name = tool_call.get("name")
    if not isinstance(message_id, str) or not isinstance(tool_call_id, str) or not isinstance(name, str):
        return None
    args = tool_call.get("args")
    return {
        "message_id": message_id,
        "tool_call_id": tool_call_id,
        "name": name,
        "args": args if isinstance(args, dict) else {},
    }


def _assistant_message_payload(message: Any, *, tool_calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    if tool_calls:
        return None
    message_id = _message_id(message)
    content = _message_content(message)
    text = _content_text(content)
    if not isinstance(message_id, str) or not text:
        return None
    payload: dict[str, Any] = {"message_id": message_id}
    thinking_block = _last_thinking_block(content)
    if thinking_block:
        thinking = _content_text(thinking_block.get("thinking")) or _content_text(
            thinking_block.get("text")
        )
        if thinking:
            payload["thinking"] = thinking
    payload["text"] = text
    return payload


def _last_thinking_block(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict):
        if content.get("type") == "thinking":
            return content
        return _last_thinking_block(content.get("delta")) or _last_thinking_block(
            content.get("content")
        )
    if isinstance(content, list):
        for item in reversed(content):
            block = _last_thinking_block(item)
            if block is not None:
                return block
    return None


def _tool_result_payload(message: Any, *, tool_call_names: dict[str, str]) -> dict[str, Any] | None:
    tool_call_id = _tool_call_id(message)
    if not tool_call_id:
        return None
    message_id = _message_id(message) or tool_call_id
    name = _tool_message_name(message) or tool_call_names.get(tool_call_id)
    return {
        "message_id": message_id,
        "tool_call_id": tool_call_id,
        "name": name,
        "status": _tool_message_status(message),
        **_tool_result_summary(message),
    }


def _tool_call_id(message: Any) -> str | None:
    if isinstance(message, dict):
        value = message.get("tool_call_id")
    else:
        value = getattr(message, "tool_call_id", None)
    return value if isinstance(value, str) and value else None


def _tool_message_name(message: Any) -> str | None:
    if isinstance(message, dict):
        value = message.get("name")
    else:
        value = getattr(message, "name", None)
    return value if isinstance(value, str) and value else None


def _tool_message_status(message: Any) -> str:
    if isinstance(message, dict):
        value = message.get("status")
    else:
        value = getattr(message, "status", None)
    return value if isinstance(value, str) and value else "success"


def _tool_message_artifact(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("artifact")
    return getattr(message, "artifact", None)


def _tool_result_summary(message: Any) -> dict[str, Any]:
    content = _message_content(message)
    media = _tool_result_media(content) or _tool_result_media(_tool_message_artifact(message))
    if media is not None:
        return {
            "content_type": media[0],
            "mime_type": media[1],
            "text": None,
            "preview": None,
        }
    text = _content_text(content).strip()
    if text:
        if len(text) <= 500:
            return {
                "content_type": "text",
                "mime_type": None,
                "text": text,
                "preview": None,
            }
        return {
            "content_type": "text",
            "mime_type": None,
            "text": None,
            "preview": f"{text[:197]}...",
        }
    return {
        "content_type": "unknown",
        "mime_type": None,
        "text": None,
        "preview": None,
    }


def _tool_result_media(value: Any) -> tuple[str, str | None] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _media_from_string(value)
    if isinstance(value, dict):
        mime_type = value.get("mime_type")
        if isinstance(mime_type, str) and mime_type:
            return _media_kind(mime_type), mime_type
        block_type = value.get("type")
        if block_type == "image_url":
            url = value.get("image_url")
            if isinstance(url, dict):
                return _tool_result_media(url.get("url"))
        for item in value.values():
            media = _tool_result_media(item)
            if media is not None:
                return media
        return None
    if isinstance(value, list):
        for item in value:
            media = _tool_result_media(item)
            if media is not None:
                return media
    return None


def _media_from_string(value: str) -> tuple[str, str | None] | None:
    if not value.startswith("data:"):
        return None
    mime_type = value[5:].split(";", 1)[0]
    if not mime_type:
        return "file", None
    return _media_kind(mime_type), mime_type


def _media_kind(mime_type: str) -> str:
    family = mime_type.split("/", 1)[0]
    if family in {"image", "audio", "video"}:
        return family
    return "file"


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return (
            _content_text(content.get("text"))
            or _content_text(content.get("delta"))
            or _content_text(content.get("content"))
        )
    if isinstance(content, list):
        return "".join(_content_text(item) for item in content)
    return ""


def _thinking_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, dict):
        block_type = content.get("type")
        if block_type == "thinking":
            return _content_text(content.get("thinking")) or _content_text(
                content.get("text")
            )
        if block_type == "reasoning":
            return _content_text(content.get("reasoning")) or _content_text(
                content.get("text")
            )
        if block_type == "non_standard":
            return _thinking_text(content.get("value"))
        return _thinking_text(content.get("delta")) or _thinking_text(content.get("content"))
    if isinstance(content, list):
        return "".join(_thinking_text(item) for item in content)
    return ""


def _stream_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"value": value}


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__
