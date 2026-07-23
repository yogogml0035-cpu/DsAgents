"""渠道供应链抽取共用的最终 JSON 合同。"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


_NUMERIC_FIELDS = (
    "quantity",
    "unit_price",
    "total_price",
    "legal_quantity_1",
    "legal_quantity_2",
    "gross_weight",
    "net_weight",
)
_DECIMAL_LITERAL = re.compile(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?")
_CORE_ITEM_FIELDS = ("product_id", "quantity", "unit", "currency", "total_price")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderItem(ContractModel):
    """Philips 与 Tecan 共用的 24 字段商品行。"""

    invoice_number: str | None
    invoice_date: date | None
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
    trade_terms: str | None
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

    @field_validator("*", mode="before")
    @classmethod
    def normalize_document_text(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator(*_NUMERIC_FIELDS, mode="before")
    @classmethod
    def normalize_number(cls, value: Any) -> str | None:
        if value is None:
            return None
        raw_value = str(value).strip()
        if not raw_value:
            return None
        if not _DECIMAL_LITERAL.fullmatch(raw_value):
            raise ValueError("must be a non-scientific decimal string")
        try:
            number = Decimal(raw_value.replace(",", ""))
        except (InvalidOperation, ValueError):
            raise ValueError("must be a decimal string") from None
        if not number.is_finite():
            raise ValueError("must be finite")
        normalized = format(number, "f").rstrip("0").rstrip(".")
        return normalized or "0"

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> str | None:
        if value is None:
            return None
        currency = str(value).strip().upper()
        if not currency:
            return None
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("must be an ISO three-letter currency")
        return currency

    @field_validator("trade_terms", mode="before")
    @classmethod
    def normalize_trade_terms(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        return normalized or None

    @field_validator("new_or_used", mode="before")
    @classmethod
    def normalize_new_or_used(cls, value: Any) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        if not value:
            return None
        if value not in {"新", "旧"}:
            raise ValueError("must be 新 or 旧")
        return value

    @field_validator("pre_or_post_sales", mode="before")
    @classmethod
    def normalize_sales_stage(cls, value: Any) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        if not value:
            return None
        if value not in {"售前", "售后"}:
            raise ValueError("must be 售前 or 售后")
        return value


class RecognitionProblem(ContractModel):
    source: str
    location: str
    issue: str
    action: str

    @field_validator("*", mode="before")
    @classmethod
    def normalize_text(cls, value: Any) -> str:
        if value is None:
            raise ValueError("must not be empty")
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("must not be empty")
        return normalized


def validate_channel_outcome(result: Any) -> Any:
    """Apply the shared terminal semantics without inventing business values."""
    if result.outcome != "input_problems" and not result.data.items:
        raise ValueError("success and partial_success require at least one item")
    if result.outcome == "input_problems" and not result.problems:
        raise ValueError("input_problems requires at least one problem")

    if result.outcome == "success":
        missing = _missing_paths(result.data)
        if missing:
            result.outcome = "partial_success"
            _append_unlisted_missing_problem(result, missing)
    if result.outcome == "partial_success":
        missing = _missing_paths(result.data)
        if not missing:
            result.outcome = "success"
            return result
        if not result.problems:
            raise ValueError("partial_success requires at least one problem")
        core_missing = _missing_core_paths(result.data)
        if core_missing:
            raise ValueError(
                "partial_success requires confirmed core facts; "
                f"use input_problems for: {', '.join(core_missing)}"
            )
        _append_unlisted_missing_problem(result, missing)
    return result


def _missing_paths(value: Any, path: str = "data") -> list[str]:
    if value is None:
        return [path]
    if isinstance(value, BaseModel):
        return [
            missing
            for name in type(value).model_fields
            for missing in _missing_paths(getattr(value, name), f"{path}.{name}")
        ]
    if isinstance(value, list):
        return [
            missing
            for index, item in enumerate(value)
            for missing in _missing_paths(item, f"{path}[{index}]")
        ]
    return []


def _missing_core_paths(data: Any) -> list[str]:
    header = data.header
    missing: list[str] = []
    if not any((getattr(header, "invoice_number", None), getattr(header, "original_waybill_number", None))):
        missing.append("data.header.ticket_identity")
    for index, item in enumerate(data.items):
        missing.extend(
            f"data.items[{index}].{field}"
            for field in _CORE_ITEM_FIELDS
            if getattr(item, field) is None
        )
    return missing


def _append_unlisted_missing_problem(result: Any, missing: list[str]) -> None:
    """Make every nullable partial field discoverable without duplicating agent notes."""
    recorded = "\n".join(
        f"{problem.location}\n{problem.issue}" for problem in result.problems
    )
    unlisted = [path for path in missing if path not in recorded]
    if not unlisted:
        return
    result.problems.append(
        RecognitionProblem(
            source="result",
            location="data",
            issue=f"未解决缺失字段：{', '.join(unlisted)}",
            action="补充单据或主数据后重试",
        )
    )
