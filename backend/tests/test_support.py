from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage


class StreamControl:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()


class FakeBrain:
    def __init__(
        self,
        threads: dict[str, list[str]],
        received_payloads: list[list[dict[str, Any]]],
        control: StreamControl | None = None,
    ) -> None:
        self.threads = threads
        self.received_payloads = received_payloads
        self.control = control

    def stream(self, payload: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: object):
        assert isinstance(payload["messages"], list)
        assert payload["messages"]
        assert kwargs["stream_mode"] == ["messages", "custom", "values"]
        assert kwargs["version"] == "v2"
        assert config is not None
        thread_id = config["configurable"]["thread_id"]
        messages = deepcopy(payload["messages"])
        assert all(isinstance(message["content"], list) for message in messages)
        assert all(block["type"] == "text" for message in messages for block in message["content"])
        self.received_payloads.append(messages)
        text = _message_text(messages[-1]["content"])
        history = self.threads.setdefault(thread_id, [])
        history.append(text)
        yield {
            "type": "values",
            "ns": (),
            "data": {"messages": messages},
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
        tool_call = {
            "id": f"call-{thread_id}-{len(history)}",
            "name": "read_file",
            "args": {"file_path": "/artifacts/uploads/demo.jpg"},
        }
        yield {"type": "messages", "ns": (), "data": (AIMessageChunk(content="echo["), {"langgraph_node": "model"})}
        yield {
            "type": "values",
            "ns": (),
            "data": {
                "messages": [
                    AIMessage(content="", id=f"assistant-tool-{thread_id}-{len(history)}", tool_calls=[tool_call])
                ]
            },
            "interrupts": (),
        }
        yield {"type": "custom", "ns": (), "data": {"name": "parse_document", "status": "started"}}
        yield {
            "type": "values",
            "ns": (),
            "data": {
                "messages": [
                    AIMessage(content="", id=f"assistant-tool-{thread_id}-{len(history)}", tool_calls=[tool_call]),
                    ToolMessage(
                        content=[{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}],
                        id=f"tool-result-{thread_id}-{len(history)}",
                        tool_call_id=tool_call["id"],
                        name="read_file",
                    ),
                ]
            },
            "interrupts": (),
        }
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
                        id=f"assistant-final-{thread_id}-{len(history)}",
                        response_metadata={"model_provider": "anthropic"},
                    )
                ]
            },
            "interrupts": (),
        }


class FakeBrainFactory:
    def __init__(self, control: StreamControl | None = None) -> None:
        self.control = control
        self.threads: dict[str, list[str]] = {}
        self.received_payloads: list[list[dict[str, Any]]] = []

    def create(self, **_: object) -> FakeBrain:
        return FakeBrain(self.threads, self.received_payloads, self.control)


def text_block(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def artifact_block(path: str) -> dict[str, str]:
    return {"type": "artifact", "path": path}


def user_message(*blocks: dict[str, str]) -> dict[str, Any]:
    return {"role": "user", "content": list(blocks)}


def messages_json(messages: list[dict[str, Any]]) -> str:
    return json.dumps(messages, ensure_ascii=False)


def wait_for_run(client: TestClient, run_id: str, expected_status: str) -> dict[str, object]:
    deadline = time.time() + 5
    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()["run"]
        if payload["status"] == expected_status:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached {expected_status}")


def _message_text(content: list[dict[str, str]]) -> str:
    return "".join(block["text"] for block in content if block["type"] == "text")
