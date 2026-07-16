from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


WORKFLOW = "philips_wgq_inbound_recognition"


class _ContractModel(BaseModel):
    """English-key API/tool contract. OMS maps these keys to form labels separately."""

    model_config = ConfigDict(extra="forbid")


class Shipment(_ContractModel):
    pieces: str | None
    total_gross_weight: str | None


class OrderHeader(_ContractModel):
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
    port_of_departure: str | None
    port_of_arrival: str | None


class OrderItem(_ContractModel):
    so_item: str | None
    product_id: str | None
    new_or_used: str | None
    chinese_name: str | None
    specification: str | None
    quantity: str | None
    unit: str | None
    currency: str | None
    unit_price: str | None
    total_price: str | None
    origin_country: str | None
    customs_code: str | None
    declaration_elements: str | None
    legal_quantity_1: str | None
    legal_unit_1: str | None
    legal_quantity_2: str | None
    legal_unit_2: str | None
    gross_weight: str | None
    net_weight: str | None
    business_unit: str | None
    pre_or_post_sales: str | None


class RecognitionData(_ContractModel):
    shipment: Shipment
    header: OrderHeader
    items: list[OrderItem] = Field(min_length=1)


class RecognitionProblem(_ContractModel):
    source: str
    location: str
    issue: str
    action: str


class PhilipsWgqRecognitionResult(_ContractModel):
    outcome: Literal["success", "partial_success", "input_problems"]
    data: RecognitionData | None
    problems: list[RecognitionProblem]

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.outcome == "input_problems":
            if self.data is not None or not self.problems:
                raise ValueError("input_problems requires data=null and at least one problem")
        elif self.data is None:
            raise ValueError(f"{self.outcome} requires data")
        if self.outcome == "success" and self.problems:
            raise ValueError("success requires an empty problems list")
        if self.outcome == "partial_success" and not self.problems:
            raise ValueError("partial_success requires at least one problem")
        return self
