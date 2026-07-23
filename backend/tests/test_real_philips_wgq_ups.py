from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from skills.philips_wgq_inbound_recognition import WAG_WORKFLOW
from tests.test_real_philips_wgq_inbound_recognition import _exercise_case


WORKFLOW = WAG_WORKFLOW
DEFAULT_BASE_URL = "http://127.0.0.1:8501"
DEFAULT_CASE_DIR = Path(
    r"C:\Users\0325\Desktop\Agent测试用例\渠道文件测试用例\飞利浦外高桥\进境\UPS普货\测试用例一"
)
DEFAULT_TIMEOUT_SECONDS = 7200.0
# Faster polling so thinking / tool / text events feel closer to live streaming.
DEFAULT_POLL_SECONDS = 0.2


def run() -> None:
    case_dir = Path(os.getenv("DSAGENTS_PHILIPS_WGQ_UPS_CASE_DIR", str(DEFAULT_CASE_DIR)))
    pdfs = sorted(
        path
        for path in case_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )
    # assert len(pdfs) == 2, f"Expected the two UPS PDFs in {case_dir}, found: {pdfs!r}"
    print("uploading:")
    for path in pdfs:
        print(f"- {path}")

    payload = _exercise_case(
        os.getenv("DSAGENTS_API_BASE_URL", DEFAULT_BASE_URL),
        pdfs,
        float(os.getenv("DSAGENTS_REAL_PHILIPS_WGQ_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
        float(os.getenv("DSAGENTS_REAL_PHILIPS_WGQ_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
        stream=True,
    )
    # _assert_response(payload)
    print("\nfinal result:")
    print(json.dumps(payload["result"], ensure_ascii=False, indent=2))
    usage = payload.get("usage")
    if usage:
        print("\nusage:")
        print(json.dumps(usage, ensure_ascii=False, indent=2))


def _assert_response(payload: dict[str, Any]) -> None:
    run_snapshot = payload["run"]
    result = payload.get("result")
    assert run_snapshot["status"] == "succeeded"
    assert run_snapshot["workflow"] == payload["workflow"] == WORKFLOW
    assert run_snapshot["result"] == result
    assert isinstance(result, dict)
    assert result["outcome"] in {"success", "partial_success"}
    assert isinstance(result.get("data"), dict)
    assert result["data"]["header"]["original_waybill_number"] == "3512498462"
    assert result["data"]["items"], "No invoice items were returned"
    assert all(item["product_id"] and item["quantity"] for item in result["data"]["items"])
    assert all(item["币种"] and item["单价"] and item["总价"] for item in result["data"]["items"])
    assert any(
        event["type"] == "tool_execution"
        and event["payload"].get("name") == "parse_documents"
        for event in payload["events"]
    ), "parse_documents was not called"


if __name__ == "__main__":
    run()
