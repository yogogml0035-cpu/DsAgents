from __future__ import annotations

import copy
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from tecan_import import (
    _validate_extraction,
    build_tecan_canonical,
    generate_tecan_documents,
    normalize_pn,
    save_tecan_adjudication,
    save_tecan_extraction,
)
from workflow_artifacts import read_json_artifact, resolve_artifact_path


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

        with patch("workflow_artifacts.artifacts_root", return_value=artifacts):
            base = _extraction()
            a = _save(base, "tecan-extractor-a")
            b = _save(base, "tecan-extractor-b")

            consistent = _build([a, b])
            assert consistent["status"] == "canonical"
            canonical = read_json_artifact(consistent["canonical_artifact"])
            assert canonical["currency"] == "USD"
            assert canonical["logistics"] == {"pieces": 2, "gross_weight": 12.5, "net_weight": 5}
            assert [item["net_price"] for item in canonical["items"]] == [10, 15]
            assert [item["source_sheet"] for item in canonical["items"]] == ["Sheet1", "备用数据"]
            assert sum(item["gross_weight"] for item in canonical["items"]) == 12.5
            assert any(check["field"] == "items.P200.source_sheet" for check in canonical["manual_checks"])

            assert _build([a, "/artifacts/downloads/missing.json"])["status"] == "needs_c"

            conflict = copy.deepcopy(base)
            conflict["logistics"]["gross_weight"] = _field(13)
            conflict_b = _save(conflict, "tecan-extractor-b")
            assert _build([a, conflict_b])["status"] == "needs_c"
            premature = save_tecan_adjudication(
                [a, conflict_b],
                [{"conflict_id": "logistics.gross_weight", "value": 12.5, "reason": "回查"}],
            )["artifact_path"]
            try:
                _build([a, conflict_b], adjudication_artifact=premature)
            except ValueError:
                pass
            else:
                raise AssertionError("A/B conflict must require extractor C before adjudication")

            assert _build(
                ["/artifacts/downloads/missing-a.json", "/artifacts/downloads/missing-b.json"]
            )["status"] == "needs_input"

            c = _save(base, "tecan-extractor-c")
            assert _build([c])["status"] == "canonical"

            vote_b = copy.deepcopy(base)
            vote_b["logistics"]["pieces"] = _field(3)
            vote_b_path = _save(vote_b, "tecan-extractor-b")
            vote_c = copy.deepcopy(base)
            vote_c["logistics"]["pieces"] = _field(2)
            vote_c_path = _save(vote_c, "tecan-extractor-c")
            majority = _build([a, vote_b_path, vote_c_path])
            assert majority["status"] == "canonical"
            assert read_json_artifact(majority["canonical_artifact"])["logistics"]["pieces"] == 2

            vote_c["logistics"]["pieces"] = _field(4)
            no_majority_c = _save(vote_c, "tecan-extractor-c")
            unresolved = _build([a, vote_b_path, no_majority_c])
            assert unresolved["status"] == "needs_adjudication"
            assert unresolved["conflicts"] == [
                {
                    "conflict_id": "logistics.pieces",
                    "field": "logistics.pieces",
                    "values": [
                        {"extractor": "tecan-extractor-a", "value": 2},
                        {"extractor": "tecan-extractor-b", "value": 3},
                        {"extractor": "tecan-extractor-c", "value": 4},
                    ],
                }
            ]
            adjudication = save_tecan_adjudication(
                [a, vote_b_path, no_majority_c],
                [{"conflict_id": "logistics.pieces", "value": 2, "reason": "回查确认"}],
            )["artifact_path"]
            resolved = _build(
                [a, vote_b_path, no_majority_c],
                adjudication_artifact=adjudication,
            )
            assert resolved["status"] == "canonical"

            missing = copy.deepcopy(base)
            missing["logistics"]["gross_weight"] = _field(None, "low")
            missing_a = _save(missing, "tecan-extractor-a")
            missing_b = _save(missing, "tecan-extractor-b")
            assert _build([missing_a, missing_b])["status"] == "needs_c"
            missing_c = _save(missing, "tecan-extractor-c")
            required = _build([missing_a, missing_b, missing_c])
            assert required == {"status": "needs_input", "missing": ["logistics.gross_weight"]}

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

            mixed = _build([a, b], order="/artifacts/uploads/mixed-currency.xlsx")
            assert mixed["status"] == "needs_input"
            assert mixed["reason"] == "order_mixed_currency"

            information = [
                "/artifacts/uploads/设备信息.xlsx",
                "/artifacts/uploads/配件信息.xlsx",
            ]
            info_conflict = _build([a, b], information=information)
            assert info_conflict["status"] == "needs_input"
            assert info_conflict["reason"] == "information_conflict"
            assert info_conflict["conflicts"][0]["pn"] == "P100"

            selected = _build(
                [a, b],
                information=information,
                pn_info_source_overrides={"P100": "配件"},
            )
            assert selected["status"] == "canonical"
            selected_canonical = read_json_artifact(selected["canonical_artifact"])
            assert selected_canonical["items"][0]["description"] == "Accessory P100"
            assert selected_canonical["source_artifacts"]["information"] == information

            equipment = _build([a, b], information=information, info_source_preference="equipment")
            assert equipment["status"] == "canonical"
            assert read_json_artifact(equipment["canonical_artifact"])["items"][0]["description"] == "Device P100"

            generated = generate_tecan_documents(consistent["canonical_artifact"])
            assert generated["status"] == "generated"
            assert len(generated["artifacts"]) == 1
            _check_workbook(generated["artifacts"][0])


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


def _build(
    paths: list[str],
    *,
    order: str = "/artifacts/uploads/order.xlsx",
    information: list[str] | None = None,
    **kwargs: object,
) -> dict[str, object]:
    return build_tecan_canonical(
        paths,
        order,
        information or ["/artifacts/uploads/设备信息.xlsx"],
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
