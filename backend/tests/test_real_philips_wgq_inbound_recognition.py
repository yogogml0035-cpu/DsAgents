from __future__ import annotations

import json
import mimetypes
import os
import sys
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
_MAX_STREAM_TEXT = 4000

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
    *,
    stream: bool = False,
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
    run_id = queued["run_id"]
    if stream:
        print(f"run_id={run_id} session_id={queued.get('session_id')}", flush=True)

    printer = _EventStreamPrinter() if stream else None
    after_event_id: int | None = None
    deadline = time.monotonic() + timeout_seconds
    detail_url = _url(base_url, f"/runs/{run_id}")
    while time.monotonic() < deadline:
        params: dict[str, int] = {}
        if stream and after_event_id is not None:
            params["after_event_id"] = after_event_id
        response = session.get(detail_url, params=params or None, timeout=60)
        response.raise_for_status()
        payload = response.json()
        if printer is not None:
            for event in payload.get("events") or []:
                printer.emit(event)
                event_id = event.get("event_id")
                if isinstance(event_id, int):
                    after_event_id = event_id
        status = payload["run"]["status"]
        if status == "succeeded":
            if printer is not None:
                printer.close()
                # Incremental polls only carry new events; re-fetch full detail for callers.
                response = session.get(detail_url, timeout=60)
                response.raise_for_status()
                return response.json()
            return payload
        if status in {"failed", "cancelled"}:
            if printer is not None:
                printer.close()
            raise AssertionError(f"run {run_id} {status}: {payload['run'].get('error')}")
        time.sleep(poll_seconds)
    if printer is not None:
        printer.close()
    raise AssertionError(f"run {run_id} timed out")


class _EventStreamPrinter:
    """Render GET /runs events to the terminal as they arrive."""

    def __init__(self) -> None:
        self._open_stream: str | None = None

    def emit(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        created_at = event.get("created_at") or ""

        if event_type == "thinking":
            self._write_delta("thinking", payload.get("content") or "")
            return
        if event_type == "text_delta":
            self._write_delta("text", payload.get("content") or "")
            return

        self.close()
        if event_type == "status":
            status = payload.get("status") or event.get("raw", {}).get("status") or "?"
            error = payload.get("error")
            line = f"[{created_at}] status -> {status}"
            if error:
                line += f" error={error}"
            print(line, flush=True)
            return
        if event_type == "tool_execution":
            self._print_tool_execution(created_at, payload)
            return
        if event_type == "tool_progress":
            name = payload.get("name") or "?"
            status = payload.get("status") or "?"
            detail = {k: v for k, v in payload.items() if k not in {"name", "status"}}
            suffix = f" {self._compact(detail)}" if detail else ""
            print(f"[{created_at}] tool_progress {name} -> {status}{suffix}", flush=True)
            return
        if event_type == "assistant_message":
            thinking = payload.get("thinking")
            text = payload.get("text")
            print(f"[{created_at}] assistant_message", flush=True)
            if thinking:
                print(self._indent_block("thinking", thinking), flush=True)
            if text:
                print(self._indent_block("text", text), flush=True)
            return
        if event_type == "model_usage":
            scope = payload.get("scope") or "model"
            model = payload.get("model") or "?"
            inp = payload.get("input_tokens")
            out = payload.get("output_tokens")
            print(
                f"[{created_at}] usage scope={scope} model={model} "
                f"in={inp} out={out}",
                flush=True,
            )
            return
        print(f"[{created_at}] {event_type}: {self._compact(payload)}", flush=True)

    def close(self) -> None:
        if self._open_stream is not None:
            print(flush=True)
            self._open_stream = None

    def _write_delta(self, kind: str, content: str) -> None:
        if not content:
            return
        if self._open_stream != kind:
            self.close()
            print(f"[{kind}] ", end="", flush=True)
            self._open_stream = kind
        print(content, end="", flush=True)
        sys.stdout.flush()

    def _print_tool_execution(self, created_at: str, payload: dict[str, Any]) -> None:
        name = payload.get("name") or "?"
        status = payload.get("status")
        agent = payload.get("agent_name")
        header = f"[{created_at}] tool {name}"
        if agent:
            header += f" @{agent}"
        if status:
            header += f" -> {status}"
            duration = payload.get("duration_ms")
            if duration is not None:
                header += f" ({duration}ms)"
        print(header, flush=True)
        args = payload.get("args")
        if isinstance(args, dict) and args:
            print(self._indent_block("args", self._compact(args, indent=2)), flush=True)
        result = payload.get("result")
        if result is not None:
            print(self._indent_block("result", str(result)), flush=True)
        # updates-mode tool_execution only carries the tool-call request (no status).
        if status is None and "tool_call_id" in payload:
            tool_call_id = payload.get("tool_call_id")
            if tool_call_id:
                print(f"  tool_call_id={tool_call_id}", flush=True)

    @staticmethod
    def _indent_block(label: str, text: str) -> str:
        clipped = text if len(text) <= _MAX_STREAM_TEXT else text[:_MAX_STREAM_TEXT] + "…(truncated)"
        lines = clipped.splitlines() or [clipped]
        body = "\n".join(f"  {line}" for line in lines)
        return f"  {label}:\n{body}"

    @staticmethod
    def _compact(value: Any, *, indent: int | None = None) -> str:
        text = json.dumps(value, ensure_ascii=False, indent=indent, default=str)
        if len(text) > _MAX_STREAM_TEXT:
            return text[:_MAX_STREAM_TEXT] + "…(truncated)"
        return text


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
    assert result["data"]["header"]["original_waybill_number"] == waybill
    items = result["data"]["items"]
    assert len(items) >= 1
    assert all(item["product_id"] and item["quantity"] for item in items)
    assert all(item["currency"] and item["unit_price"] and item["total_price"] for item in items)
    assert any(item["chinese_name"] or item["specification"] or item["customs_code"] for item in items)
    assert all(
        item["new_or_used"] != "//" and "：//" not in (item["declaration_elements"] or "")
        for item in items
    )
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
