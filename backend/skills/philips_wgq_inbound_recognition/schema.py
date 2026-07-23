from __future__ import annotations

from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, field_validator, model_validator

from skills.channel_contract import (
    ContractModel,
    OrderItem,
    RecognitionProblem,
    validate_channel_outcome,
)


WAG_WORKFLOW = "WGQ"


class OrderHeader(ContractModel):
    """飞利浦外高桥票次抬头字段。"""

    om: str | None
    dn: str | None
    po: str | None
    so: str | None
    original_waybill_number: str | None
    buyer: str | None
    seller: str | None
    shipper: str | None
    consignee: str | None
    payment_terms: str | None
    contract_number: str | None
    salesperson: str | None
    invoice_number: str | None
    etd: date | None
    trade_terms: str | None
    port_of_departure: str | None
    port_of_arrival: str | None

    @field_validator("*", mode="before")
    @classmethod
    def normalize_document_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("trade_terms", mode="before")
    @classmethod
    def normalize_trade_terms(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        return normalized or None


class RecognitionData(ContractModel):
    header: OrderHeader
    items: list[OrderItem]


class PhilipsWgqRecognitionResult(ContractModel):
    """飞利浦外高桥进境识别的最终结构化结果。"""

    outcome: Literal["success", "partial_success", "input_problems"]
    data: RecognitionData
    problems: list[RecognitionProblem]

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.outcome == "partial_success" and _is_runtime_recovery_skeleton(self):
            return self
        return validate_channel_outcome(self)


def _is_runtime_recovery_skeleton(result: PhilipsWgqRecognitionResult) -> bool:
    return (
        any(
            problem.source == "runtime"
            and problem.location == "structured_response"
            and problem.issue
            in {
                "minimal recovery shape",
                "model kept submitting empty data shell after recovery retries",
            }
            for problem in result.problems
        )
        and _all_null(result.data)
    )


def _all_null(value: Any) -> bool:
    if isinstance(value, BaseModel):
        return all(_all_null(getattr(value, name)) for name in type(value).model_fields)
    if isinstance(value, list):
        return bool(value) and all(_all_null(item) for item in value)
    return value is None


__all__ = [
    "WAG_WORKFLOW",
    "OrderHeader",
    "OrderItem",
    "RecognitionData",
    "RecognitionProblem",
    "PhilipsWgqRecognitionResult",
]
