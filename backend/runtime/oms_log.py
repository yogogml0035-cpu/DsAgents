"""OMS run-created index log (JSON Lines).

Best-effort append-only index for ops grep by time / filename stem.
Not part of the run_events observability path; never blocks run creation.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 与 SqliteRunLedger 一致：中国标准时间（UTC+8）。
_CHINA_TZ = timezone(timedelta(hours=8))

# 与 ResourceConfig 相同：锚定 backend/，与 CWD 无关。
_BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OMS_LOG_PATH = _BACKEND_DIR / "log" / "oms_log.log"


def extract_run_files(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Collect artifact paths from request messages (order preserved, no dedupe)."""
    files: list[dict[str, str]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "artifact":
                continue
            path = block.get("path")
            if not isinstance(path, str) or not path:
                continue
            files.append({"name": Path(path).name, "path": path})
    return files


def append_run_created_log(
    *,
    run_id: str,
    session_id: str,
    workflow: str | None,
    messages: list[dict[str, Any]],
    log_path: Path | None = None,
    created_at: str | None = None,
) -> None:
    """Append one immutable run_created JSONL record. Raises on I/O errors."""
    target = log_path if log_path is not None else DEFAULT_OMS_LOG_PATH
    record = {
        "event": "run_created",
        "created_at": created_at if created_at is not None else _now_text(),
        "run_id": run_id,
        "session_id": session_id,
        "workflow": workflow,
        "files": extract_run_files(messages),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _now_text() -> str:
    # 中国时区本地时间：YYYY-MM-DD HH:MM:SS，与 SqliteRunLedger 一致。
    return datetime.now(_CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
