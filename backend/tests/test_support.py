from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from skills.philips_wgq_inbound_recognition import WAG_WORKFLOW
from skills.tecan_import import DK_WORKFLOW
from skills.tecan_import.scripts.tools import FINALIZE_TECAN_RESULT_TOOL


class StreamControl:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()


class FakeBrain:
    """Mock langgraph brain. Yields a scripted v2 stream with stream_mode
    ["messages", "custom", "updates"] and subgraphs=True, exercising the new
    event pipeline: model_usage, thinking, text_delta, tool_execution,
    tool_progress, assistant_message."""

    def __init__(
        self,
        threads: dict[str, list[str]],
        received_payloads: list[list[dict[str, Any]]],
        control: StreamControl | None = None,
        workflow: str | None = None,
    ) -> None:
        self.threads = threads
        self.received_payloads = received_payloads
        self.control = control
        self.workflow = workflow

    def stream(self, payload: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: object):
        assert isinstance(payload["messages"], list)
        assert payload["messages"]
        assert kwargs["stream_mode"] == ["messages", "custom", "updates"]
        assert kwargs["version"] == "v2"
        assert kwargs.get("subgraphs") is True
        assert config is not None
        thread_id = config["configurable"]["thread_id"]
        messages = deepcopy(payload["messages"])
        assert all(isinstance(message["content"], list) for message in messages)
        assert all(block["type"] == "text" for message in messages for block in message["content"])
        self.received_payloads.append(messages)
        text = _message_text(messages[-1]["content"])
        history = self.threads.setdefault(thread_id, [])
        history.append(text)
        # 1. thinking chunk (main agent)
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
        # 2. subagent message chunk (usage captured, text filtered out)
        yield {
            "type": "messages",
            "ns": ("task",),
            "data": (
                AIMessageChunk(
                    content="subagent secret",
                    usage_metadata={
                        "input_tokens": 200,
                        "output_tokens": 40,
                        "total_tokens": 240,
                        "input_token_details": {"cache_read": 50, "cache_creation": 10},
                    },
                ),
                {"langgraph_node": "model", "lc_agent_name": "tecan-extractor-a"},
            ),
        }
        if text == "fail":
            raise RuntimeError("planned failure")
        if text == "hold":
            assert self.control is not None
            self.control.started.set()
            assert self.control.release.wait(timeout=5), "hold run was never released"
        reply = f"echo[{len(history)}]: {text}"
        finalizer_call = self.workflow is None and text == "tecan final"
        tool_call = {
            "id": f"call-{thread_id}-{len(history)}",
            "name": FINALIZE_TECAN_RESULT_TOOL if finalizer_call else "read_file",
            "args": (
                {"result": _tecan_recognition_result()}
                if finalizer_call
                else {"file_path": "/artifacts/uploads/demo.jpg"}
            ),
        }
        # 3. main-agent text delta
        yield {"type": "messages", "ns": (), "data": (AIMessageChunk(content="echo["), {"langgraph_node": "model"})}
        # 4. update chunk carrying the assistant tool-call request -> tool_execution
        yield {
            "type": "updates",
            "ns": (),
            "data": {
                "agent": {
                    "messages": [
                        AIMessage(content="", id=f"assistant-tool-{thread_id}-{len(history)}", tool_calls=[tool_call])
                    ]
                }
            },
        }
        if finalizer_call:
            yield {
                "type": "updates",
                "ns": (),
                "data": {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content=json.dumps(_tecan_recognition_result(), ensure_ascii=False),
                                id=f"tecan-result-{thread_id}-{len(history)}",
                                tool_call_id=tool_call["id"],
                                name=FINALIZE_TECAN_RESULT_TOOL,
                            )
                        ]
                    }
                },
            }
        if self.workflow in {WAG_WORKFLOW, DK_WORKFLOW} and "missing structured" not in text:
            yield {
                "type": "updates",
                "ns": (),
                "data": {
                    "agent": {
                        "structured_response": (
                            _recognition_result(text)
                            if self.workflow == WAG_WORKFLOW
                            else _tecan_recognition_result(text)
                        )
                    }
                },
            }
        # 5. custom chunk: parse_documents progress -> tool_progress
        yield {"type": "custom", "ns": (), "data": {"name": "parse_documents", "status": "started"}}
        # 6. update chunk carrying the tool result (ToolMessage, role=tool -> not mapped)
        yield {
            "type": "updates",
            "ns": (),
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content=[{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}],
                            id=f"tool-result-{thread_id}-{len(history)}",
                            tool_call_id=tool_call["id"],
                            name="read_file",
                        )
                    ]
                }
            },
        }
        # 7. main-agent terminal chunk with usage_metadata -> model_usage + text_delta
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                AIMessageChunk(
                    content=f"{len(history)}]: {text}",
                    usage_metadata={
                        "input_tokens": 1000,
                        "output_tokens": 300,
                        "total_tokens": 1300,
                        "input_token_details": {
                            "cache_read": 600,
                            "cache_creation": 200,
                            "ephemeral_5m_input_tokens": 50,
                            "ephemeral_1h_input_tokens": 30,
                        },
                    },
                ),
                {"langgraph_node": "model"},
            ),
        }
        # 8. update chunk carrying the final assistant message -> assistant_message
        yield {
            "type": "updates",
            "ns": (),
            "data": {
                "agent": {
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
                }
            },
        }


