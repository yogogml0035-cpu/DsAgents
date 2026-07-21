from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from integrations.artifacts import read_json_artifact
from skills.tecanimport.schema import TecanOverseasRecognitionResult
from skills.tecanimport.scripts.tools import (
    finalize_tecan_overseas_recognition,
    inspect_supply_chain_workbooks,
)


def run() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        artifacts = (Path(tmp) / "artifacts").resolve()
        uploads = artifacts / "uploads"
        uploads.mkdir(parents=True)
        workbook_path = uploads / "invoice.xlsx"
        _workbook_fixture(workbook_path)

        with patch("integrations.artifacts.artifacts_root", return_value=artifacts):
            inspected = inspect_supply_chain_workbooks(
                ["/artifacts/uploads/invoice.xlsx", "/artifacts/uploads/not-xlsx.pdf"]
            )
            assert len(inspected["workbooks"]) == 1
            assert len(inspected["problems"]) == 1
            content = read_json_artifact(inspected["workbooks"][0]["result_path"])
            assert content["source_artifact"] == "/artifacts/uploads/invoice.xlsx"
            assert content["sheets"][0]["name"] == "Invoice"
            assert content["sheets"][0]["rows"] == [["PO", "000123"], ["Amount", 12.5]]

    payload = _result()
    finalized = json.loads(finalize_tecan_overseas_recognition(payload))
    assert finalized["outcome"] == "success"
    assert finalized["data"]["items"][0]["quantity"] == "2"
    assert finalized["data"]["items"][0]["currency"] == "USD"
    assert len(finalized["data"]["items"][0]) == 24
    finalizer_schema = StructuredTool.from_function(finalize_tecan_overseas_recognition).args_schema.model_json_schema()
    assert finalizer_schema["properties"]["result"]["$ref"].endswith("TecanOverseasRecognitionResult")
    assert "OrderItem" in finalizer_schema["$defs"]

    cleaned = copy.deepcopy(payload)
    cleaned["data"]["header"]["buyer"] = "  "
    cleaned["data"]["items"][0]["chinese_name"] = " "
    cleaned["outcome"] = "partial_success"
    cleaned["problems"] = [_problem("data.header.buyer", "存在待补充字段")]
    validated_cleaned = TecanOverseasRecognitionResult.model_validate(cleaned)
    assert validated_cleaned.data.header.buyer is None
    assert validated_cleaned.data.items[0].chinese_name is None
    assert "data.items[0].chinese_name" in validated_cleaned.problems[-1].issue

    success_with_optional_missing = copy.deepcopy(payload)
    success_with_optional_missing["data"]["items"][0]["chinese_name"] = None
    downgraded = TecanOverseasRecognitionResult.model_validate(success_with_optional_missing)
    assert downgraded.outcome == "partial_success"
    assert "data.items[0].chinese_name" in downgraded.problems[-1].issue

    malformed_number = copy.deepcopy(payload)
    malformed_number["data"]["items"][0]["quantity"] = "1,2,3"
    _assert_invalid(malformed_number)

    partial = copy.deepcopy(payload)
    partial["outcome"] = "partial_success"
    partial["data"]["items"][0]["chinese_name"] = None
    partial["problems"] = [_problem("items[0].chinese_name", "缺少中文品名")]
    assert TecanOverseasRecognitionResult.model_validate(partial).outcome == "partial_success"

    fully_confirmed_partial = copy.deepcopy(payload)
    fully_confirmed_partial["outcome"] = "partial_success"
    fully_confirmed_partial["problems"] = [_problem("attachments", "无关附件已忽略")]
    assert TecanOverseasRecognitionResult.model_validate(fully_confirmed_partial).outcome == "success"

    partial_without_core_fact = copy.deepcopy(partial)
    partial_without_core_fact["data"]["items"][0]["total_price"] = None
    _assert_invalid(partial_without_core_fact)

    input_problems = _result()
    input_problems.update(
        outcome="input_problems",
        data={"header": {key: None for key in input_problems["data"]["header"]}, "items": []},
        problems=[_problem("data.items", "无法安全确认商品行")],
    )
    assert TecanOverseasRecognitionResult.model_validate(input_problems).data.items == []

    invalid = copy.deepcopy(input_problems)
    invalid["problems"] = []
    try:
        TecanOverseasRecognitionResult.model_validate(invalid)
    except ValidationError:
        pass
    else:
        raise AssertionError("input_problems without problems was accepted")


def _assert_invalid(payload: dict[str, object]) -> None:
    try:
        TecanOverseasRecognitionResult.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("invalid Tecan result was accepted")


def _workbook_fixture(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Invoice"
    sheet.append(["PO", "000123"])
    sheet.append(["Amount", 12.5])
    workbook.save(path)


def _problem(location: str, issue: str) -> dict[str, str]:
    return {
        "source": "invoice.xlsx",
        "location": location,
        "issue": issue,
        "action": "补充单据后重试",
    }


def _result() -> dict[str, object]:
    return {
        "outcome": "success",
        "data": {
            "header": {
                "po": "000123",
                "dn": "DN-1",
                "original_waybill_number": "0012345678",
                "buyer": "Tecan China",
                "seller": "Tecan Austria",
                "shipper": "Tecan Austria",
                "consignee": "Tecan China",
                "payment_terms": "NET30",
                "contract_number": "CT-1",
                "invoice_number": "INV-1",
                "invoice_date": "2026-07-22",
                "trade_terms": "fob",
                "port_of_departure": "Vienna",
                "port_of_arrival": "Shanghai",
            },
            "items": [
                {
                    "invoice_number": "INV-1",
                    "invoice_date": "2026-07-22",
                    "so_item": "10",
                    "product_id": "000012345678",
                    "new_or_used": "新",
                    "chinese_name": "设备",
                    "specification": "MODEL-1",
                    "quantity": "2.00",
                    "unit": "EA",
                    "currency": "usd",
                    "unit_price": "6.25",
                    "total_price": "12.50",
                    "trade_terms": "fob",
                    "origin_country": "AT",
                    "customs_code": "9018909090",
                    "declaration_elements": "用途：诊断",
                    "legal_quantity_1": "2",
                    "legal_unit_1": "个",
                    "legal_quantity_2": "2",
                    "legal_unit_2": "千克",
                    "gross_weight": "3.5",
                    "net_weight": "3",
                    "business_unit": "LSP",
                    "pre_or_post_sales": "售前",
                }
            ],
        },
        "problems": [],
    }


if __name__ == "__main__":
    run()
