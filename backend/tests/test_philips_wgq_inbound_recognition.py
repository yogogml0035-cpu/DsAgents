from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook
from pydantic import ValidationError

from skills.philips_wgq_inbound_recognition.schema import PhilipsWgqRecognitionResult
from skills.philips_wgq_inbound_recognition.scripts.tools import (
    lookup_philips_wgq_master_data,
    normalize_product_id,
)
from tests.test_support import _recognition_result


PRODUCT_ID = "989000085103"
NO_VALID_TRACKING_ID = "453561616354"


def run() -> None:
    _check_result_contract()
    assert normalize_product_id("109890-000-85103") == PRODUCT_ID
    assert normalize_product_id(989000085103.0) == PRODUCT_ID
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        artifacts = (Path(tmp) / "artifacts").resolve()
        tracking = artifacts / "uploads" / "tracking.xlsx"
        tracking.parent.mkdir(parents=True)
        _tracking_fixture(tracking)
        with patch("integrations.artifacts.artifacts_root", return_value=artifacts):
            _check_tracking_and_oracle()
            _check_strict_tracking_no_fallback()
            _check_oracle_degradation()


def _check_result_contract() -> None:
    payload = _recognition_result("success")
    duplicate = copy.deepcopy(payload["data"]["items"][0])
    duplicate["so_item"] = "20"
    duplicate["quantity"] = "3"
    payload["data"]["items"].append(duplicate)
    result = PhilipsWgqRecognitionResult.model_validate(payload).model_dump(mode="json")
    assert [item["so_item"] for item in result["data"]["items"]] == ["10", "20"]
    assert [item["product_id"] for item in result["data"]["items"]] == [PRODUCT_ID, PRODUCT_ID]
    assert result["data"]["header"]["etd"] == "2026-05-25"

    partial = copy.deepcopy(payload)
    partial["outcome"] = "partial_success"
    partial["problems"] = [
        {
            "source": "attachment.docx",
            "location": "attachments",
            "issue": "无关附件已忽略",
            "action": "无需处理",
        }
    ]
    assert PhilipsWgqRecognitionResult.model_validate(partial).outcome == "success"
    partial_with_unlisted_missing = copy.deepcopy(partial)
    partial_with_unlisted_missing["data"]["items"][0]["chinese_name"] = None
    checked_partial = PhilipsWgqRecognitionResult.model_validate(partial_with_unlisted_missing)
    assert checked_partial.outcome == "partial_success"
    assert "data.items[0].chinese_name" in checked_partial.problems[-1].issue
    partial_without_core_fact = copy.deepcopy(partial)
    partial_without_core_fact["data"]["items"][0]["product_id"] = None
    _assert_invalid(partial_without_core_fact)
    input_problems = PhilipsWgqRecognitionResult.model_validate(_recognition_result("input problems"))
    assert input_problems.data.items == []

    extra = copy.deepcopy(payload)
    extra["unexpected"] = True
    _assert_invalid(extra)
    # success may carry non-empty problems (field gaps / master-data misses).
    success_with_problems = copy.deepcopy(payload)
    success_with_problems["problems"] = partial["problems"]
    assert (
        PhilipsWgqRecognitionResult.model_validate(success_with_problems).outcome
        == "success"
    )
    bad_partial = copy.deepcopy(payload)
    bad_partial["outcome"] = "partial_success"
    bad_partial["data"]["items"][0]["chinese_name"] = None
    bad_partial["problems"] = []
    _assert_invalid(bad_partial)
    bad_input = _recognition_result("input problems")
    bad_input["problems"] = []
    _assert_invalid(bad_input)

    normalized = copy.deepcopy(payload)
    item = normalized["data"]["items"][0]
    item["quantity"] = "1,000.00"
    item["currency"] = "usd"
    item["trade_terms"] = "fob"
    assert PhilipsWgqRecognitionResult.model_validate(normalized).model_dump(mode="json")["data"]["items"][0] == {
        **result["data"]["items"][0],
        "quantity": "1000",
        "currency": "USD",
        "trade_terms": "FOB",
    }


def _check_tracking_and_oracle() -> None:
    rows = {
        PRODUCT_ID: (
            "Oracle中文名",
            "oracle-model",
            "德国",
            "9999999999",
            "套",
            "oracle-法一",
            "oracle-法二",
        )
    }
    connection = _FakeConnection(rows)
    env = {
        "ORACLE_DSN": "dsn",
        "ORACLE_USERNAME": "user",
        "ORACLE_PASSWORD": "password",
        "ORACLE_TIMEOUT_SECONDS": "5",
    }
    with patch.dict(os.environ, env, clear=True), patch(
        "oracledb.connect",
        return_value=connection,
    ):
        result = lookup_philips_wgq_master_data(
            ["109890-000-85103", PRODUCT_ID],
            "/artifacts/uploads/tracking.xlsx",
        )

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["product_id"] == PRODUCT_ID
    assert item["chinese_name"] == "申报页中文名"
    assert item["specification"] == "latest-model"
    assert item["origin_country"] == "美国"
    assert item["customs_code"] == "9018909090"
    assert item["unit"] == "套"
    assert item["legal_unit_1"] == "个"
    assert item["legal_unit_2"] == "千克"
    assert item["new_or_used"] == "新"
    assert item["business_unit"] == "CT"
    assert item["declaration_elements"] == "用途：医疗诊断；品牌：Philips；新旧：新"
    assert connection.cursor_instance.queried == [PRODUCT_ID]
    assert result["problems"] == []

    forbidden = {
        "quantity",
        "currency",
        "unit_price",
        "total_price",
        "original_waybill_number",
        "gross_weight",
        "net_weight",
        "po",
        "so",
        "dn",
        "om",
        "12NC",
        "中文品名",
        "库存数量",
    }
    assert forbidden.isdisjoint(item)


