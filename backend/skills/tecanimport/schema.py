from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import field_validator, model_validator

from skills.channel_contract import (
    ContractModel,
    OrderItem,
    RecognitionProblem,
    validate_channel_outcome,
)


class TecanHeader(ContractModel):
    """Tecan 境外业务票次抬头字段。"""

    po: str | None
    dn: str | None
    original_waybill_number: str | None
    buyer: str | None
    seller: str | None
    shipper: str | None
    consignee: str | None
    payment_terms: str | None
    contract_number: str | None
    invoice_number: str | None
    invoice_date: date | None
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


class TecanRecognitionData(ContractModel):
    header: TecanHeader
    items: list[OrderItem]


class TecanOverseasRecognitionResult(ContractModel):
    outcome: Literal["success", "partial_success", "input_problems"]
    data: TecanRecognitionData
    problems: list[RecognitionProblem]

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        return validate_channel_outcome(self)


__all__ = ["TecanHeader", "TecanRecognitionData", "TecanOverseasRecognitionResult"]
