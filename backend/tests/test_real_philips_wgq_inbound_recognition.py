from __future__ import annotations

import mimetypes
import os
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import requests


WORKFLOW = "philips_wgq_inbound_recognition"
DEFAULT_BASE_URL = "http://127.0.0.1:8500"
DEFAULT_SAMPLE_ROOT = Path(r"C:\Users\0325\Desktop\Agent测试用例\渠道文件测试用例\飞利浦外高桥\进境")
DEFAULT_TIMEOUT_SECONDS = 7200.0
DEFAULT_POLL_SECONDS = 1.0

_CASES = (
    ("DHL", Path("DHL快件/测试用例一"), "9198153694", (Path("DHL快件/测试用例一/DHL快件.zip"),)),
    ("DSV", Path("DSV普货/测试用例一"), "SIN0588220", ()),
    ("FedEx", Path("FedEx快件/测试用例一"), "491802621943", (Path("FedEx快件/测试用例二/王士奇-Agent开发工程师-优化版简历.docx"),)),
    ("UPS", Path("UPS普货/测试用例一"), "3512498462", ()),
    ("康捷空", Path("康捷空普货/测试用例二"), "4520163152", ()),
)


def run() -> None:
    if os.getenv("DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST") != "1":
        print("skipped real Philips WGQ test; set DSAGENTS_RUN_REAL_PHILIPS_WGQ_TEST=1 to run it")
        return
    root = Path(os.getenv("DSAGENTS_PHILIPS_WGQ_SAMPLE_ROOT", str(DEFAULT_SAMPLE_ROOT)))
    tracking = root / "CS进口业务表格-DS Tracking2026.xlsx"
    assert tracking.is_file(), f"Tracking file not found: {tracking}"
    base_url = os.getenv("DSAGENTS_API_BASE_URL", DEFAULT_BASE_URL)
    timeout = float(os.getenv("DSAGENTS_REAL_PHILIPS_WGQ_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    poll = float(os.getenv("DSAGENTS_REAL_PHILIPS_WGQ_POLL_SECONDS", DEFAULT_POLL_SECONDS))
    session_ids: set[str] = set()

    for name, relative_dir, waybill, extras in _CASES:
        case_dir = root / relative_dir
        pdfs = sorted(path for path in case_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
        assert pdfs, f"No PDF files found for {name}: {case_dir}"
        extra_paths = [root / extra for extra in extras]
        payload = _exercise_case(base_url, [*pdfs, tracking, *extra_paths], timeout, poll)
        run_snapshot = payload["run"]
        assert run_snapshot["session_id"] not in session_ids
        session_ids.add(run_snapshot["session_id"])
        _assert_result(name, payload, waybill, bool(extra_paths))
        print(f"passed {name}: {run_snapshot['run_id']}")


def _exercise_case(
    base_url: str,
    files: list[Path],
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    session = requests.Session()
    artifacts = _upload(session, base_url, files)
    response = session.post(
        _url(base_url, "/runs"),
        json={
            "workflow": WORKFLOW,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "artifact", "path": artifact["file_path"]}
                        for artifact in artifacts
                    ],
                }
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    queued = response.json()
    assert queued["status"] == "queued"

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = session.get(_url(base_url, f"/runs/{queued['run_id']}"), timeout=60)
        response.raise_for_status()
        payload = response.json()
        status = payload["run"]["status"]
        if status == "succeeded":
            return payload
        if status in {"failed", "cancelled"}:
            raise AssertionError(f"run {queued['run_id']} {status}: {payload['run'].get('error')}")
        time.sleep(poll_seconds)
    raise AssertionError(f"run {queued['run_id']} timed out")


def _upload(session: requests.Session, base_url: str, paths: list[Path]) -> list[dict[str, Any]]:
    for path in paths:
        assert path.is_file(), f"Sample file not found: {path}"
    with ExitStack() as stack:
        files = [
            (
                "files",
                (
                    path.name,
                    stack.enter_context(path.open("rb")),
                    mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                ),
            )
            for path in paths
        ]
        response = session.post(_url(base_url, "/upload"), files=files, timeout=600)
    response.raise_for_status()
    artifacts = response.json()["files"]
    assert len(artifacts) == len(paths)
    return artifacts


def _assert_result(name: str, payload: dict[str, Any], waybill: str, has_ignored_file: bool) -> None:
    run = payload["run"]
    result = payload["result"]
    assert run["workflow"] == payload["workflow"] == WORKFLOW
    assert run["result"] == result
    assert result["outcome"] in {"success", "partial_success"}, (name, result)
    assert result["data"]["header"]["原运单号"] == waybill
    items = result["data"]["items"]
    assert len(items) >= 1
    assert all(item["12NC"] and item["库存数量"] for item in items)
    assert all(item["币种"] and item["单价"] and item["总价"] for item in items)
    assert any(item["中文品名"] or item["规格型号"] or item["海关编码"] for item in items)
    assert all(item["新旧"] != "//" and "：//" not in (item["申报要素"] or "") for item in items)
    _assert_lookup_called(payload)
    if has_ignored_file:
        assert result["outcome"] == "partial_success"
        assert any(
            "忽略" in problem["issue"]
            or "忽略" in problem["action"]
            or problem["source"].lower() in {"zip", "docx"}
            or problem["source"].lower().endswith((".zip", ".docx"))
            for problem in result["problems"]
        )


def _assert_lookup_called(payload: dict[str, Any]) -> None:
    calls = [
        event["payload"]
        for event in payload["events"]
        if event["type"] == "tool_execution"
        and event["payload"].get("name") == "lookup_philips_wgq_master_data"
        and isinstance(event["payload"].get("args"), dict)
    ]
    assert calls, "lookup_philips_wgq_master_data was not called"
    assert any(call["args"].get("tracking_artifact") for call in calls)


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


if __name__ == "__main__":
    run()
