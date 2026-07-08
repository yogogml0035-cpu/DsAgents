from __future__ import annotations

import argparse
import mimetypes
import os
import shutil
import time
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import Any

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8500"
DEFAULT_PDF_DIR = Path(__file__).resolve().parent / "tests_file" / "测试用例1"
DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parent / "tests_file" / "测试用例1" / "downloaded"
DEFAULT_REQUEST = "将这些pdf调用工具解析并下载下来"
DEFAULT_TIMEOUT_SECONDS = 7200.0
DEFAULT_UPLOAD_TIMEOUT_SECONDS = 600.0
DEFAULT_POLL_SECONDS = 0.5


def run() -> None:
    if os.getenv("DSAGENTS_RUN_REAL_MULTI_PDF_TEST") != "1":
        print("skipped real multi-pdf integration test; set DSAGENTS_RUN_REAL_MULTI_PDF_TEST=1 to run it")
        return
    _exercise_real_multi_pdf_run(
        base_url=os.getenv("DSAGENTS_API_BASE_URL", DEFAULT_BASE_URL),
        pdf_paths=_pdf_paths(Path(os.getenv("DSAGENTS_PDF_DIR", str(DEFAULT_PDF_DIR)))),
        download_dir=Path(os.getenv("DSAGENTS_MD_DOWNLOAD_DIR", str(DEFAULT_DOWNLOAD_DIR))),
        request=os.getenv("DSAGENTS_MULTI_PDF_REQUEST", DEFAULT_REQUEST),
        timeout_seconds=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
        upload_timeout_seconds=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_UPLOAD_TIMEOUT_SECONDS", DEFAULT_UPLOAD_TIMEOUT_SECONDS)),
        poll_seconds=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload PDFs once, ask the agent to call parse_documents once, and copy generated markdown files locally."
    )
    parser.add_argument("--base-url", default=os.getenv("DSAGENTS_API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--pdf-dir", type=Path, default=Path(os.getenv("DSAGENTS_PDF_DIR", str(DEFAULT_PDF_DIR))))
    parser.add_argument("--pdf", type=Path, action="append", default=[])
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=Path(os.getenv("DSAGENTS_MD_DOWNLOAD_DIR", str(DEFAULT_DOWNLOAD_DIR))),
    )
    parser.add_argument("--request", default=os.getenv("DSAGENTS_MULTI_PDF_REQUEST", DEFAULT_REQUEST))
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
    )
    parser.add_argument(
        "--upload-timeout",
        type=float,
        default=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_UPLOAD_TIMEOUT_SECONDS", DEFAULT_UPLOAD_TIMEOUT_SECONDS)),
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=float(os.getenv("DSAGENTS_REAL_MULTI_PDF_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
    )
    args = parser.parse_args()
    _exercise_real_multi_pdf_run(
        base_url=args.base_url,
        pdf_paths=args.pdf or _pdf_paths(args.pdf_dir),
        download_dir=args.download_dir,
        request=args.request,
        timeout_seconds=args.timeout,
        upload_timeout_seconds=args.upload_timeout,
        poll_seconds=args.poll,
    )


def _exercise_real_multi_pdf_run(
    *,
    base_url: str,
    pdf_paths: list[Path],
    download_dir: Path,
    request: str,
    timeout_seconds: float,
    upload_timeout_seconds: float,
    poll_seconds: float,
) -> None:
    if not pdf_paths:
        raise AssertionError("No PDF files found")
    for pdf_path in pdf_paths:
        if not pdf_path.is_file():
            raise AssertionError(f"PDF file not found: {pdf_path}")

    session = requests.Session()
    uploaded_files = _upload_pdfs(session, base_url, pdf_paths, upload_timeout_seconds)
    existing_downloads = _existing_downloads(uploaded_files)
    run_payload = _start_run(session, base_url, uploaded_files, _build_request(request, uploaded_files))
    final_payload = _wait_for_run(session, base_url, run_payload["run_id"], timeout_seconds, poll_seconds)
    _assert_parse_documents_called(final_payload, uploaded_files)
    downloads = _copy_markdown_outputs(uploaded_files, download_dir, existing_downloads)

    print("\ndownloaded markdown files:")
    for path in downloads:
        print(path)


def _pdf_paths(pdf_dir: Path) -> list[Path]:
    return sorted(pdf_dir.glob("*.pdf"))


def _upload_pdfs(
    session: requests.Session,
    base_url: str,
    pdf_paths: list[Path],
    upload_timeout_seconds: float,
) -> list[dict[str, Any]]:
    with ExitStack() as stack:
        files = []
        for pdf_path in pdf_paths:
            mime_type = mimetypes.guess_type(pdf_path.name)[0] or "application/pdf"
            handle = stack.enter_context(pdf_path.open("rb"))
            files.append(("files", (pdf_path.name, handle, mime_type)))
    response = session.post(
        _url(base_url, "/upload"),
        files=files,
        timeout=upload_timeout_seconds,
    )
    response.raise_for_status()
    uploaded_files = response.json()["files"]
    assert len(uploaded_files) == len(pdf_paths)
    assert sorted(file_info["size"] for file_info in uploaded_files) == sorted(
        pdf_path.stat().st_size for pdf_path in pdf_paths
    )
    for file_info in uploaded_files:
        print(f"uploaded: {file_info['file_path']}")
    return uploaded_files


def _build_request(request: str, uploaded_files: list[dict[str, Any]]) -> str:
    lines = [
        request,
        "",
        "必须只调用一次 parse_documents；args.file_paths 必须严格使用下面清单里的路径并保持顺序。",
        "全部完成后，只列出生成的文件路径，不要只总结 PDF 内容。",
        "",
        "PDF 清单：",
    ]
    for index, file_info in enumerate(uploaded_files, 1):
        lines.append(f"{index}. file_path={file_info['file_path']}")
    return "\n".join(lines)


def _start_run(
    session: requests.Session,
    base_url: str,
    uploaded_files: list[dict[str, Any]],
    request: str,
) -> dict[str, Any]:
    response = session.post(
        _url(base_url, "/runs"),
        json={
            "session_id": None,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": request}]
                    + [{"type": "artifact", "path": file_info["file_path"]} for file_info in uploaded_files],
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
) -> dict[str, Any]:
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
            assert run["reply"] or last_latest_text, "model returned an empty final reply"
            return payload
        if run["status"] == "failed":
            raise AssertionError(f"run failed: {run.get('error')}")

        time.sleep(poll_seconds)

    raise AssertionError(f"run {run_id} did not finish within {timeout_seconds} seconds")


def _assert_parse_documents_called(payload: dict[str, Any], uploaded_files: list[dict[str, Any]]) -> None:
    calls = [
        event.get("payload", {})
        for event in payload.get("events", [])
        if event.get("type") == "tool_call" and event.get("payload", {}).get("name") == "parse_documents"
    ]
    if len(calls) != 1:
        raise AssertionError(f"expected exactly one parse_documents call, got {len(calls)}")
    called_paths = calls[0].get("args", {}).get("file_paths")
    expected_paths = [file_info["file_path"] for file_info in uploaded_files]
    if called_paths != expected_paths:
        raise AssertionError(f"parse_documents args.file_paths mismatch: {called_paths!r} != {expected_paths!r}")


def _copy_markdown_outputs(
    uploaded_files: list[dict[str, Any]],
    download_dir: Path,
    existing_downloads: dict[str, set[Path]],
) -> list[Path]:
    download_dir.mkdir(parents=True, exist_ok=True)
    downloads = []
    for file_info in uploaded_files:
        stem = _uploaded_stem(file_info["file_path"])
        pattern = f"{stem}_*.md"
        after = set(_downloads_root().glob(pattern))
        new_files = sorted(after - existing_downloads.get(stem, set()))
        if len(new_files) != 1:
            raise AssertionError(f"Expected exactly one markdown output for {stem}, got {new_files}")
        source = new_files[0]
        target = download_dir / source.name
        shutil.copy2(source, target)
        assert target.stat().st_size > 0
        downloads.append(target)
    return downloads


def _existing_downloads(uploaded_files: list[dict[str, Any]]) -> dict[str, set[Path]]:
    existing: dict[str, set[Path]] = {}
    downloads_root = _downloads_root()
    for file_info in uploaded_files:
        stem = _uploaded_stem(file_info["file_path"])
        existing[stem] = set(downloads_root.glob(f"{stem}_*.md"))
    return existing


def _downloads_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "artifacts" / "downloads"


def _uploaded_stem(artifact_path: str) -> str:
    virtual_path = PurePosixPath(artifact_path)
    return Path(virtual_path.name).stem


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