def _check_strict_tracking_no_fallback() -> None:
    with patch.dict(os.environ, {}, clear=True):
        result = lookup_philips_wgq_master_data(
            [NO_VALID_TRACKING_ID],
            "/artifacts/uploads/tracking.xlsx",
        )
    item = result["items"][0]
    assert all(item[field] is None for field in item if field != "product_id")
    assert any("未找到合格 Tracking" in problem["issue"] for problem in result["problems"])
    assert any("Oracle 配置缺失" in problem["issue"] for problem in result["problems"])


def _check_oracle_degradation() -> None:
    env = {
        "ORACLE_DSN": "dsn",
        "ORACLE_USERNAME": "user",
        "ORACLE_PASSWORD": "password",
    }
    with patch.dict(os.environ, env, clear=True), patch(
        "oracledb.connect",
        side_effect=RuntimeError("offline"),
    ):
        result = lookup_philips_wgq_master_data([PRODUCT_ID])
    assert result["items"][0]["chinese_name"] is None
    assert any("Oracle 查询失败" in problem["issue"] for problem in result["problems"])

    connection = _FakeConnection({})
    with patch.dict(os.environ, env, clear=True), patch(
        "oracledb.connect",
        return_value=connection,
    ):
        result = lookup_philips_wgq_master_data([PRODUCT_ID])
    assert any("Oracle 未命中" in problem["issue"] for problem in result["problems"])


def _tracking_fixture(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "进口"
    sheet.append(
        [
            "状态",
            "料号",
            "数量",
            "备案单价USD",
            "运单号",
            "毛重",
            "中文品名",
            "型号",
            "原产国",
            "HS编码",
            "Modality",
            "申报计量单位",
            "法定第一单位",
            "法定第二单位",
        ]
    )
    sheet.append(["进口", PRODUCT_ID, 99, 88, "OLD", 77, "old-name", "old-model", "日本", "111", "OLD", "个", "台", "克"])
    sheet.append([None, PRODUCT_ID, 98, 87, "BLANK", 76, "blank-name", "blank-model", "法国", "222", "BAD", "件", "台", "克"])
    sheet.append(["备注进口", PRODUCT_ID, 97, 86, "NOTE", 75, "note-name", "note-model", "英国", "333", "BAD", "件", "台", "克"])
    sheet.append([" 已出 ", PRODUCT_ID, 96, 85, "LATEST", 74, "latest-name", "latest-model", "美国", "9018909090", "CT", None, None, None])
    sheet.append([None, NO_VALID_TRACKING_ID, 1, 2, "BAD-1", 3, "bad-empty", "bad", "中国", "444", "BAD", "件", "台", "克"])
    sheet.append(["进口备注", NO_VALID_TRACKING_ID, 1, 2, "BAD-2", 3, "bad-note", "bad", "中国", "555", "BAD", "件", "台", "克"])

    declaration = workbook.create_sheet("申报要素")
    declaration.append(
        [
            "飞利浦料号",
            "中文品名",
            "规格型号",
            "原产国",
            "HS code",
            "法定第一单位",
            "法定第二单位",
            "用途",
            "品牌",
            "新旧",
            "主要配置",
            "Modality",
        ]
    )
    declaration.append([PRODUCT_ID, "申报页中文名", None, "美国", "9018909090", "个", "千克", "医疗诊断", "Philips", "新", "//", "CT"])
    declaration.append([NO_VALID_TRACKING_ID, "不得读取", "bad", "中国", "666", "台", "克", "无效", "Bad", "旧", "//", "BAD"])
    workbook.save(path)


class _FakeCursor:
    def __init__(self, rows: dict[str, tuple[str, ...]]) -> None:
        self.rows = rows
        self.product_id = ""
        self.queried: list[str] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, *, product_id: str) -> None:
        assert ":product_id" in sql
        self.product_id = product_id
        self.queried.append(product_id)

    def fetchone(self) -> tuple[str, ...] | None:
        return self.rows.get(self.product_id)


class _FakeConnection:
    def __init__(self, rows: dict[str, tuple[str, ...]]) -> None:
        self.cursor_instance = _FakeCursor(rows)
        self.call_timeout = 0

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance


def _assert_invalid(payload: dict[str, object]) -> None:
    try:
        PhilipsWgqRecognitionResult.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("invalid recognition result was accepted")


if __name__ == "__main__":
    run()
