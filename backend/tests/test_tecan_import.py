from __future__ import annotations

import copy
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from dsagents.integrations.artifacts import read_json_artifact, resolve_artifact_path
from dsagents.skills.tecanimport.scripts.tools import (
    _validate_extraction,
    generate_tecan_import,
    normalize_pn,
    save_tecan_extraction,
)


def run() -> None:
    assert normalize_pn(Decimal("1000")) == "1000"
    assert normalize_pn("1000.0") == "1000"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        artifacts = (Path(tmp) / "artifacts").resolve()
        downloads = artifacts / "downloads"
        uploads = artifacts / "uploads"
        downloads.mkdir(parents=True)
        uploads.mkdir(parents=True)
        (downloads / "mineru.json").write_text('{"content_list":[]}', encoding="utf-8")
        _order_fixture(uploads / "order.xlsx")
        _order_fixture(uploads / "mixed-currency.xlsx", mixed_currency=True)
        _information_fixture(uploads / "设备信息.xlsx")
        _conflicting_information_fixture(uploads / "配件信息.xlsx")

        with patch("dsagents.integrations.artifacts.artifacts_root", return_value=artifacts):
            base = _extraction()
            a = _save(base, "tecan-extractor-a")
            b = _save(base, "tecan-extractor-b")

            # A/B consistent -> one-shot generate.
            consistent = _generate([a, b])
            assert consistent["status"] == "generated"
            canonical = read_json_artifact(consistent["canonical_artifact"])
            assert canonical["currency"] == "USD"
            assert canonical["logistics"] == {"pieces": 2, "gross_weight": 12.5, "net_weight": 5}
            assert [item["net_price"] for item in canonical["items"]] == [10, 15]
            assert [item["source_sheet"] for item in canonical["items"]] == ["Sheet1", "备用数据"]
            assert sum(item["gross_weight"] for item in canonical["items"]) == 12.5
            assert any(check["field"] == "items.P200.source_sheet" for check in canonical["manual_checks"])

            # Only one A/B extractor -> needs C.
            one_failed = _generate([a, "/artifacts/downloads/missing.json"])
            assert one_failed["code"] == "input_problems"
            assert any("extractor C" in p["issue"] for p in one_failed["problems"])

            # A/B conflict on gross_weight -> needs C.
            conflict = copy.deepcopy(base)
            conflict["logistics"]["gross_weight"] = _field(13)
            conflict_b = _save(conflict, "tecan-extractor-b")
            conflict_result = _generate([a, conflict_b])
            assert conflict_result["code"] == "input_problems"
            assert any("extractor C" in p["issue"] for p in conflict_result["problems"])

            # Premature decisions on A/B conflict -> input_problems.
            premature = _generate(
                [a, conflict_b],
                decisions=[{"conflict_id": "logistics.gross_weight", "value": 12.5, "reason": "回查"}],
            )
            assert premature["code"] == "input_problems"
            assert any("C 抽取尚未完成" in p["issue"] for p in premature["problems"])

            # All extractions missing -> input_problems.
            all_missing = _generate(
                ["/artifacts/downloads/missing-a.json", "/artifacts/downloads/missing-b.json"]
            )
            assert all_missing["code"] == "input_problems"

            # C alone (consistent) -> generate.
            c = _save(base, "tecan-extractor-c")
            assert _generate([c])["status"] == "generated"

            # A/B/C majority on pieces -> generate with pieces=2.
            vote_b = copy.deepcopy(base)
            vote_b["logistics"]["pieces"] = _field(3)
            vote_b_path = _save(vote_b, "tecan-extractor-b")
            vote_c = copy.deepcopy(base)
            vote_c["logistics"]["pieces"] = _field(2)
            vote_c_path = _save(vote_c, "tecan-extractor-c")
            majority = _generate([a, vote_b_path, vote_c_path])
            assert majority["status"] == "generated"
            assert read_json_artifact(majority["canonical_artifact"])["logistics"]["pieces"] == 2

            # A/B/C conflict on pieces with no decision -> input_problems on that field.
            vote_c["logistics"]["pieces"] = _field(4)
            no_majority_c = _save(vote_c, "tecan-extractor-c")
            unresolved = _generate([a, vote_b_path, no_majority_c])
            assert unresolved["code"] == "input_problems"
            assert any(
                p["location"] == "logistics.pieces" and "不一致" in p["issue"] for p in unresolved["problems"]
            )

            # Same A/B/C conflict resolved by an inline decision -> generate.
            resolved = _generate(
                [a, vote_b_path, no_majority_c],
                decisions=[{"conflict_id": "logistics.pieces", "value": 2, "reason": "回查确认"}],
            )
            assert resolved["status"] == "generated"

            # All extractors jointly missing gross_weight -> input_problems.
            missing = copy.deepcopy(base)
            missing["logistics"]["gross_weight"] = _field(None, "low")
            missing_a = _save(missing, "tecan-extractor-a")
            missing_b = _save(missing, "tecan-extractor-b")
            missing_c = _save(missing, "tecan-extractor-c")
            required = _generate([missing_a, missing_b, missing_c])
            assert required["code"] == "input_problems"
            assert any(p["location"] == "logistics.gross_weight" for p in required["problems"])

            # Old extraction contract is rejected.
            try:
                _validate_extraction(
                    {
                        "schema_version": 1,
                        "extractor_id": "tecan-extractor-a",
                        "logistics_fields": {},
                        "raw_items": [],
                    }
                )
            except ValueError:
                pass
            else:
                raise AssertionError("old Tecan extraction contract must be rejected")

            # Mixed currency order -> input_problems.
            mixed = _generate([a, b], order="/artifacts/uploads/mixed-currency.xlsx")
            assert mixed["code"] == "input_problems"
            assert any("币种" in p["issue"] for p in mixed["problems"])

            # Conflicting information records for a PN -> input_problems (no more
            # info_source_preference / pn_info_source_overrides).
            information = [
                "/artifacts/uploads/设备信息.xlsx",
                "/artifacts/uploads/配件信息.xlsx",
            ]
            info_conflict = _generate([a, b], information=information)
            assert info_conflict["code"] == "input_problems"
            assert any(
                p["location"] == "information.P100" and "不一致" in p["issue"]
                for p in info_conflict["problems"]
            )

            # Generated workbook carries the expected cell values.
            _check_workbook(consistent["artifacts"][0])


