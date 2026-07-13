from __future__ import annotations

import argparse
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8500"
DEFAULT_IMAGE_PATH = Path(r"D:\AgentProject\DsAgents\backend\tests\tests_file\imags1.jpg")
DEFAULT_QUESTION = "请详细描述这张图片"
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_POLL_SECONDS = 0.1


def run() -> None:
    if os.getenv("DSAGENTS_RUN_REAL_IMAGE_TEST") != "1":
        print("skipped real image integration test; set DSAGENTS_RUN_REAL_IMAGE_TEST=1 to run it")
        return
    _exercise_real_image_run(
        base_url=os.getenv("DSAGENTS_API_BASE_URL", DEFAULT_BASE_URL),
        image_path=Path(os.getenv("DSAGENTS_IMAGE_PATH", str(DEFAULT_IMAGE_PATH))),
        question=os.getenv("DSAGENTS_IMAGE_QUESTION", DEFAULT_QUESTION),
        timeout_seconds=float(os.getenv("DSAGENTS_REAL_IMAGE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
        poll_seconds=float(os.getenv("DSAGENTS_REAL_IMAGE_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload an image, start /runs, and poll the latest model result.")
    parser.add_argument("--base-url", default=os.getenv("DSAGENTS_API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--image", type=Path, default=Path(os.getenv("DSAGENTS_IMAGE_PATH", str(DEFAULT_IMAGE_PATH))))
    parser.add_argument("--question", default=os.getenv("DSAGENTS_IMAGE_QUESTION", DEFAULT_QUESTION))
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("DSAGENTS_REAL_IMAGE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=float(os.getenv("DSAGENTS_REAL_IMAGE_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
    )
    args = parser.parse_args()
    _exercise_real_image_run(
        base_url=args.base_url,
        image_path=args.image,
        question=args.question,
        timeout_seconds=args.timeout,
        poll_seconds=args.poll,
    )


def _exercise_real_image_run(
    *,
    base_url: str,
    image_path: Path,
    question: str,
    timeout_seconds: float,
    poll_seconds: float,
) -> None:
    if not image_path.is_file():
        raise AssertionError(f"Image file not found: {image_path}")

    session = requests.Session()
    artifact_path = _upload_image(session, base_url, image_path)
    run_payload = _start_run(session, base_url, artifact_path, question)
    final_reply = _wait_for_run(session, base_url, run_payload["run_id"], timeout_seconds, poll_seconds)

    assert final_reply, "model returned an empty final reply"
    print("\nfinal reply:")
    print(final_reply)


def _upload_image(session: requests.Session, base_url: str, image_path: Path) -> str:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    with image_path.open("rb") as image_file:
        response = session.post(
            _url(base_url, "/upload"),
            files=[("files", (image_path.name, image_file, mime_type))],
            timeout=60,
        )
    response.raise_for_status()
    files = response.json()["files"]
    file_info = files[0]
    assert file_info["size"] == image_path.stat().st_size
    artifact_path = file_info["file_path"]
    print(f"uploaded: {artifact_path}")
    return artifact_path


def _start_run(session: requests.Session, base_url: str, artifact_path: str, question: str) -> dict[str, Any]:
    response = session.post(
        _url(base_url, "/runs"),
        json={
            "session_id": None,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "artifact", "path": artifact_path},
                    ],
                }
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    assert payload["status"] == "queued"
    print(f"run queued: {payload['run_id']}")
    return payload


def _wait_for_run(
    session: requests.Session,
    base_url: str,
    run_id: str,
    timeout_seconds: float,
    poll_seconds: float,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_latest_text = ""
    while time.monotonic() < deadline:
        response = session.get(_url(base_url, f"/runs/{run_id}"), timeout=30)
        response.raise_for_status()
        payload = response.json()
        run = payload["run"]
        latest_text = _latest_text(payload.get("latest_content_event"))
        if latest_text and latest_text != last_latest_text:
            last_latest_text = latest_text
            print("\nlatest result:")
            print(latest_text)

        if run["status"] == "succeeded":
            return run["reply"] or last_latest_text
        if run["status"] == "failed":
            raise AssertionError(f"run failed: {run.get('error')}")
        if run["status"] == "cancelled":
            raise AssertionError(f"run cancelled: {run.get('error')}")

        time.sleep(poll_seconds)

    raise AssertionError(f"run {run_id} did not finish within {timeout_seconds} seconds")


def _latest_text(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return ""
    return payload.get("text") or payload.get("content") or ""


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


if __name__ == "__main__":
    main()
