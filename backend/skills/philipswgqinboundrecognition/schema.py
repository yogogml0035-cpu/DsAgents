from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


WORKFLOW = "philips_wgq_inbound_recognition"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Shipment(_ContractModel):
    pieces: str | None = Field(alias="件数")
    total_gross_weight: str | None = Field(alias="总毛重")


class OrderHeader(_ContractModel):
    om: str | None = Field(alias="OM")
    dn: str | None = Field(alias="DN")
    po: str | None = Field(alias="PO")
    so: str | None = Field(alias="SO")
    original_waybill_number: str | None = Field(alias="原运单号")
    buyer: str | None = Field(alias="买方")
    seller: str | None = Field(alias="卖方")
    shipper: str | None = Field(alias="发货人")
    consignee: str | None = Field(alias="收货人")
    payment_terms: str | None = Field(alias="付款方式")
    contract_number: str | None = Field(alias="合同号")
    salesperson: str | None = Field(alias="业务员")
    invoice_number: str | None = Field(alias="发票号")
    etd: date | None = Field(alias="ETD")
    port_of_departure: str | None = Field(alias="启运港")
    port_of_arrival: str | None = Field(alias="到货港")


class OrderItem(_ContractModel):
    so_item: str | None = Field(alias="SO_ITEM")
    product_id: str | None = Field(alias="12NC")
    new_or_used: str | None = Field(alias="新旧")
    chinese_name: str | None = Field(alias="中文品名")
    specification: str | None = Field(alias="规格型号")
    quantity: str | None = Field(alias="库存数量")
    unit: str | None = Field(alias="单位")
    currency: str | None = Field(alias="币种")
    unit_price: str | None = Field(alias="单价")
    total_price: str | None = Field(alias="总价")
    origin_country: str | None = Field(alias="原产国")
    customs_code: str | None = Field(alias="海关编码")
    declaration_elements: str | None = Field(alias="申报要素")
    legal_quantity_1: str | None = Field(alias="法一数量")
    legal_unit_1: str | None = Field(alias="法一单位")
    legal_quantity_2: str | None = Field(alias="法二数量")
    legal_unit_2: str | None = Field(alias="法二单位")
    gross_weight: str | None = Field(alias="毛重")
    net_weight: str | None = Field(alias="净重")
    business_unit: str | None = Field(alias="BU")
    pre_or_post_sales: str | None = Field(alias="售前/售后")


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