def _field(value: object, confidence: str = "high") -> dict[str, object]:
    return {"value": value, "confidence": confidence}


def _extraction() -> dict[str, object]:
    return {
        "logistics": {"pieces": _field(2), "gross_weight": _field("12.5 KG")},
        "items": [],
    }


def _save(payload: dict[str, object], extractor: str) -> str:
    return save_tecan_extraction(
        extractor,
        "/artifacts/downloads/mineru.json",
        payload["logistics"],
        payload["items"],
    )["artifact_path"]


def _generate(
    paths: list[str],
    *,
    order: str = "/artifacts/uploads/order.xlsx",
    information: list[str] | None = None,
    **kwargs: object,
) -> dict[str, object]:
    return generate_tecan_import(
        extraction_artifacts=paths,
        order_artifact=order,
        information_artifacts=information or ["/artifacts/uploads/设备信息.xlsx"],
        **kwargs,
    )


def _order_fixture(path: Path, *, mixed_currency: bool = False) -> None:
    workbook = Workbook()
    cover = workbook.active
    cover.title = "封面"
    cover["A1"] = "This sheet is not the order table"
    sheet = workbook.create_sheet("Orders")
    sheet.append(["PN", "Order Qty", "Amount"])
    sheet.append([" P100 ", 2, 20])
    sheet.append(["P200", 1, 15])
    sheet["C2"].number_format = "$#,##0.00"
    sheet["C3"].number_format = "€#,##0.00" if mixed_currency else "$#,##0.00"
    workbook.save(path)


def _information_fixture(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["料号", "英文品名", "原产国", "净重"])
    sheet.append(["P100", "Device P100", "CHN", 2])
    other = workbook.create_sheet("备用数据")
    other.append(["料号", "英文品名", "原产国", "参考净重"])
    other.append(["P200", "Device P200", "CHE", 1])
    workbook.save(path)


def _conflicting_information_fixture(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["料号", "英文品名", "原产国", "净重"])
    sheet.append(["P100", "Accessory P100", "DEU", 1.5])
    sheet.append(["P200", "Device P200", "CHE", 1])
    workbook.save(path)


def _check_workbook(artifact: str) -> None:
    path = resolve_artifact_path(artifact)
    workbook = load_workbook(path, data_only=False)
    assert set(workbook.sheetnames) >= {"Customs invoice", "Packing List"}
    customs = workbook["Customs invoice"]
    packing = workbook["Packing List"]
    assert customs["C33"].value == "P100"
    assert customs["H33"].value == 10
    assert customs["I34"].value == 15
    assert customs["H66"].value == "USD"
    assert customs["I66"].value == 35
    assert isinstance(packing["G33"].value, (int, float))
    assert isinstance(packing["H33"].value, (int, float))
    assert packing["G68"].value == 2
    assert packing["G69"].value == 5
    assert packing["G70"].value == 12.5
    assert packing["H33"].value + packing["H34"].value == packing["G70"].value
    workbook.close()


if __name__ == "__main__":
    run()