class FakeBrainFactory:
    def __init__(self, control: StreamControl | None = None) -> None:
        self.control = control
        self.threads: dict[str, list[str]] = {}
        self.received_payloads: list[list[dict[str, Any]]] = []
        self.created_workflows: list[str | None] = []

    def create(self, **kwargs: object) -> FakeBrain:
        workflow = kwargs.get("workflow")
        assert workflow is None or isinstance(workflow, str)
        self.created_workflows.append(workflow)
        return FakeBrain(self.threads, self.received_payloads, self.control, workflow)


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


def _recognition_result(text: str) -> dict[str, Any]:
    if "input problems" in text:
        return {
            "outcome": "input_problems",
            "data": {
                "header": {
                    "om": None,
                    "dn": None,
                    "po": None,
                    "so": None,
                    "original_waybill_number": None,
                    "buyer": None,
                    "seller": None,
                    "shipper": None,
                    "consignee": None,
                    "payment_terms": None,
                    "contract_number": None,
                    "salesperson": None,
                    "invoice_number": None,
                    "etd": None,
                    "trade_terms": None,
                    "port_of_departure": None,
                    "port_of_arrival": None,
                },
                "items": [],
            },
            "problems": [
                {
                    "source": "batch",
                    "location": "shipment",
                    "issue": "检测到多个真实票次",
                    "action": "拆分批次后重试",
                }
            ],
        }
    return {
        "outcome": "success",
        "data": {
            "header": {
                "om": "OM123",
                "dn": "DN123",
                "po": "PO123",
                "so": "SO123",
                "original_waybill_number": "9198153694",
                "buyer": "buyer",
                "seller": "seller",
                "shipper": "shipper",
                "consignee": "consignee",
                "payment_terms": "NET30",
                "contract_number": "CON123",
                "salesperson": "salesperson",
                "invoice_number": "INV123",
                "etd": "2026-05-25",
                "trade_terms": "FOB",
                "port_of_departure": "Shanghai",
                "port_of_arrival": "Shanghai",
            },
            "items": [
                {
                    "invoice_number": "INV123",
                    "invoice_date": "2026-05-25",
                    "so_item": "10",
                    "product_id": "989000085103",
                    "new_or_used": "新",
                    "chinese_name": "医疗设备部件",
                    "specification": "MODEL-1",
                    "quantity": "2",
                    "unit": "个",
                    "currency": "USD",
                    "unit_price": "10.00",
                    "total_price": "20.00",
                    "trade_terms": "FOB",
                    "origin_country": "美国",
                    "customs_code": "9018909090",
                    "declaration_elements": "用途：医疗",
                    "legal_quantity_1": "2",
                    "legal_unit_1": "个",
                    "legal_quantity_2": "2",
                    "legal_unit_2": "千克",
                    "gross_weight": "1.2",
                    "net_weight": "1",
                    "business_unit": "CT",
                    "pre_or_post_sales": "售前",
                }
            ],
        },
        "problems": [],
    }


def _tecan_recognition_result(text: str = "") -> dict[str, Any]:
    result = {
        "outcome": "success",
        "data": {
            "header": {
                "po": "PO123",
                "dn": "DN123",
                "original_waybill_number": "0012345678",
                "buyer": "buyer",
                "seller": "seller",
                "shipper": "shipper",
                "consignee": "consignee",
                "payment_terms": "NET30",
                "contract_number": "CON123",
                "invoice_number": "INV123",
                "invoice_date": "2026-05-25",
                "trade_terms": "FOB",
                "port_of_departure": "Vienna",
                "port_of_arrival": "Shanghai",
            },
            "items": [
                {
                    "invoice_number": "INV123",
                    "invoice_date": "2026-05-25",
                    "so_item": "10",
                    "product_id": "989000085103",
                    "new_or_used": "新",
                    "chinese_name": "医疗设备部件",
                    "specification": "MODEL-1",
                    "quantity": "2",
                    "unit": "个",
                    "currency": "USD",
                    "unit_price": "10",
                    "total_price": "20",
                    "trade_terms": "FOB",
                    "origin_country": "美国",
                    "customs_code": "9018909090",
                    "declaration_elements": "用途：医疗",
                    "legal_quantity_1": "2",
                    "legal_unit_1": "个",
                    "legal_quantity_2": "2",
                    "legal_unit_2": "千克",
                    "gross_weight": "1.2",
                    "net_weight": "1",
                    "business_unit": "CT",
                    "pre_or_post_sales": "售前",
                }
            ],
        },
        "problems": [],
    }
    if "input problems" in text:
        result.update(
            outcome="input_problems",
            data={"header": {key: None for key in result["data"]["header"]}, "items": []},
            problems=[
                {
                    "source": "batch",
                    "location": "shipment",
                    "issue": "检测到多个真实票次",
                    "action": "拆分批次后重试",
                }
            ],
        )
    return result
